# app.py
import os
import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any

import requests
from flask import Flask, request, jsonify

# -----------------------------------------------------------------------------
# Config & Logging
# -----------------------------------------------------------------------------
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("app")

app = Flask(__name__)

# -----------------------------------------------------------------------------
# ENV helpers
# -----------------------------------------------------------------------------
def _split_csv(value: str) -> List[str]:
    if not value:
        return []
    return [x.strip() for x in value.split(",") if x.strip()]

BREVO_API_KEY: str = os.getenv("BREVO_API_KEY", "")
BREVO_BASE = "https://api.brevo.com/v3"

ALERT_FROM_EMAIL = os.getenv("ALERT_FROM_EMAIL") or os.getenv("ALERT_FROM")  # compat
ALERT_FROM_NAME = os.getenv("ALERT_FROM_NAME", "Leads")
ALERT_TO = _split_csv(os.getenv("ALERT_TO", ""))

# Opcionales
BREVO_LIST_ID = os.getenv("BREVO_LIST_ID")
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")

# -----------------------------------------------------------------------------
# Utils Brevo
# -----------------------------------------------------------------------------
def brevo_headers() -> Dict[str, str]:
    if not BREVO_API_KEY:
        raise RuntimeError("Falta BREVO_API_KEY")
    return {
        "api-key": BREVO_API_KEY,
        "accept": "application/json",
        "content-type": "application/json",
    }

def brevo_send_email(to_emails: List[str], subject: str, html: str,
                     tags: List[str] | None = None,
                     template_id: int | None = None) -> Dict[str, Any]:
    """Envía correo por API transaccional de Brevo. Devuelve dict con status y body."""
    if not ALERT_FROM_EMAIL:
        raise RuntimeError("Falta ALERT_FROM_EMAIL (remitente)")

    payload: Dict[str, Any] = {
        "sender": {"name": ALERT_FROM_NAME, "email": ALERT_FROM_EMAIL},
        "to": [{"email": e} for e in to_emails],
    }

    if template_id:
        payload["templateId"] = int(template_id)
    else:
        payload["subject"] = subject
        payload["htmlContent"] = html

    if tags:
        payload["tags"] = tags

    url = f"{BREVO_BASE}/smtp/email"
    log.info("➡️ Brevo API send: to=%s, subject=%s", to_emails, subject)
    r = requests.post(url, headers=brevo_headers(), data=json.dumps(payload), timeout=20)
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text}
    log.info("⬅️ Brevo API status=%s, body=%s", r.status_code, json.dumps(body))
    if r.status_code not in (200, 201, 202):
        raise RuntimeError(f"Brevo envío falló ({r.status_code}): {body}")
    return {"status": r.status_code, "body": body}

def brevo_account() -> Dict[str, Any]:
    r = requests.get(f"{BREVO_BASE}/account", headers=brevo_headers(), timeout=15)
    return r.json()

def brevo_events_by_email(email: str, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    url = f"{BREVO_BASE}/smtp/emails?email={requests.utils.quote(email)}&limit={limit}&offset={offset}"
    r = requests.get(url, headers=brevo_headers(), timeout=15)
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text}
    return {"status": r.status_code, "data": data}

def brevo_blocked(email: str) -> Dict[str, Any]:
    url = f"{BREVO_BASE}/smtp/blockedContacts?email={requests.utils.quote(email)}"
    r = requests.get(url, headers=brevo_headers(), timeout=15)
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text}
    return {"status": r.status_code, "data": data}

# -----------------------------------------------------------------------------
# Basic endpoints
# -----------------------------------------------------------------------------
@app.get("/")
def root():
    return "OK - flask-shopify-brevo"

@app.get("/debug/brevo/env")
def debug_env():
    return jsonify(
        ok=True,
        env={
            "ALERT_FROM_EMAIL": bool(ALERT_FROM_EMAIL),
            "ALERT_FROM_NAME": ALERT_FROM_NAME,
            "ALERT_TO_count": len(ALERT_TO),
            "BREVO_API_KEY_loaded": bool(BREVO_API_KEY),
            "BREVO_LIST_ID": BREVO_LIST_ID or "",
        },
    )

@app.get("/debug/brevo/account")
def debug_brevo_account():
    try:
        data = brevo_account()
        return jsonify(ok=True, account=data)
    except Exception as e:
        log.exception("Brevo account error")
        return jsonify(ok=False, error=str(e)), 500

