# app.py
from flask import Flask, request, jsonify
import requests
import json
import os
import logging
import smtplib
import ssl
from email.message import EmailMessage
from time import sleep

app = Flask(__name__)

# -------------------------------------------------------------------
# Configuración y utilidades
# -------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BREVO_API_KEY = os.getenv("BREVO_API_KEY", "").strip()
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN", "").strip()
SHOPIFY_STORE = os.getenv("SHOPIFY_STORE", "uaua8v-s7.myshopify.com").strip()

ALERT_PROVIDER = os.getenv("ALERT_PROVIDER", "smtp").strip().lower()  # smtp | brevo
ALERT_FROM_EMAIL = os.getenv("ALERT_FROM_EMAIL", "info@espaciocontainerhouse.cl").strip()
ALERT_FROM_NAME = os.getenv("ALERT_FROM_NAME", "Leads").strip()
ALERT_TO = [x.strip() for x in os.getenv("ALERT_TO", "").split(",") if x.strip()]

# SMTP (Zoho u otro)
ALERT_SMTP_HOST = os.getenv("ALERT_SMTP_HOST", "").strip()             # ej: smtp.zoho.com
ALERT_SMTP_PORT = int(os.getenv("ALERT_SMTP_PORT", "587").strip())     # ej: 587
ALERT_SMTP_USER = os.getenv("ALERT_SMTP_USER", "").strip()             # ej: info@espaciocontainerhouse.cl
ALERT_SMTP_PASS = os.getenv("ALERT_SMTP_PASS", "").strip()             # app password

# Flags
ENABLE_DEBUG_ROUTES = os.getenv("ENABLE_DEBUG_ROUTES", "true").lower().strip() == "true"

# Endpoints externos
BREVO_CONTACTS_URL = "https://api.brevo.com/v3/contacts"
BREVO_CONTACT_URL_BY_EMAIL = "https://api.brevo.com/v3/contacts/{email}"
SHOPIFY_GRAPHQL_URL = f"https://{SHOPIFY_STORE}/admin/api/2023-10/graphql.json"
SHOPIFY_REST_METAFIELDS = f"https://{SHOPIFY_STORE}/admin/api/2023-10/customers/{{customer_id}}/metafields.json"

# -------------------------------------------------------------------
# Validaciones suaves (no detenemos el servidor)
# -------------------------------------------------------------------
if not SHOPIFY_ACCESS_TOKEN:
    logging.warning("⚠️ SHOPIFY_ACCESS_TOKEN no configurado. Funciones Shopify fallarán.")
if not SHOPIFY_STORE:
    logging.warning("⚠️ SHOPIFY_STORE no configurado. Usa dominio correcto de tu tienda.")
if not BREVO_API_KEY:
    logging.warning("⚠️ BREVO_API_KEY no configurado. Funciones Brevo (contactos/API) pueden fallar.")
if ALERT_PROVIDER == "smtp":
    for k, v in {
        "ALERT_SMTP_HOST": ALERT_SMTP_HOST,
        "ALERT_SMTP_USER": ALERT_SMTP_USER,
        "ALERT_SMTP_PASS": ALERT_SMTP_PASS,
    }.items():
        if not v:
            logging.warning(f"⚠️ {k} sin valor; envío SMTP puede fallar.")
if not ALERT_TO:
    logging.warning("⚠️ ALERT_TO vacío. No habrá destinatarios para notificaciones.")

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------
def _shopify_headers():
    return {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }

def _brevo_headers():
    return {
        "api-key": BREVO_API_KEY,
        "accept": "application/json",
        "content-type": "application/json"
    }

