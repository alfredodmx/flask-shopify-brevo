import os
import json
from flask import Flask, request, jsonify
import requests

# =========================
# Config & helpers
# =========================

def _split_emails(raw: str) -> list[str]:
    if not raw:
        return []
    seps = [",", ";", " "]
    out = [raw]
    for s in seps:
        next_out = []
        for chunk in out:
            next_out.extend(chunk.split(s))
        out = next_out
    return [x.strip() for x in out if x and "@" in x]

PORT = int(os.environ.get("PORT", "10000"))
BREVO_API_KEY   = os.environ.get("BREVO_API_KEY", "").strip()
BREVO_BASE      = os.environ.get("BREVO_BASE", "https://api.brevo.com/v3").rstrip("/")
ALERT_FROM_NAME = os.environ.get("ALERT_FROM_NAME", "Leads").strip()
ALERT_FROM_EMAIL= os.environ.get("ALERT_FROM_EMAIL", "").strip()   # ej: info@espaciocontainerhouse.cl
ALERT_TO        = _split_emails(os.environ.get("ALERT_TO", "alfredodmx@gmail.com,alfredodmxf@gmail.com"))
BREVO_LIST_ID   = os.environ.get("BREVO_LIST_ID", "7").strip()     # opcional

# Validación mínima en arranque (solo imprime, no rompe)
if not BREVO_API_KEY:
    print("⚠️  BREVO_API_KEY vacío (setéalo en Render).")
if not ALERT_FROM_EMAIL:
    print("⚠️  ALERT_FROM_EMAIL vacío (setéalo en Render).")
if not ALERT_TO:
    print("⚠️  ALERT_TO vacío (setéalo en Render).")

app = Flask(__name__)

# =========================
# Brevo client utils
# =========================

def brevo_headers_json() -> dict:
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "api-key": BREVO_API_KEY,
    }

def brevo_get_account() -> dict:
    r = requests.get(f"{BREVO_BASE}/account", headers=brevo_headers_json(), timeout=20)
    print(f"➡️ Brevo GET /account status={r.status_code}")
    print(f"⬅️ body={r.text[:1000]}")
    r.raise_for_status()
    return r.json()

def brevo_send_email(to_list: list[str], subject: str, html: str, tags: list[str] | None = None) -> dict:
    payload = {
        "sender": {"name": ALERT_FROM_NAME, "email": ALERT_FROM_EMAIL},
        "to": [{"email": x} for x in to_list],
        "subject": subject,
        "htmlContent": html,
    }
    if tags:
        payload["tags"] = tags

    r = requests.post(f"{BREVO_BASE}/smtp/email", headers=brevo_headers_json(), json=payload, timeout=25)
    print(f"➡️ Brevo POST /smtp/email status={r.status_code}")
    print(f"⬅️ body={r.text[:1000]}")
    r.raise_for_status()
    return r.json()

def brevo_get_events_by_email(email: str, limit: int = 20, offset: int = 0) -> dict:
    params = {"email": email, "limit": limit, "offset": offset}
    r = requests.get(f"{BREVO_BASE}/smtp/emails", headers=brevo_headers_json(), params=params, timeout=20)
    print(f"➡️ Brevo GET /smtp/emails?email={email} status={r.status_code}")
    print(f"⬅️ body={r.text[:1200]}")
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        # algunos endpoints de Brevo devuelven vacío "{}"
        return {}

def brevo_get_blocked(email: str) -> dict:
    params = {"email": email}
    r = requests.get(f"{BREVO_BASE}/smtp/blockedContacts", headers=brevo_headers_json(), params=params, timeout=20)
    print(f"➡️ Brevo GET /smtp/blockedContacts?email={email} status={r.status_code}")
    print(f"⬅️ body={r.text[:1000]}")
    r.raise_for_status()
    return r.json()

# =========================
# Routes
# =========================

@app.route("/", methods=["GET"])
def root_ok():
    return jsonify(ok=True, msg="flask-shopify-brevo up", port=PORT), 200

