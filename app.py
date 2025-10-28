import os
import json
import logging
from typing import List, Tuple, Optional, Dict

import requests
from flask import Flask, request, jsonify

# -----------------------------------------------------------------------------
# Config & Logger
# -----------------------------------------------------------------------------
app = Flask(__name__)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ENV (Render -> Dashboard -> Environment)
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "").strip()
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN", "").strip()
SHOPIFY_STORE = os.getenv("SHOPIFY_STORE", "").strip() or "uaua8v-s7.myshopify.com"
BREVO_SENDER = os.getenv("BREVO_SENDER", "").strip() or "info@espaciocontainerhouse.cl"

# Lista de correos separados por coma
NOTIFY_EMAILS = [
    e.strip() for e in os.getenv("NOTIFY_EMAILS", "").split(",") if e.strip()
]

# Shopify endpoints
SHOPIFY_GRAPHQL_URL = f"https://{SHOPIFY_STORE}/admin/api/2023-10/graphql.json"
SHOPIFY_API_PREFIX = f"https://{SHOPIFY_STORE}/admin/api/2023-10"

# Brevo endpoints
BREVO_EMAIL_URL = "https://api.brevo.com/v3/smtp/email"
BREVO_ACCOUNT_URL = "https://api.brevo.com/v3/account"
BREVO_CONTACT_URL = "https://api.brevo.com/v3/contacts"
BREVO_GET_CONTACT_URL = "https://api.brevo.com/v3/contacts/{email}"

# Pequeña ayuda para evitar errores comunes de configuración
if not BREVO_API_KEY:
    log.warning("BREVO_API_KEY no está definido en ENV.")
if not SHOPIFY_ACCESS_TOKEN:
    log.warning("SHOPIFY_ACCESS_TOKEN no está definido en ENV.")
if not NOTIFY_EMAILS:
    log.info("NOTIFY_EMAILS está vacío; puedes pasar ?to= en /debug/alert para probar.")

# -----------------------------------------------------------------------------
# Utils
# -----------------------------------------------------------------------------
def _mask_secret(value: str, keep: int = 4) -> str:
    if not value:
        return "∅"
    if len(value) <= keep:
        return "*" * len(value)
    return value[:keep] + "…" + "*" * (len(value) - keep - 1)

def _shopify_headers() -> Dict[str, str]:
    return {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json",
    }

def _brevo_headers(json_ct: bool = True) -> Dict[str, str]:
    h = {
        "api-key": BREVO_API_KEY,
        "accept": "application/json",
    }
    if json_ct:
        h["content-type"] = "application/json"
    return h

# -----------------------------------------------------------------------------
# Brevo: envío de emails por API
# -----------------------------------------------------------------------------
def send_brevo_email(
    to_emails: List[str],
    subject: str,
    html: str,
    tags: Optional[List[str]] = None,
    sender_name: str = "Leads",
    sender_email: Optional[str] = None,
    timeout: int = 30,
) -> Tuple[int, dict]:
    """
    Envía correo vía Brevo API.
    Devuelve (status_code, body_dict_or_raw).
    """
    if not to_emails:
        return 400, {"error": "to_emails vacío"}
    if not BREVO_API_KEY:
        return 400, {"error": "BREVO_API_KEY no configurado"}

    payload = {
        "sender": {"name": sender_name, "email": sender_email or BREVO_SENDER},
        "to": [{"email": e} for e in to_emails],
        "subject": subject,
        "htmlContent": html,
    }
    if tags:
        payload["tags"] = tags

    try:
        r = requests.post(BREVO_EMAIL_URL, headers=_brevo_headers(), json=payload, timeout=timeout)
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text}
        log.info("Brevo send status=%s body=%s", r.status_code, str(body)[:500])
        return r.status_code, body
    except Exception as e:
        log.error("Brevo send error: %s", e, exc_info=True)
        return 500, {"error": str(e)}

# -----------------------------------------------------------------------------
# Shopify: obtener URL pública de archivo (MediaImage o GenericFile)
# -----------------------------------------------------------------------------
def get_public_file_url(gid: Optional[str]) -> Optional[str]:
    if not gid:
        return None

    # 1) Intentar como MediaImage
    q_image = {
        "query": f"""
        query {{
          node(id: "{gid}") {{
            ... on MediaImage {{
              image {{ url }}
            }}
          }}
        }}
        """
    }
    try:
        r = requests.post(SHOPIFY_GRAPHQL_URL, headers=_shopify_headers(), json=q_image, timeout=30)
        r.raise_for_status()
        data = r.json()
        url = (
            data.get("data", {})
                .get("node", {})
                .get("image", {})
                .get("url")
        )
        if url:
            return url
    except Exception as e:
        log.warning("GraphQL MediaImage error gid=%s: %s", gid, e)

    # 2) Intentar como GenericFile
    q_file = {
        "query": f"""
        query {{
          node(id: "{gid}") {{
            ... on GenericFile {{
              url
            }}
          }}
        }}
        """
    }
    try:
        r = requests.post(SHOPIFY_GRAPHQL_URL, headers=_shopify_headers(), json=q_file, timeout=30)
        r.raise_for_status()
        data = r.json()
        url = data.get("data", {}).get("node", {}).get("url")
        return url
    except Exception as e:
        log.warning("GraphQL GenericFile error gid=%s: %s", gid, e)

    return None

