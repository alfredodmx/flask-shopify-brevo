import os
import json
import logging
from typing import List, Tuple, Optional

import requests
from flask import Flask, request, jsonify

# ──────────────────────────────────────────────────────────────────────────────
# Config & Logging
# ──────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("app")

BREVO_API_KEY = os.getenv("BREVO_API_KEY")  # HTTP API KEY (no SMTP)
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
SHOPIFY_STORE = os.getenv("SHOPIFY_STORE", "uaua8v-s7.myshopify.com")

# Coma-separados: ej. "a@x.cl,b@y.com"
NOTIFY_EMAILS = [e.strip() for e in os.getenv("NOTIFY_EMAILS", "").split(",") if e.strip()]

if not BREVO_API_KEY or not SHOPIFY_ACCESS_TOKEN:
    log.critical("Faltan ENV: BREVO_API_KEY y/o SHOPIFY_ACCESS_TOKEN")
    raise SystemExit(1)

BREVO_CONTACTS_URL = "https://api.brevo.com/v3/contacts"
BREVO_GET_CONTACT_URL = "https://api.brevo.com/v3/contacts/{email}"
BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"

SHOPIFY_GRAPHQL_URL = f"https://{SHOPIFY_STORE}/admin/api/2023-10/graphql.json"

# ──────────────────────────────────────────────────────────────────────────────
# Utilidades Shopify
# ──────────────────────────────────────────────────────────────────────────────
def _shopify_headers() -> dict:
    return {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }

def shopify_graphql(query: str) -> dict:
    r = requests.post(SHOPIFY_GRAPHQL_URL, headers=_shopify_headers(), json={"query": query}, timeout=20, verify=False)
    r.raise_for_status()
    return r.json()

def get_public_file_url(gid: Optional[str]) -> Optional[str]:
    """ Intenta resolver GID como MediaImage y luego como GenericFile. """
    if not gid:
        return None

    # MediaImage
    q1 = f"""
    query {{
      node(id: "{gid}") {{
        ... on MediaImage {{
          image {{ url }}
        }}
      }}
    }}"""
    try:
        data = shopify_graphql(q1)
        url = (((data.get("data") or {}).get("node") or {}).get("image") or {}).get("url")
        if url:
            return url
    except Exception as e:
        log.warning("MediaImage error %s: %s", gid, e)

    # GenericFile
    q2 = f"""
    query {{
      node(id: "{gid}") {{
        ... on GenericFile {{
          url
        }}
      }}
    }}"""
    try:
        data = shopify_graphql(q2)
        url = ((data.get("data") or {}).get("node") or {}).get("url")
        return url
    except Exception as e:
        log.warning("GenericFile error %s: %s", gid, e)
        return None

def get_customer_metafields(customer_id: int) -> Tuple[str, str, str, str, str, str, str]:
    url = f"https://{SHOPIFY_STORE}/admin/api/2023-10/customers/{customer_id}/metafields.json"
    try:
        r = requests.get(url, headers=_shopify_headers(), timeout=20, verify=False)
        r.raise_for_status()
        mfs = r.json().get("metafields", []) or []

        def mv(key: str, default=""):
            return next((m["value"] for m in mfs if m.get("key") == key), default)

        modelo = mv("modelo", "Sin modelo")
        precio = mv("precio", "Sin precio")
        describe = mv("describe_lo_que_quieres", "Sin descripción")
        plano_gid = mv("tengo_un_plano", None)
        plano_url = get_public_file_url(plano_gid) if plano_gid else "Sin plano"
        direccion = mv("tu_direccin_actual", "Sin dirección")
        presupuesto = mv("indica_tu_presupuesto", "Sin presupuesto")
        persona = mv("tipo_de_persona", "Persona natural")

        return (modelo, precio, describe, plano_url, direccion, presupuesto, persona)
    except Exception as e:
        log.error("Metafields error: %s", e)
        return ("Error",)*7

# ──────────────────────────────────────────────────────────────────────────────
# Brevo API (HTTP) – SIEMPRE (Render bloquea SMTP)
# ──────────────────────────────────────────────────────────────────────────────
def brevo_headers() -> dict:
    return {
        "api-key": BREVO_API_KEY,
        "accept": "application/json",
        "content-type": "application/json"
    }

def send_brevo_email(to_emails: List[str], subject: str, html: str,
                     sender_name: str = "Leads", sender_email: str = "info@espaciocontainerhouse.cl",
                     tags: Optional[List[str]] = None) -> dict:
    """ Envío por API HTTP de Brevo (no SMTP). """
    payload = {
        "sender": {"name": sender_name, "email": sender_email},
        "to": [{"email": e} for e in to_emails if e],
        "subject": subject,
        "htmlContent": html
    }
    if tags:
        payload["tags"] = tags

    log.info("➡️ Brevo API send → to=%s, subject=%s", to_emails, subject)
    r = requests.post(BREVO_SEND_URL, headers=brevo_headers(), json=payload, timeout=30)
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text}

    log.info("⬅️ Brevo API status=%s, body=%s", r.status_code, body)
    r.raise_for_status()
    return body