@app.route("/debug/brevo/env", methods=["GET"])
def debug_env():
    safe_env = {
        "BREVO_API_KEY_loaded": bool(BREVO_API_KEY),
        "BREVO_BASE": BREVO_BASE,
        "ALERT_FROM_NAME": ALERT_FROM_NAME,
        "ALERT_FROM_EMAIL": bool(ALERT_FROM_EMAIL),
        "ALERT_TO_count": len(ALERT_TO),
        "BREVO_LIST_ID": BREVO_LIST_ID,
    }
    return jsonify(ok=True, env=safe_env), 200

@app.route("/debug/brevo/account", methods=["GET"])
def debug_account():
    try:
        account = brevo_get_account()
        return jsonify(ok=True, account=account), 200
    except Exception as e:
        print(f"❌ /debug/brevo/account error: {e}")
        return jsonify(ok=False, error=str(e)), 500

@app.route("/debug/alert", methods=["POST", "GET"])
def debug_alert():
    """
    Envía una alerta rápida usando ALERT_TO desde ENV.
    """
    try:
        subject = "Alerta de prueba desde Render"
        html = "<p>Hola, este es un test desde /debug/alert</p>"
        resp = brevo_send_email(ALERT_TO, subject, html, tags=["render-debug", "shopify-webhook"])
        return jsonify(ok=True, msg="Prueba de alerta enviada", brevo=resp), 200
    except Exception as e:
        print(f"❌ /debug/alert error: {e}")
        return jsonify(ok=False, error=str(e)), 500

@app.route("/debug/brevo/send", methods=["POST"])
def debug_brevo_send():
    """
    Envía a un destinatario arbitrario.
    Body JSON:
    {
      "to": "correo@dominio.com",
      "subject": "opcional",
      "html": "<p>opcional</p>",
      "tags": ["render-debug"]
    }
    """
    j = {}
    try:
        j = request.get_json(force=True) or {}
    except Exception:
        pass

    to = j.get("to")
    subject = j.get("subject") or "Prueba directa API"
    html = j.get("html") or "<p>Hola desde /debug/brevo/send</p>"
    tags = j.get("tags")

    if not to:
        return jsonify(ok=False, error="Falta 'to'"), 400

    try:
        resp = brevo_send_email([to], subject, html, tags=tags)
        return jsonify(ok=True, brevo=resp), 200
    except Exception as e:
        print(f"❌ /debug/brevo/send error: {e}")
        return jsonify(ok=False, error=str(e)), 500

@app.route("/debug/brevo/events", methods=["GET"])
def debug_brevo_events():
    """
    Consulta eventos por destinatario.
    GET /debug/brevo/events?email=alguien@dominio.com&limit=20&offset=0
    """
    email = request.args.get("email", "").strip()
    if not email:
        return jsonify(ok=False, error="Falta parámetro ?email="), 400
    limit = int(request.args.get("limit", "20"))
    offset = int(request.args.get("offset", "0"))
    try:
        data = brevo_get_events_by_email(email, limit=limit, offset=offset)
        return jsonify(ok=True, email=email, events=data), 200
    except Exception as e:
        print(f"❌ /debug/brevo/events error: {e}")
        return jsonify(ok=False, error=str(e)), 500

@app.route("/debug/brevo/blocked", methods=["GET"])
def debug_brevo_blocked():
    """
    Verifica si un destinatario está bloqueado en Brevo.
    GET /debug/brevo/blocked?email=alguien@dominio.com
    """
    email = request.args.get("email", "").strip()
    if not email:
        return jsonify(ok=False, error="Falta parámetro ?email="), 400
    try:
        data = brevo_get_blocked(email)
        return jsonify(ok=True, email=email, data=data), 200
    except Exception as e:
        print(f"❌ /debug/brevo/blocked error: {e}")
        return jsonify(ok=False, error=str(e)), 500

# =========================
# Shopify webhook (opcional)
# =========================
@app.route("/webhook/shopify", methods=["POST"])
def webhook_shopify():
    raw = request.get_data(as_text=True)
    print(f"📩 Webhook recibido (RAW): {raw[:1500]}")
    try:
        data = request.get_json(force=True)
        print("📩 Webhook recibido de Shopify (JSON):")
        print(json.dumps(data, ensure_ascii=False, indent=4)[:2000])
    except Exception as e:
        print(f"⚠️ No JSON en webhook: {e}")

    # Ejemplo mínimo de procesamiento…
    return jsonify(ok=True), 201

# =========================
# Entrypoint (para debug local)
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=True)