# -----------------------------------------------------------------------------
# Send a test alert via Brevo
# -----------------------------------------------------------------------------
@app.post("/debug/alert")
def debug_alert():
    """Envía un correo de prueba a los destinatarios de ALERT_TO (o a ?to=email)."""
    try:
        body = request.get_json(silent=True) or {}
        override_to = body.get("to") if isinstance(body, dict) else None
        if not override_to:
            override_to = request.args.get("to")

        to_list: List[str] = []
        if override_to:
            if isinstance(override_to, list):
                to_list = override_to
            else:
                to_list = _split_csv(str(override_to))
        else:
            to_list = ALERT_TO

        if not to_list:
            return jsonify(ok=False, msg="No hay destinatarios (ALERT_TO o ?to=)"), 400

        subject = "🔔 Alerta de prueba (Render/Debug)"
        html = f"""
        <h3>Render → Brevo</h3>
        <p>Mensaje de prueba enviado {datetime.now(timezone.utc).isoformat()}</p>
        """

        resp = brevo_send_email(
            to_emails=to_list,
            subject=subject,
            html=html,
            tags=["render-debug"],
        )
        return jsonify(ok=True, msg="Prueba de alerta enviada", brevo=resp["body"])
    except Exception as e:
        log.exception("Error en /debug/alert")
        return jsonify(ok=False, error=str(e)), 500

# -----------------------------------------------------------------------------
# Brevo Webhook (EVENTOS REALES)
# -----------------------------------------------------------------------------
@app.post("/webhook/brevo-events")
def brevo_webhook():
    """Recibe eventos transaccionales reales de Brevo."""
    try:
        payload = request.get_json(force=True, silent=True)
        if payload is None:
            # Brevo puede mandar form-encoded en algunos escenarios
            payload = request.form.to_dict(flat=True)

        log.info("📩 Brevo webhook payload: %s", payload)
        # Aquí podrías guardar en base de datos, etc.

        return jsonify(ok=True)
    except Exception as e:
        log.exception("Error en webhook Brevo")
        return jsonify(ok=False, error=str(e)), 400

# -----------------------------------------------------------------------------
# Shopify Webhook (ejemplo)
# -----------------------------------------------------------------------------
@app.post("/webhook/shopify")
def shopify_webhook():
    """Ejemplo de recepción de webhook de Shopify (clientes, órdenes, etc.)."""
    try:
        raw = request.get_data(as_text=True)
        log.info("📩 Webhook recibido (RAW): %s", raw)

        data = request.get_json(force=True, silent=True) or {}
        log.info("📩 Webhook recibido de Shopify (JSON): %s", json.dumps(data, ensure_ascii=False, indent=4))

        # Ejemplo: extraer y loguear metacampos si existen
        meta_summary = []
        for k in ("modelo", "precio", "detalle", "plano", "direccion", "valor", "tipo_persona"):
            v = (data.get("metafields") or {}).get(k) if isinstance(data.get("metafields"), dict) else None
            if v is not None:
                meta_summary.append(f"{k}={v}")

        log.info("Metacampos: %s", " ".join(meta_summary) if meta_summary else "Sin metacampos")

        # (Opcional) Enviar alerta por email
        try:
            if ALERT_TO:
                subject = "🛒 Shopify Webhook recibido"
                html = f"<pre>{json.dumps(data, ensure_ascii=False, indent=2)}</pre>"
                brevo_send_email(ALERT_TO, subject, html, tags=["shopify", "webhook"])
        except Exception:
            log.exception("Fallo alerta por Brevo desde /webhook/shopify")

        return jsonify(ok=True), 201
    except Exception as e:
        log.exception("Error en /webhook/shopify")
        return jsonify(ok=False, error=str(e)), 400

# -----------------------------------------------------------------------------
# Endpoints de diagnóstico Brevo (eventos y bloqueos)
# -----------------------------------------------------------------------------
@app.get("/debug/brevo/events")
def brevo_events_query():
    try:
        email = request.args.get("email")
        if not email:
            return jsonify(ok=False, msg="Usa ?email=destinatario@dominio"), 400
        data = brevo_events_by_email(email=email, limit=int(request.args.get("limit", 50)))
        return jsonify(ok=(data["status"] == 200), **data), data["status"]
    except Exception as e:
        log.exception("Error consultando eventos")
        return jsonify(ok=False, error=str(e)), 500

@app.get("/debug/brevo/blocked")
def brevo_blocked_query():
    try:
        email = request.args.get("email")
        if not email:
            return jsonify(ok=False, msg="Usa ?email=destinatario@dominio"), 400
        data = brevo_blocked(email)
        return jsonify(ok=(data["status"] == 200), **data), data["status"]
    except Exception as e:
        log.exception("Error consultando bloqueos")
        return jsonify(ok=False, error=str(e)), 500

# -----------------------------------------------------------------------------
# Main (para ejecución local). En Render usa gunicorn con: app:app
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))  # Render expone PORT; default 10000
    app.run(host="0.0.0.0", port=port)
