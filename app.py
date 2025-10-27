# --- DEBUG BREVO TOOLS (añade al final de app.py) ---
import os, hashlib, json, requests
from flask import request, jsonify

BREVO_URL = "https://api.brevo.com/v3"
BREVO_KEY = os.getenv("BREVO_API_KEY", "").strip()

def mask_key(k: str) -> str:
    if not k: return "EMPTY"
    h = hashlib.sha256(k.encode()).hexdigest()[:8]
    return f"len={len(k)}, sha256[0:8]={h}"

def brevo_headers():
    return {
        "api-key": BREVO_KEY,
        "accept": "application/json",
        "content-type": "application/json",
    }

@app.route("/debug/brevo/env", methods=["GET"])
def debug_brevo_env():
    return jsonify({
        "brevo_key_mask": mask_key(BREVO_KEY),
        "has_key": bool(BREVO_KEY),
    }), 200

@app.route("/debug/brevo/account", methods=["GET"])
def debug_brevo_account():
    try:
        r = requests.get(f"{BREVO_URL}/account", headers=brevo_headers(), timeout=12)
        return jsonify({
            "status_code": r.status_code,
            "ok": r.ok,
            "body": r.json() if r.headers.get("content-type","").startswith("application/json") else r.text,
            "brevo_key_mask": mask_key(BREVO_KEY),
        }), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/debug/brevo/send", methods=["POST"])
def debug_brevo_send():
    """Body JSON: { "to": "correo@dominio.com" }"""
    try:
        data = request.get_json(force=True) or {}
        to = data.get("to")
        if not to:
            return jsonify({"ok": False, "error": "Falta 'to'"}), 400

        payload = {
            "sender": {"name": "Leads", "email": "info@espaciocontainerhouse.cl"},
            "to": [{"email": to}],
            "subject": "Prueba desde Render (API Brevo)",
            "htmlContent": "<p>Hola, prueba directa desde el servidor Render.</p>",
            "tags": ["render-debug"]
        }
        r = requests.post(f"{BREVO_URL}/smtp/email",
                          headers=brevo_headers(),
                          data=json.dumps(payload),
                          timeout=15)
        body = r.json() if "application/json" in r.headers.get("content-type","") else r.text
        return jsonify({
            "status_code": r.status_code,
            "ok": r.ok,
            "response": body,
            "brevo_key_mask": mask_key(BREVO_KEY),
        }), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/debug/brevo/events", methods=["GET"])
def debug_brevo_events():
    """/debug/brevo/events?email=dest@dom.com"""
    try:
        email = request.args.get("email", "")
        if not email:
            return jsonify({"ok": False, "error": "Falta parámetro 'email'"}), 400
        r = requests.get(f"{BREVO_URL}/smtp/emails",
                         headers=brevo_headers(),
                         params={"email": email, "limit": 20, "offset": 0},
                         timeout=12)
        return jsonify({
            "status_code": r.status_code,
            "ok": r.ok,
            "response": r.json() if r.headers.get("content-type","").startswith("application/json") else r.text,
        }), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/debug/brevo/blocked", methods=["GET"])
def debug_brevo_blocked():
    """/debug/brevo/blocked?email=dest@dom.com"""
    try:
        email = request.args.get("email", "")
        if not email:
            return jsonify({"ok": False, "error": "Falta 'email'"}), 400
        r = requests.get(f"{BREVO_URL}/smtp/blockedContacts",
                         headers=brevo_headers(),
                         params={"email": email},
                         timeout=12)
        return jsonify({
            "status_code": r.status_code,
            "ok": r.ok,
            "response": r.json() if r.headers.get("content-type","").startswith("application/json") else r.text,
        }), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
# --- FIN DEBUG ---
