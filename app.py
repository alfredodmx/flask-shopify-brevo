# app.py
# -*- coding: utf-8 -*-

import os
import json
import logging
from typing import List, Optional

import requests
from flask import Flask, request, jsonify

# -----------------------------------------------------------------------------
# Config básica de Flask + logs
# -----------------------------------------------------------------------------
app = Flask(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# -----------------------------------------------------------------------------
# Utilidades
# -----------------------------------------------------------------------------
def env(name: str, default: Optional[str] = None) -> Optional[str]:
    """Lee variables de entorno con valor por defecto."""
    return os.environ.get(name, default)

def split_recipients(csv_emails: str) -> List[str]:
    """Convierte 'a@b.com, c@d.com' → ['a@b.com', 'c@d.com'] (limpiando espacios)."""
    if not csv_emails:
        return []
    return [e.strip() for e in csv_emails.split(",") if e.strip()]

# -----------------------------------------------------------------------------
# Config leída desde ENV (usa los nombres que mostraste en Render)
# -----------------------------------------------------------------------------
ALERT_FROM_NAME  = env("ALERT_FROM_NAME", "Leads")
ALERT_FROM_EMAIL = env("ALERT_FROM_EMAIL") or env("ALERT_FROM")
ALERT_TO_CSV     = env("ALERT_TO", "")  # puede ser "a@x.com,b@y.com"
BREVO_API_KEY    = env("BREVO_API_KEY", "")

# Opcional: si más tarde quieres usar listas de Brevo
BREVO_LIST_ID    = env("BREVO_LIST_ID")

# -----------------------------------------------------------------------------
# Cliente Brevo (API v3)
# -----------------------------------------------------------------------------
BREVO_BASE = "https://api.brevo.com/v3"

def brevo_send_email(
    to_emails: List[str],
    subject: str,
    html: str,
    tags: Optional[List[str]] = None,
    from_email: Optional[str] = None,
    from_name: Optional[str] = None,
) -> dict:
    """
    Envía correo por Brevo /v3/smtp/email.
    Lanza excepción si la API responde != 2xx.
    """
    if not BREVO_API_KEY:
        raise RuntimeError("Falta BREVO_API_KEY en variables de entorno.")

    from_email = from_email or ALERT_FROM_EMAIL
    from_name  = from_name  or ALERT_FROM_NAME

    if not from_email:
        raise RuntimeError("Falta ALERT_FROM_EMAIL/ALERT_FROM en variables de entorno.")

    payload = {
        "sender": {"name": from_name, "email": from_email},
        "to": [{"email": e} for e in to_emails],
        "subject": subject,
        "htmlContent": html,
    }
    if tags:
        payload["tags"] = tags

    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "api-key": BREVO_API_KEY,
    }

    app.logger.info(f"➡️ Brevo API send: to={to_emails}, subject={subject}")
    r = requests.post(f"{BREVO_BASE}/smtp/email", headers=headers, json=payload, timeout=20)
    app.logger.info(f"⬅️ Brevo API status={r.status_code}, body={r.text[:500]}")
    r.raise_for_status()
    return r.json() if r.text else {}

