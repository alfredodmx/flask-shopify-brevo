from flask import Flask, request, jsonify
import os
import logging
import requests

app = Flask(__name__)

# ---------- Config básica ----------
logging.basicConfig(level=logging.INFO)
ALERT_FROM_EMAIL = os.getenv("ALERT_FROM_EMAIL") or os.getenv("ALERT_FROM")
ALERT_FROM_NAME  = os.getenv("ALERT_FROM_NAME", "Leads")
ALERT_TO         = [e.strip() for e in (os.getenv("ALERT_TO") or "").split(",") if e.strip()]
BREVO_API_KEY    = os.getenv("BREVO_API_KEY")

# ---------- Health ----------
@app.get("/")
def index():
    return "OK — flask-shopify-brevo", 200

# ---------- Webhook de Brevo (SMTP) ----------
# IMPORTANTE: esta ruta es la que configuraste en Brevo
@app.post("/webhook/brevo-events")
def brevo_events():
    payload = request.get_json(silent=True) or {}
    app.logger.info("📩 Brevo webhook payload: %s", payload)
    # Devuelve 200 siempre para que Brevo lo considere recibido
    return jsonify(ok=True), 200

# ---------- Utilidad: ver env clave ----------
@app.get("/debug/brevo/env")
def brevo_env():
    return jsonify({
        "ok": True,
        "env":{
            "ALERT_FROM_EMAIL": bool(ALERT_FROM_EMAIL),
            "ALERT_FROM_NAME": ALERT_FROM_NAME,
            "ALERT_TO_count": len(ALERT_TO),
            "BREVO_API_KEY_loaded": bool(BREVO_API_KEY)
        }
    }), 200

# ---------- Utilidad: /debug/alert (envía correo con Brevo API) ----------
@app.post("/debug/alert")
def debug_alert():
    if not (BREVO_API_KEY and ALERT_FROM_EMAIL and ALERT_TO):
        return jsonify(ok=False, msg="Faltan env: BREVO_API_KEY, ALERT_FROM_EMAIL/ALERT_FROM, ALERT_TO"), 500

    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "api-key": BREVO_API_KEY,
        "accept": "application/json",
        "content-type": "application/json"
    }
    body = {
        "sender": {"name": ALERT_FROM_NAME, "email": ALERT_FROM_EMAIL},
        "to": [{"email": e} for e in ALERT_TO],
        "subject": "🔔 Alerta de prueba (Render/Debug)",
        "htmlContent": "<p>Hola desde Render + Brevo (debug)</p>",
        "tags": ["render-debug"]
    }
    app.logger.info("➡️ Brevo API send: to=%s, subject=%s", ALERT_TO, body["subject"])
    r = requests.post(url, headers=headers, json=body, timeout=15)
    app.logger.info("⬅️ Brevo API status=%s, body=%s", r.status_code, r.text)
    return jsonify(ok=(r.status_code == 201), brevo=r.json() if r.text else {}), r.status_code

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
