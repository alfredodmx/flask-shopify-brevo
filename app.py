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

# 📝 Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 📊 Métricas simples
metrics = defaultdict(int)

# 🚦 Rate limiting
limiter = Limiter(
    app,
    key_func=get_remote_address,
    default_limits=["200 per hour"],
    storage_uri="memory://"
)

# 🔑 Variables de entorno
BREVO_API_KEY = os.getenv("BREVO_API_KEY")
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
SHOPIFY_WEBHOOK_SECRET = os.getenv("SHOPIFY_WEBHOOK_SECRET")
SHOPIFY_STORE = os.getenv("SHOPIFY_STORE", "uaua8v-s7.myshopify.com")

# Validación crítica
required_vars = {
    "BREVO_API_KEY": BREVO_API_KEY,
    "SHOPIFY_ACCESS_TOKEN": SHOPIFY_ACCESS_TOKEN,
    "SHOPIFY_WEBHOOK_SECRET": SHOPIFY_WEBHOOK_SECRET,
}
missing = [k for k, v in required_vars.items() if not v]
if missing:
    logger.error(f"❌ Faltan variables de entorno: {', '.join(missing)}")
    raise EnvironmentError("Variables críticas faltantes.")

# ✅ URLs corregidas (sin espacios)
BREVO_API_URL = "https://api.sendinblue.com/v3/contacts"
BREVO_CONTACT_URL = "https://api.sendinblue.com/v3/contacts/{email}"
SHOPIFY_GRAPHQL_URL = f"https://{SHOPIFY_STORE}/admin/api/2023-10/graphql.json"
SHOPIFY_METAFIELDS_URL = f"https://{SHOPIFY_STORE}/admin/api/2023-10/customers/{{customer_id}}/metafields.json"

# 🔄 Sesión HTTP reutilizable
session = requests.Session()
session.headers.update({"Content-Type": "application/json"})


# 🔒 Decorador para validar firma del webhook de Shopify
def verify_shopify_webhook(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        metrics["webhooks_received"] += 1
        signature = request.headers.get('X-Shopify-Hmac-Sha256')
        if not signature:
            metrics["webhooks_invalid_signature"] += 1
            logger.warning("⚠️ Webhook recibido sin firma HMAC.")
            return jsonify({"error": "Firma HMAC faltante"}), 401

        body = request.get_data()
        expected_signature = hmac.new(
            SHOPIFY_WEBHOOK_SECRET.encode('utf-8'),
            body,
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(signature, expected_signature):
            metrics["webhooks_invalid_signature"] += 1
            logger.warning("🚨 Firma HMAC inválida. Posible ataque.")
            return jsonify({"error": "Firma HMAC inválida"}), 401

        metrics["webhooks_valid"] += 1
        return f(*args, **kwargs)
    return decorated_function


# 📌 Obtener URL pública de un archivo en Shopify
def get_public_file_url(gid):
    if not gid:
        return "Sin plano"

    headers = {"X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN}

    # Intentar como MediaImage
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
        resp = session.post(SHOPIFY_GRAPHQL_URL, headers=headers, json=query_image)
        resp.raise_for_status()
        data = resp.json()
        url = data.get("data", {}).get("node", {}).get("image", {}).get("url")
        if url:
            return url
    except Exception as e:
        logger.warning(f"⚠️ Error consultando MediaImage para GID {gid}: {e}")

    # Intentar como GenericFile
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
        resp = session.post(SHOPIFY_GRAPHQL_URL, headers=headers, json=query_file)
        resp.raise_for_status()
        data = resp.json()
        url = data.get("data", {}).get("node", {}).get("url")
        return url if url else "Sin plano"
    except Exception as e:
        logger.warning(f"⚠️ Error consultando GenericFile para GID {gid}: {e}")
        return "Sin plano"


# 📌 Obtener metacampos del cliente
def get_customer_metafields(customer_id):
    url = SHOPIFY_METAFIELDS_URL.format(customer_id=customer_id)
    headers = {"X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN}

    try:
        resp = session.get(url, headers=headers)
        resp.raise_for_status()
        metafields = {mf["key"]: mf["value"] for mf in resp.json().get("metafields", [])}

        defaults = {
            "modelo": "Sin modelo",
            "precio": "Sin precio",
            "describe_lo_que_quieres": "Sin descripción",
            "tu_direccin_actual": "Sin dirección",
            "indica_tu_presupuesto": "Sin presupuesto",
            "tipo_de_persona": "Sin persona"
        }

        plano_gid = metafields.get("tengo_un_plano")
        plano_url = get_public_file_url(plano_gid) if plano_gid else "Sin plano"

        return (
            metafields.get("modelo", defaults["modelo"]),
            metafields.get("precio", defaults["precio"]),
            metafields.get("describe_lo_que_quieres", defaults["describe_lo_que_quieres"]),
            plano_url,
            metafields.get("tu_direccin_actual", defaults["tu_direccin_actual"]),
            metafields.get("indica_tu_presupuesto", defaults["indica_tu_presupuesto"]),
            metafields.get("tipo_de_persona", defaults["tipo_de_persona"])
        )
    except Exception as e:
        logger.error(f"❌ Error obteniendo metacampos para cliente {customer_id}: {e}")
        return ("Error",) * 7


# 📩 Webhook de Shopify (protegido)
@app.route('/webhook/shopify', methods=['POST'])
@limiter.limit("20 per minute")
@verify_shopify_webhook
def receive_webhook():
    try:
        data = request.get_json()
        if not data:  # ✅ CORREGIDO: esta era la línea 106 con error de sintaxis
            logger.warning("Webhook sin JSON válido.")
            return jsonify({"error": "JSON inválido"}), 400

        email = data.get("email")
        customer_id = data.get("id")
        if not email or not customer_id:
            logger.warning("Faltan email o ID de cliente.")
            return jsonify({"error": "Faltan campos obligatorios"}), 400

        logger.info(f"✅ Procesando webhook autenticado: {email} (ID: {customer_id})")

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

        # Verificar si el contacto existe
        check_resp = session.get(contact_url, headers=brevo_headers)

        if check_resp.status_code == 200:
            # Actualizar
            resp = session.put(contact_url, json=contact_data, headers=brevo_headers)
            if resp.status_code == 200:
                metrics["contacts_updated"] += 1
                logger.info(f"🔄 Contacto actualizado: {email}")
                return jsonify({"message": "Contacto actualizado en Brevo"}), 200
        elif check_resp.status_code == 404:
            # Crear
            resp = session.post(BREVO_API_URL, json=contact_data, headers=brevo_headers)
            if resp.status_code == 201:
                metrics["contacts_created"] += 1
                logger.info(f"🆕 Contacto creado: {email}")
                return jsonify({"message": "Contacto creado en Brevo"}), 201

        # Error en Brevo
        metrics["brevo_errors"] += 1
        final_resp = resp if 'resp' in locals() else check_resp
        logger.error(f"❌ Error en Brevo ({email}): {final_resp.status_code}")
        return jsonify({
            "error": "Falló la operación en Brevo",
            "status": final_resp.status_code,
            "details": final_resp.text[:200]
        }), 400

    except Exception as e:
        metrics["internal_errors"] += 1
        logger.exception("💥 Error no controlado en webhook")
        return jsonify({"error": "Error interno"}), 500


# 🩺 Health check para Render
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        "status": "healthy",
        "service": "shopify-brevo-sync",
        "metrics": dict(metrics)
    }), 200


# 📈 Métricas (opcional)
@app.route('/metrics', methods=['GET'])
def metrics_endpoint():
    return jsonify(dict(metrics)), 200


# 🔥 Iniciar servidor
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
