from flask import Flask, request, jsonify
import requests
import json
import os

app = Flask(__name__)

# 🔑 Obtener API Key de Brevo y Shopify desde variables de entorno
BREVO_API_KEY = os.getenv("BREVO_API_KEY")
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
SHOPIFY_STORE = "uaua8v-s7.myshopify.com"  # Reemplaza con tu dominio real de Shopify

if not BREVO_API_KEY or not SHOPIFY_ACCESS_TOKEN:
    print("❌ ERROR: Las API Keys no están configuradas. Asegúrate de definir 'BREVO_API_KEY' y 'SHOPIFY_ACCESS_TOKEN'.")
    exit(1)

# Endpoint de la API de Brevo para agregar un nuevo contacto
BREVO_API_URL = "https://api.sendinblue.com/v3/contacts"

# 📌 Función para obtener los medios del producto desde Shopify usando GraphQL
def get_product_media(product_id):
    shopify_graphql_url = f"https://{SHOPIFY_STORE}/admin/api/2023-10/graphql.json"
    
    query = """
    query getProductMedia($productId: ID!) {
      product(id: $productId) {
        id
        media(first: 10) {
          edges {
            node {
              id
              mediaContentType
              preview {
                image {
                  src
                }
              }
            }
          }
        }
      }
    }
    """
    
    variables = {
        "productId": product_id
    }

    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }

    response = requests.post(shopify_graphql_url, json={"query": query, "variables": variables}, headers=headers)

    if response.status_code == 200:
        data = response.json()
        if data.get('data') and data['data'].get('product'):
            media = data['data']['product']['media']['edges']
            # Extraer las URLs de las imágenes
            image_urls = [node['node']['preview']['image']['src'] for node in media if node['node']['mediaContentType'] == 'IMAGE']
            return image_urls
        else:
            return []
    else:
        print("❌ Error al obtener medios del producto:", response.text)
        return []

# 📩 Ruta del webhook que Shopify enviará a esta API
@app.route('/webhook/shopify', methods=['POST'])
def receive_webhook():
    try:
        raw_data = request.data.decode('utf-8')  # Capturar datos crudos del webhook
        print("📩 Webhook recibido (RAW):", raw_data)

        # Intentar parsear JSON
        data = request.get_json(silent=True)

        if not data:
            print("❌ ERROR: No se pudo interpretar el JSON correctamente.")
            return jsonify({"error": "Webhook sin JSON válido"}), 400

        print("📩 Webhook recibido de Shopify (JSON):", json.dumps(data, indent=4))

        # Extraer información básica
        customer_id = data.get("id")
        email = data.get("email")
        first_name = data.get("first_name", "")
        last_name = data.get("last_name", "")
        phone = data.get("phone", "")
        product_id = data.get("product_id")  # Asegúrate de que el producto esté incluido en el webhook

        if not email or not customer_id or not product_id:
            print("❌ ERROR: No se recibió un email, ID de cliente o ID de producto válido.")
            return jsonify({"error": "Falta información necesaria"}), 400

        # 🔍 Obtener los medios del producto desde Shopify
        image_urls = get_product_media(product_id)

        # Verificar si encontramos imágenes
        if not image_urls:
            print("❌ ERROR: No se encontraron imágenes para el producto.")
            return jsonify({"error": "No se encontraron imágenes para el producto"}), 400

        # 📌 Crear el contacto con los metacampos incluidos
        contact_data = {
            "email": email,
            "attributes": {
                "NOMBRE": first_name,
                "APELLIDOS": last_name,
                "TELEFONO_WHATSAPP": phone,
                "WHATSAPP": phone,
                "SMS": phone,
                "LANDLINE_NUMBER": phone,
                "PLANO_CLIENTE": image_urls[0],  # Usamos la primera imagen como el plano
            }
        }

        headers = {
            "api-key": BREVO_API_KEY,
            "Content-Type": "application/json"
        }

        # 🚀 Enviar los datos a Brevo
        response = requests.post(BREVO_API_URL, json=contact_data, headers=headers)

        # 🔍 Imprimir la respuesta de Brevo
        print("🔍 Respuesta de Brevo:", response.status_code, response.text)

        if response.status_code == 200:
            return jsonify({"message": "Contacto creado en Brevo con imagen"}), 200
        else:
            return jsonify({"error": "No se pudo crear el contacto en Brevo", "details": response.text}), 400

    except Exception as e:
        print("❌ ERROR procesando el webhook:", str(e))
        return jsonify({"error": "Error interno"}), 500

# 🔥 Iniciar el servidor en Render
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