def get_public_file_url(gid: str):
    """
    Intenta resolver GID como MediaImage y luego como GenericFile para sacar una URL pública.
    """
    if not gid:
        return None

    # 1) MediaImage
    query_image = {
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
        r = requests.post(SHOPIFY_GRAPHQL_URL, headers=_shopify_headers(), json=query_image, timeout=20)
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
        logging.warning(f"⚠️ MediaImage GID {gid}: {e}")

    # 2) GenericFile
    query_file = {
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
        r = requests.post(SHOPIFY_GRAPHQL_URL, headers=_shopify_headers(), json=query_file, timeout=20)
        r.raise_for_status()
        data = r.json()
        url = data.get("data", {}).get("node", {}).get("url")
        if url:
            return url
        else:
            logging.info(f"ℹ️ GID {gid} no resolvió a URL pública (GenericFile). Respuesta: {data}")
    except Exception as e:
        logging.warning(f"⚠️ GenericFile GID {gid}: {e}")

    return None

def get_customer_metafields(customer_id: int):
    """
    Lee metafields del cliente en Shopify y devuelve tuple normalizada.
    Keys esperadas: modelo, precio, describe_lo_que_quieres, tengo_un_plano (GID),
                    tu_direccin_actual, indica_tu_presupuesto, tipo_de_persona
    """
    url = SHOPIFY_REST_METAFIELDS.format(customer_id=customer_id)
    try:
        r = requests.get(url, headers=_shopify_headers(), timeout=20)
        r.raise_for_status()
        metafields = r.json().get("metafields", [])
        def val(key, default=""):
            for m in metafields:
                if m.get("key") == key:
                    return m.get("value", default)
            return default

        modelo = val("modelo", "Sin modelo")
        precio = val("precio", "Sin precio")
        describe = val("describe_lo_que_quieres", "Sin descripción")
        plano_gid = val("tengo_un_plano", "")
        direccion = val("tu_direccin_actual", "Sin dirección")
        presupuesto = val("indica_tu_presupuesto", "Sin presupuesto")
        persona = val("tipo_de_persona", "Sin persona")

        plano_url = get_public_file_url(plano_gid) if plano_gid else "Sin plano"
        return (modelo, precio, describe, plano_url, direccion, presupuesto, persona)

    except Exception as e:
        logging.error(f"❌ Error obteniendo metacampos de Shopify: {e}")
        # devolvemos placeholders
        return ("Error", "Error", "Error", "Error", "Error", "Error", "Error")

def brevo_upsert_contact(email, first_name, last_name, phone, meta):
    """
    Crea o actualiza contacto en Brevo con attributes desde meta.
    """
    if not BREVO_API_KEY:
        logging.warning("⚠️ BREVO_API_KEY ausente; saltando upsert de contacto.")
        return {"ok": False, "skipped": True, "reason": "no_api_key"}

    # ¿Existe?
    try:
        get_r = requests.get(
            BREVO_CONTACT_URL_BY_EMAIL.format(email=email),
            headers=_brevo_headers(),
            timeout=20
        )
    except Exception as e:
        return {"ok": False, "error": f"Error consultando contacto: {e}"}

    attrs = {
        "NOMBRE": first_name or "",
        "APELLIDOS": last_name or "",
        "TELEFONO_WHATSAPP": phone or "",
        "WHATSAPP": phone or "",
        "SMS": phone or "",
        "LANDLINE_NUMBER": phone or "",
        "MODELO_CABANA": meta.get("modelo", ""),
        "PRECIO_CABANA": meta.get("precio", ""),
        "DESCRIPCION_CLIENTE": meta.get("describe", ""),
        "PLANO_CLIENTE": meta.get("plano_url", ""),
        "DIRECCION_CLIENTE": meta.get("direccion", ""),
        "PRESUPUESTO_CLIENTE": meta.get("presupuesto", ""),
        "TIPO_DE_PERSONA": meta.get("persona", "")
    }

    if get_r.status_code == 200:
        logging.info(f"⚠️ Contacto {email} existe. Actualizando…")
        try:
            put_r = requests.put(
                BREVO_CONTACT_URL_BY_EMAIL.format(email=email),
                headers=_brevo_headers(),
                json={"email": email, "attributes": attrs},
                timeout=20
            )
            if 200 <= put_r.status_code < 300:
                return {"ok": True, "action": "updated"}
            return {"ok": False, "error": f"PUT {put_r.status_code}: {put_r.text}"}
        except Exception as e:
            return {"ok": False, "error": f"Error actualizando: {e}"}

    elif get_r.status_code == 404:
        logging.info(f"✅ Contacto {email} no existe. Creando…")
        try:
            post_r = requests.post(
                BREVO_CONTACTS_URL,
                headers=_brevo_headers(),
                json={"email": email, "attributes": attrs},
                timeout=20
            )
            if post_r.status_code in (200, 201):
                return {"ok": True, "action": "created"}
            return {"ok": False, "error": f"POST {post_r.status_code}: {post_r.text}"}
        except Exception as e:
            return {"ok": False, "error": f"Error creando: {e}"}
    else:
        return {"ok": False, "error": f"GET {get_r.status_code}: {get_r.text}"}

def _send_via_smtp(subject: str, html: str, to_list: list, retries: int = 2):
    """
    Envía correo vía SMTP (TLS). Reintentos simples.
    """
    if not ALERT_SMTP_HOST or not ALERT_SMTP_USER or not ALERT_SMTP_PASS:
        return {"ok": False, "error": "smtp_config_missing"}

    msg = EmailMessage()
    msg["From"] = f"{ALERT_FROM_NAME} <{ALERT_FROM_EMAIL}>"
    msg["To"] = ", ".join(to_list)
    msg["Subject"] = subject
    msg.set_content("Versión solo texto")
    msg.add_alternative(html, subtype="html")

    for i in range(retries + 1):
        try:
            context = ssl.create_default_context()
            with smtplib.SMTP(ALERT_SMTP_HOST, ALERT_SMTP_PORT, timeout=20) as server:
                server.starttls(context=context)
                server.login(ALERT_SMTP_USER, ALERT_SMTP_PASS)
                server.send_message(msg)
            logging.info(f"📧 SMTP enviado a {to_list}")
            return {"ok": True, "provider": "smtp"}
        except Exception as e:
            logging.warning(f"SMTP intento {i+1} fallo: {e}")
            if i < retries:
                sleep(1.5)
    return {"ok": False, "error": "smtp_send_failed"}

def _send_via_brevo_api(subject: str, html: str, to_list: list, tags=None):
    """
    Envía correo vía Brevo API /v3/smtp/email a múltiples destinatarios.
    """
    if not BREVO_API_KEY:
        return {"ok": False, "error": "brevo_api_key_missing"}

    payload = {
        "sender": {"name": ALERT_FROM_NAME, "email": ALERT_FROM_EMAIL},
        "to": [{"email": x} for x in to_list],
        "subject": subject,
        "htmlContent": html
    }
    if tags:
        payload["tags"] = tags

    try:
        r = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers=_brevo_headers(),
            json=payload,
            timeout=25
        )
        if 200 <= r.status_code < 300:
            mid = r.json().get("messageId")
            logging.info(f"📧 Brevo API OK → {to_list}, mid={mid}")
            return {"ok": True, "provider": "brevo", "messageId": mid}
        return {"ok": False, "error": f"brevo_api_{r.status_code}", "body": r.text}
    except Exception as e:
        return {"ok": False, "error": f"brevo_api_exception: {e}"}

def send_alert(subject: str, html: str, to_list: list, tags=None):
    """
    Enrouta el envío según ALERT_PROVIDER. Si falla el proveedor elegido,
    intenta con el otro como fallback.
    """
    if not to_list:
        return {"ok": False, "error": "no_recipients"}

    provider = ALERT_PROVIDER
    logging.info(f"🔔 Enviando alerta via {provider} a {to_list}")

    if provider == "smtp":
        res = _send_via_smtp(subject, html, to_list)
        if res.get("ok"):
            return res
        # fallback a Brevo API si hay API key
        if BREVO_API_KEY:
            logging.info("↪️ Fallback a Brevo API…")
            return _send_via_brevo_api(subject, html, to_list, tags=tags)
        return res

    # provider == "brevo"
    res = _send_via_brevo_api(subject, html, to_list, tags=tags)
    if res.get("ok"):
        return res
    # fallback a SMTP si está configurado
    if ALERT_SMTP_HOST and ALERT_SMTP_USER and ALERT_SMTP_PASS:
        logging.info("↪️ Fallback a SMTP…")
        return _send_via_smtp(subject, html, to_list)
    return res

# -------------------------------------------------------------------
# Rutas
# -------------------------------------------------------------------
@app.get("/")
def root():
    return "OK", 200

@app.get("/debug/env")
def debug_env():
    if not ENABLE_DEBUG_ROUTES:
        return jsonify({"ok": False, "error": "debug_disabled"}), 403
    return jsonify({
        "ok": True,
        "alert_provider": ALERT_PROVIDER,
        "from": {"email": ALERT_FROM_EMAIL, "name": ALERT_FROM_NAME},
        "to_count": len(ALERT_TO),
        "smtp_ready": bool(ALERT_SMTP_HOST and ALERT_SMTP_USER and ALERT_SMTP_PASS),
        "brevo_key_loaded": bool(BREVO_API_KEY),
        "shopify_store": SHOPIFY_STORE,
        "shopify_token_loaded": bool(SHOPIFY_ACCESS_TOKEN),
    })

@app.post("/debug/alert")
def debug_alert():
    """
    Envía una alerta de prueba a ALERT_TO usando el proveedor configurado.
    """
    if not ENABLE_DEBUG_ROUTES:
        return jsonify({"ok": False, "error": "debug_disabled"}), 403

    to = ALERT_TO or []
    if not to:
        return jsonify({"ok": False, "error": "no_recipients_in_env"}), 400

    subject = "🔔 Alerta de prueba (debug)"
    html = "<p>Prueba de notificación desde /debug/alert</p>"
    res = send_alert(subject, html, to_list=to, tags=["debug"])
    return jsonify({"ok": res.get("ok", False), "provider": res.get("provider"), "detail": res})

@app.post("/webhook/brevo-events")
def brevo_events():
    """
    Solo para ver que el webhook de Brevo llega. No bloquea nada.
    """
    try:
        payload = request.get_json(force=True, silent=True) or {}
        logging.info(f"📩 Brevo webhook payload: {payload}")
        return "ok", 200
    except Exception as e:
        logging.error(f"brevo webhook error: {e}")
        return "bad", 400

@app.post("/webhook/shopify")
def webhook_shopify():
    """
    Webhook de Shopify: procesa el cliente, upsert en Brevo y
    envía notificación a 3+ destinatarios (ALERT_TO).
    """
    raw = request.data.decode("utf-8", errors="ignore")
    logging.info(f"📩 Webhook Shopify RAW: {raw}")

    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({"ok": False, "error": "invalid_json"}), 400

    # Campos básicos
    customer_id = data.get("id")
    email = data.get("email") or ""
    first_name = data.get("first_name") or ""
    last_name = data.get("last_name") or ""
    phone = data.get("phone") or ""

    if not customer_id or not email:
        return jsonify({"ok": False, "error": "missing_email_or_customer_id"}), 400

    # Metafields
    modelo, precio, describe, plano_url, direccion, presupuesto, persona = get_customer_metafields(customer_id)
    meta = {
        "modelo": modelo,
        "precio": precio,
        "describe": describe,
        "plano_url": plano_url,
        "direccion": direccion,
        "presupuesto": presupuesto,
        "persona": persona
    }
    logging.info(f"ℹ️ Metacampos: {meta}")

    # Upsert en Brevo (contactos)
    up = brevo_upsert_contact(email, first_name, last_name, phone, meta)
    logging.info(f"ℹ️ Brevo upsert contacto → {up}")

    # --------- ALERTA A MÚLTIPLES DESTINATARIOS ---------
    if ALERT_TO:
        subject = f"🧾 Nuevo cliente Shopify: {first_name} {last_name} ({email})"
        html = f"""
        <h3>Nuevo cliente recibido</h3>
        <ul>
          <li><b>Email:</b> {email}</li>
          <li><b>Nombre:</b> {first_name} {last_name}</li>
          <li><b>Teléfono:</b> {phone}</li>
        </ul>
        <h4>Metadatos</h4>
        <ul>
          <li><b>Modelo:</b> {modelo}</li>
          <li><b>Precio:</b> {precio}</li>
          <li><b>Descripción:</b> {describe}</li>
          <li><b>Plano (URL):</b> {plano_url}</li>
          <li><b>Dirección:</b> {direccion}</li>
          <li><b>Presupuesto:</b> {presupuesto}</li>
          <li><b>Tipo Persona:</b> {persona}</li>
        </ul>
        """

        alert_res = send_alert(subject, html, to_list=ALERT_TO, tags=["shopify-webhook"])
        logging.info(f"🔔 Alerta enviada → {alert_res}")
    else:
        alert_res = {"ok": False, "error": "no_recipients_in_env"}

    return jsonify({
        "ok": True,
        "brevo_upsert": up,
        "alert": alert_res
    }), 201

# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