# -----------------------------------------------------------------------------
# Shopify: metacampos de un cliente
# -----------------------------------------------------------------------------
def get_customer_metafields(customer_id: int) -> Tuple[str, str, str, Optional[str], str, str, str]:
    """
    Devuelve:
      (modelo, precio, describe, plano_url, direccion, presupuesto, tipo_persona)
    """
    url = f"{SHOPIFY_API_PREFIX}/customers/{customer_id}/metafields.json"
    try:
        r = requests.get(url, headers=_shopify_headers(), timeout=30)
        r.raise_for_status()
        metafields = r.json().get("metafields", [])

        def val(key: str, default: str = "Sin dato") -> str:
            for m in metafields:
                if m.get("key") == key:
                    return str(m.get("value", default)).strip() or default
            return default

        modelo = val("modelo", "Sin modelo")
        precio = val("precio", "Sin precio")
        describe = val("describe_lo_que_quieres", "Sin descripción")
        plano_gid = val("tengo_un_plano", "")
        direccion = val("tu_direccin_actual", "Sin dirección")
        presupuesto = val("indica_tu_presupuesto", "Sin presupuesto")
        tipo_pers = val("tipo_de_persona", "Sin persona")

        plano_url = get_public_file_url(plano_gid) if plano_gid else None
        return modelo, precio, describe, (plano_url or "Sin plano"), direccion, presupuesto, tipo_pers

    except Exception as e:
        log.error("Error leyendo metacampos Shopify: %s", e, exc_info=True)
        return "Error", "Error", "Error", "Error", "Error", "Error", "Error"

# -----------------------------------------------------------------------------
# Brevo: crear/actualizar (upsert) contacto con atributos
# -----------------------------------------------------------------------------
def upsert_brevo_contact(
    email: str,
    attributes: Dict[str, str],
) -> Tuple[int, dict]:
    """
    Si existe, actualiza; si no, crea.
    """
    if not BREVO_API_KEY:
        return 400, {"error": "BREVO_API_KEY no configurado"}

    # ¿Existe?
    try:
        g = requests.get(BREVO_GET_CONTACT_URL.format(email=email), headers=_brevo_headers(False), timeout=20)
        exists = (g.status_code == 200)
    except Exception as e:
        return 500, {"error": f"Lookup contact error: {e}"}

    payload = {"email": email, "attributes": attributes}

    try:
        if exists:
            r = requests.put(BREVO_GET_CONTACT_URL.format(email=email), headers=_brevo_headers(), json=payload, timeout=30)
        else:
            r = requests.post(BREVO_CONTACT_URL, headers=_brevo_headers(), json=payload, timeout=30)

        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text}

        return r.status_code, body
    except Exception as e:
        return 500, {"error": str(e)}

# -----------------------------------------------------------------------------
# Rutas
# -----------------------------------------------------------------------------
@app.get("/")
def root():
    return "OK", 200

@app.get("/debug/env")
def debug_env():
    info = {
        "shopify_store": SHOPIFY_STORE or "∅",
        "notify_emails": NOTIFY_EMAILS,
        "notify_count": len(NOTIFY_EMAILS),
        "brevo_api_key_masked": _mask_secret(BREVO_API_KEY),
        "brevo_sender": BREVO_SENDER or "∅",
    }

    # Ping Brevo /account (opcional)
    try:
        acc = requests.get(BREVO_ACCOUNT_URL, headers=_brevo_headers(False), timeout=12)
        info["brevo_account_status"] = acc.status_code
        info["brevo_account_ok"] = (acc.status_code == 200)
        if acc.status_code != 200:
            info["brevo_account_body"] = acc.text[:400]
    except Exception as e:
        info["brevo_account_error"] = str(e)

    return jsonify(info), 200