def ensure_brevo_contact(email: str, attributes: dict) -> None:
    """ Crea o actualiza contacto en Brevo. """
    g = requests.get(BREVO_GET_CONTACT_URL.format(email=email), headers=brevo_headers(), timeout=20)
    if g.status_code == 200:
        log.info("Contacto existe → update %s", email)
        u = requests.put(BREVO_GET_CONTACT_URL.format(email=email),
                         headers=brevo_headers(),
                         json={"email": email, "attributes": attributes},
                         timeout=20)
        if u.status_code not in (200, 204):
            log.warning("Update contacto fallo %s: %s", u.status_code, u.text)
    elif g.status_code == 404:
        log.info("Contacto NO existe → create %s", email)
        c = requests.post(BREVO_CONTACTS_URL,
                          headers=brevo_headers(),
                          json={"email": email, "attributes": attributes},
                          timeout=20)
        if c.status_code not in (200, 201):
            log.warning("Create contacto fallo %s: %s", c.status_code, c.text)
    else:
        log.warning("GET contacto %s → %s: %s", email, g.status_code, g.text)

# ──────────────────────────────────────────────────────────────────────────────
# Rutas
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return "OK", 200

@app.post("/webhook/shopify")
def webhook_shopify():
    raw = request.data.decode("utf-8", "ignore")
    log.info("📩 Shopify RAW: %s", raw)

    data = request.get_json(silent=True) or {}
    log.info("Shopify JSON: %s", json.dumps(data, ensure_ascii=False))

    customer_id = data.get("id")
    email = data.get("email")
    first_name = data.get("first_name", "") or ""
    last_name = data.get("last_name", "") or ""
    phone = data.get("phone", "") or ""

    if not (customer_id and email):
        return jsonify({"ok": False, "error": "Falta email o customer_id"}), 400

    # Metacampos
    modelo, precio, desc, plano, direccion, presupuesto, persona = get_customer_metafields(customer_id)
    attrs = {
        "NOMBRE": first_name,
        "APELLIDOS": last_name,
        "TELEFONO_WHATSAPP": phone,
        "WHATSAPP": phone,
        "SMS": phone,
        "LANDLINE_NUMBER": phone,
        "MODELO_CABANA": modelo,
        "PRECIO_CABANA": precio,
        "DESCRIPCION_CLIENTE": desc,
        "PLANO_CLIENTE": plano,
        "DIRECCION_CLIENTE": direccion,
        "PRESUPUESTO_CLIENTE": presupuesto,
        "TIPO_DE_PERSONA": persona
    }
    # Contacto en Brevo
    try:
        ensure_brevo_contact(email, attrs)
    except Exception as e:
        log.warning("No se pudo sincronizar contacto Brevo: %s", e)

    # Notificación por correo (multi-destinatario)
    to_list = [email] + NOTIFY_EMAILS  # incluye al cliente y a tu lista interna
    subject = f"Nuevo registro Shopify: {first_name or ''} {last_name or ''}".strip()
    html = f"""
      <h2>Nuevo cliente</h2>
      <ul>
        <li><b>Email:</b> {email}</li>
        <li><b>Nombre:</b> {first_name} {last_name}</li>
        <li><b>Teléfono:</b> {phone}</li>
        <li><b>Modelo:</b> {modelo}</li>
        <li><b>Precio:</b> {precio}</li>
        <li><b>Descripción:</b> {desc}</li>
        <li><b>Plano (URL):</b> {plano}</li>
        <li><b>Dirección:</b> {direccion}</li>
        <li><b>Presupuesto:</b> {presupuesto}</li>
        <li><b>Tipo de persona:</b> {persona}</li>
      </ul>
    """
    try:
        resp = send_brevo_email(to_list, subject, html, tags=["shopify-webhook"])
        return jsonify({"ok": True, "brevo": resp}), 201
    except Exception as e:
        log.error("Error enviando notificación: %s", e)
        return jsonify({"ok": False, "error": "brevo_send_failed"}), 500

@app.post("/webhook/brevo-events")
def webhook_brevo_events():
    # Configura este endpoint en Brevo Webhooks → Transactional → “Events”
    # para ver delivered/bounce/open/click en tiempo real.
    payload = request.get_json(silent=True) or {}
    log.info("📩 Brevo webhook payload: %s", payload)
    return jsonify({"ok": True}), 200

@app.post("/debug/alert")
def debug_alert():
    """ Dispara un correo de prueba por API a los NOTIFY_EMAILS. """
    to_list = NOTIFY_EMAILS or []
    if not to_list:
        return jsonify({"ok": False, "error": "Define NOTIFY_EMAILS en ENV"}), 400
    try:
        resp = send_brevo_email(
            to_list=to_list,
            subject="🔔 Alerta de prueba (API/Brevo/Render)",
            html="<p>Funciona por API (no SMTP) 💪</p>",
            tags=["render-debug"]
        )
        return jsonify({"ok": True, "brevo": resp, "msg": "Prueba de alerta enviada"}), 200
    except Exception as e:
        log.error("debug_alert error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500

# ──────────────────────────────────────────────────────────────────────────────
# Run (Render usa gunicorn, esto es por si corres local)
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
