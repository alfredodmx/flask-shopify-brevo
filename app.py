from flask import Flask, request, jsonify
import requests
import os
import logging
import hmac
import hashlib
from functools import wraps
from collections import defaultdict
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# 📝 Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 📊 Métricas simples (reiniciadas en cada deploy)
metrics = defaultdict(int)

# 🚦 Rate limiting: máximo 20 webhooks por minuto por IP
limiter = Limiter(
    app,
    key_func=get_remote_address,
    default_limits=["200 per hour"],  # Límite global suave
    storage_uri="memory://"  # Usa memoria (suficiente para una sola instancia en Render)
)

# 🔑 Variables de entorno
BREVO_API_KEY = os.getenv("BREVO_API_KEY")
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
SHOPIFY_WEBHOOK_SECRET = os.getenv("SHOPIFY_WEBHOOK_SECRET")
SHOPIFY_STORE = os.getenv("SHOPIFY_STORE", "uaua8v-s7.myshopify.com")

required_vars = {
    "BREVO_API_KEY": BREVO_API_KEY,
    "SHOPIFY_ACCESS_TOKEN": SHOPIFY_ACCESS_TOKEN,
    "SHOPIFY_WEBHOOK_SECRET": SHOPIFY_WEBHOOK_SECRET,
}
missing = [k for k, v in required_vars.items() if not v]
if missing:
    logger.error(f"❌ Faltan variables: {', '.join(missing)}")
    raise EnvironmentError("Variables críticas faltantes.")

# URLs
BREVO_API_URL = "https://api.sendinblue.com/v3/contacts"
BREVO_CONTACT_URL = "https://api.sendinblue.com/v3/contacts/{email}"
SHOPIFY_GRAPHQL_URL = f"https://{SHOPIFY_STORE}/admin/api/2023-10/graphql.json"
SHOPIFY_METAFIELDS_URL = f"https://{SHOPIFY_STORE}/admin/api/2023-10/customers/{{customer_id}}/metafields.json"

session = requests.Session()
session.headers.update({"Content-Type": "application/json"})


# 🔒 Validación de webhook
def verify_shopify_webhook(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        metrics["webhooks_received"] += 1
        signature = request.headers.get('X-Shopify-Hmac-Sha256')
        if not signature:
            metrics["webhooks_invalid_signature"] += 1
            logger.warning("⚠️ Webhook sin firma HMAC.")
            return jsonify({"error": "Firma HMAC faltante"}), 401

        body = request.get_data()
        expected_signature = hmac.new(
            SHOPIFY_WEBHOOK_SECRET.encode('utf-8'),
            body,
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(signature, expected_signature):
            metrics["webhooks_invalid_signature"] += 1
            logger.warning("🚨 Firma HMAC inválida.")
            return jsonify({"error": "Firma HMAC inválida"}), 401

        metrics["webhooks_valid"] += 1
        return f(*args, **kwargs)
    return decorated_function


# --- [Incluye aquí tus funciones get_public_file_url y get_customer_metafields] ---
# (Mantén las versiones completas de la versión anterior)

def get_public_file_url(gid):
    # ... (tu implementación anterior)
    pass

def get_customer_metafields(customer_id):
    # ... (tu implementación anterior)
    pass


# 📩 Webhook protegido + con rate limiting
@app.route('/webhook/shopify', methods=['POST'])
@limiter.limit("20 per minute")  # Límite estricto por endpoint
@verify_shopify_webhook
def receive_webhook():
    try:
        data = request.get_json()
        if not 
            return jsonify({"error": "JSON inválido"}), 400

        email = data.get("email")
        customer_id = data.get("id")
        if not email or not customer_id:
            return jsonify({"error": "Faltan campos"}), 400

        modelo, precio, desc, plano, direccion, presupuesto, tipo_persona = get_customer_metafields(customer_id)

        contact_data = {
            "email": email,
            "attributes": {
                "NOMBRE": data.get("first_name", ""),
                "APELLIDOS": data.get("last_name", ""),
                "TELEFONO_WHATSAPP": data.get("phone", ""),
                "WHATSAPP": data.get("phone", ""),
                "SMS": data.get("phone", ""),
                "LANDLINE_NUMBER": data.get("phone", ""),
                "MODELO_CABANA": modelo,
                "PRECIO_CABANA": precio,
                "DESCRIPCION_CLIENTE": desc,
                "PLANO_CLIENTE": plano,
                "DIRECCION_CLIENTE": direccion,
                "PRESUPUESTO_CLIENTE": presupuesto,
                "TIPO_DE_PERSONA": tipo_persona
            }
        }

        brevo_headers = {"api-key": BREVO_API_KEY}
        contact_url = BREVO_CONTACT_URL.format(email=email)

        check_resp = session.get(contact_url, headers=brevo_headers)

        if check_resp.status_code == 200:
            resp = session.put(contact_url, json=contact_data, headers=brevo_headers)
            if resp.status_code == 200:
                metrics["contacts_updated"] += 1
                logger.info(f"🔄 Actualizado: {email}")
                return jsonify({"message": "Actualizado"}), 200
        elif check_resp.status_code == 404:
            resp = session.post(BREVO_API_URL, json=contact_data, headers=brevo_headers)
            if resp.status_code == 201:
                metrics["contacts_created"] += 1
                logger.info(f"🆕 Creado: {email}")
                return jsonify({"message": "Creado"}), 201

        metrics["brevo_errors"] += 1
        final_resp = resp if 'resp' in locals() else check_resp
        return jsonify({"error": "Brevo error", "status": final_resp.status_code}), 400

    except Exception as e:
        metrics["internal_errors"] += 1
        logger.exception("💥 Error interno")
        return jsonify({"error": "Error interno"}), 500


# 🩺 Health check
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        "status": "healthy",
        "service": "shopify-brevo-sync",
        "metrics": dict(metrics)  # 👈 ¡Expone métricas aquí!
    }), 200


# 📈 Endpoint solo para métricas (opcional)
@app.route('/metrics', methods=['GET'])
def metrics_endpoint():
    return jsonify(dict(metrics)), 200


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