@app.post("/debug/alert")
def debug_alert():
    """
    Envía un correo de prueba por Brevo API.
    Prioriza parámetros por query: ?to=a@x.com,b@y.com&subject=...&tags=t1,t2
    JSON opcional: {"to":["a@x.com"],"subject":"...","html":"<p>..</p>","tags":["t1"]}
    Si no hay 'to', usa NOTIFY_EMAILS.
    """
    js = request.get_json(silent=True) or {}

    # to
    qs_to = (request.args.get("to") or "").strip()
    if qs_to:
        to_emails = [e.strip() for e in qs_to.split(",") if e.strip()]
    else:
        to_emails = [e for e in js.get("to", []) if isinstance(e, str) and e.strip()]

    if not to_emails:
        to_emails = NOTIFY_EMAILS[:]

    if not to_emails:
        return jsonify({"ok": False, "error": "Define NOTIFY_EMAILS en ENV o pásame ?to=correo1,correo2"}), 400

    subject = request.args.get("subject") or js.get("subject") or "🔔 Prueba Render → Brevo (API)"
    html = request.args.get("html") or js.get("html") or "<p>Funciona por API (no SMTP) 💪</p>"

    tags_qs = (request.args.get("tags") or "")
    tags = [t.strip() for t in tags_qs.split(",") if t.strip()] if tags_qs else js.get("tags", [])
    if not tags:
        tags = ["render-debug"]

    status, body = send_brevo_email(
        to_emails=to_emails,
        subject=subject,
        html=html,
        tags=tags,
        sender_name="Leads",
        sender_email=BREVO_SENDER,
    )
    ok = 200 <= status < 300
    return jsonify({"ok": ok, "status": status, "to": to_emails, "brevo": body}), (200 if ok else status)

@app.post("/webhook/shopify")
def webhook_shopify():
    """
    Espera un JSON de Shopify Customer Create/Update.
    Lee metacampos, hace upsert en Brevo y notifica a NOTIFY_EMAILS.
    """
    raw = request.data.decode("utf-8", errors="ignore")
    log.info("📩 Shopify webhook RAW: %s", raw[:2000])

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON inválido"}), 400

    email = data.get("email")
    customer_id = data.get("id")
    first_name = data.get("first_name", "") or ""
    last_name = data.get("last_name", "") or ""
    phone = data.get("phone", "") or ""

    if not email or not customer_id:
        return jsonify({"error": "Falta email o id de cliente"}), 400

    # Metacampos
    modelo, precio, describe, plano_url, direccion, presupuesto, tipo_pers = get_customer_metafields(customer_id)
    log.info("Metacampos: modelo=%s precio=%s describe=%s plano=%s dir=%s pres=%s tipo=%s",
             modelo, precio, describe, plano_url, direccion, presupuesto, tipo_pers)

    # Upsert contacto Brevo
    attributes = {
        "NOMBRE": first_name,
        "APELLIDOS": last_name,
        "TELEFONO_WHATSAPP": phone,
        "WHATSAPP": phone,
        "SMS": phone,
        "LANDLINE_NUMBER": phone,
        "MODELO_CABANA": modelo,
        "PRECIO_CABANA": precio,
        "DESCRIPCION_CLIENTE": describe,
        "PLANO_CLIENTE": plano_url,
        "DIRECCION_CLIENTE": direccion,
        "PRESUPUESTO_CLIENTE": presupuesto,
        "TIPO_DE_PERSONA": tipo_pers,
    }
    st, body = upsert_brevo_contact(email, attributes)
    log.info("Upsert Brevo contact status=%s body=%s", st, str(body)[:500])

    # Notificar por correo a NOTIFY_EMAILS (si hay)
    if NOTIFY_EMAILS:
        html = f"""
        <h3>Nuevo/Actualizado cliente Shopify</h3>
        <ul>
          <li><b>Email:</b> {email}</li>
          <li><b>Nombre:</b> {first_name} {last_name}</li>
          <li><b>Teléfono:</b> {phone}</li>
          <li><b>Modelo:</b> {modelo}</li>
          <li><b>Precio:</b> {precio}</li>
          <li><b>Descripción:</b> {describe}</li>
          <li><b>Plano:</b> {plano_url}</li>
          <li><b>Dirección:</b> {direccion}</li>
          <li><b>Presupuesto:</b> {presupuesto}</li>
          <li><b>Tipo de persona:</b> {tipo_pers}</li>
        </ul>
        """
        send_brevo_email(
            to_emails=NOTIFY_EMAILS,
            subject="🔔 Shopify → Brevo: Cliente actualizado/creado",
            html=html,
            tags=["shopify-webhook", "render"],
            sender_name="Leads",
            sender_email=BREVO_SENDER,
        )

    code = 200 if 200 <= st < 300 else 202  # no romper el webhook si Brevo falla
    return jsonify({"ok": True, "brevo_status": st, "brevo_body": body}), code

@app.post("/webhook/brevo-events")
def webhook_brevo_events():
    """
    Webhook de Brevo (eventos de email). Solo loguea y devuelve 200.
    Configúralo en Brevo → 'New webhooks' → Events.
    """
    data = request.get_json(silent=True)
    log.info("📩 Brevo webhook event: %s", json.dumps(data, ensure_ascii=False)[:2000])
    return jsonify({"ok": True}), 200

# -----------------------------------------------------------------------------
# Main (para desarrollo local; en Render corre con gunicorn)
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