def brevo_get_account() -> dict:
    headers = {"accept": "application/json", "api-key": BREVO_API_KEY}
    r = requests.get(f"{BREVO_BASE}/account", headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()

def brevo_get_events_by_email(email: str, limit: int = 20, offset: int = 0) -> dict:
    headers = {"accept": "application/json", "api-key": BREVO_API_KEY}
    params = {"email": email, "limit": limit, "offset": offset}
    r = requests.get(f"{BREVO_BASE}/smtp/emails", headers=headers, params=params, timeout=15)
    r.raise_for_status()
    # Nota: si no hay eventos, Brevo puede devolver {} (vacío)
    try:
        return r.json()
    except Exception:
        return {}

# -----------------------------------------------------------------------------
# Rutas
# -----------------------------------------------------------------------------
@app.route("/", methods=["GET"])
def root_ok():
    return jsonify(ok=True, service="flask-shopify-brevo", msg="UP")

# ---- Debug: ver variables mínimas (sin exponer secretos) --------------------
@app.route("/debug/brevo/env", methods=["GET"])
def debug_brevo_env():
    data = {
        "ALERT_FROM_EMAIL": bool(ALERT_FROM_EMAIL),
        "ALERT_FROM_NAME": ALERT_FROM_NAME,
        "ALERT_TO_count": len(split_recipients(ALERT_TO_CSV)),
        "BREVO_API_KEY_loaded": bool(BREVO_API_KEY),
        "BREVO_LIST_ID": BREVO_LIST_ID or None,
    }
    return jsonify(ok=True, env=data)

# ---- Debug: ver cuenta Brevo asociada a la API key --------------------------
@app.route("/debug/brevo/account", methods=["GET"])
def debug_brevo_account():
    try:
        acc = brevo_get_account()
        return jsonify(ok=True, account=acc)
    except Exception as e:
        app.logger.exception("Error consultando /v3/account")
        return jsonify(ok=False, error=str(e)), 500

# ---- Debug: enviar correo a ALERT_TO ----------------------------------------
@app.route("/debug/alert", methods=["POST"])
def debug_alert():
    recipients = split_recipients(ALERT_TO_CSV)
    if not recipients:
        return jsonify(ok=False, error="ALERT_TO vacío o inválido"), 400

    try:
        resp = brevo_send_email(
            recipients,
            subject="🔔 Alerta de prueba (Render/Debug)",
            html="<p>Hola 👋, esto es un test enviado desde el endpoint /debug/alert</p>",
            tags=["render-debug"],
        )
        return jsonify(ok=True, msg="Prueba de alerta enviada", brevo=resp), 200
    except Exception as e:
        app.logger.exception("Error enviando alerta de prueba")
        return jsonify(ok=False, error=str(e)), 500

# ---- Debug: consultar eventos por destinatario ------------------------------
@app.route("/debug/brevo/events", methods=["GET"])
def debug_brevo_events():
    email = request.args.get("email", "").strip()
    if not email:
        return jsonify(ok=False, error="Falta parámetro ?email="), 400
    try:
        events = brevo_get_events_by_email(email=email, limit=20, offset=0)
        return jsonify(ok=True, email=email, events=events), 200
    except Exception as e:
        app.logger.exception("Error consultando eventos")
        return jsonify(ok=False, error=str(e)), 500

# ---- Webhook Shopify (ejemplo mínimo) ---------------------------------------
@app.route("/webhook/shopify", methods=["POST"])
def webhook_shopify():
    """
    Recibe el webhook de customer/create (u otros) y dispara una alerta por Brevo.
    No valida HMAC aquí (puedes agregarlo si lo necesitas).
    """
    try:
        raw = request.get_data(as_text=True) or ""
        data = request.get_json(silent=True) or {}
        app.logger.info(f"📩 Webhook Shopify recibido (RAW) len={len(raw)}")
        app.logger.info(f"📩 Webhook Shopify JSON: {json.dumps(data, ensure_ascii=False)[:1000]}")

        # Construye un resumen básico para el correo
        email = data.get("email") or "sin_email"
        first = data.get("first_name") or data.get("firstName") or ""
        phone = data.get("phone") or ""
        admin_id = data.get("admin_graphql_api_id") or ""
        resumen_html = f"""
        <h3>Nuevo evento Shopify</h3>
        <ul>
          <li><b>Email:</b> {email}</li>
          <li><b>Nombre:</b> {first}</li>
          <li><b>Tel:</b> {phone}</li>
          <li><b>ID:</b> {admin_id}</li>
        </ul>
        <pre style="white-space:pre-wrap;font-size:12px;background:#f7f7f7;padding:8px;border:1px solid #eee;">
        {json.dumps(data, ensure_ascii=False, indent=2)[:4000]}
        </pre>
        """

        recipients = split_recipients(ALERT_TO_CSV)
        if not recipients:
            return jsonify(ok=False, error="ALERT_TO vacío o inválido"), 400

        resp = brevo_send_email(
            recipients,
            subject=f"🛒 Shopify webhook: {email}",
            html=resumen_html,
            tags=["shopify-webhook"],
        )
        return jsonify(ok=True, brevo=resp), 201

    except Exception as e:
        app.logger.exception("Error procesando webhook Shopify")
        return jsonify(ok=False, error=str(e)), 500

# -----------------------------------------------------------------------------
# Main (para correr en local: python app.py)
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
