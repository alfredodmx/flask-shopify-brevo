from flask import Flask, request, jsonify
import requests
import json
import os

app = Flask(__name__)

# Obtener API Keys desde las variables de entorno
BREVO_API_KEY = os.getenv("BREVO_API_KEY")
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
SHOPIFY_STORE = "uaua8v-s7.myshopify.com"  # Reemplaza con tu dominio real de Shopify

if not BREVO_API_KEY or not SHOPIFY_ACCESS_TOKEN:
    print("❌ ERROR: Las API Keys no están configuradas. Asegúrate de definir 'BREVO_API_KEY' y 'SHOPIFY_ACCESS_TOKEN'.")
    exit(1)

# Endpoint de la API de Brevo para agregar un nuevo contacto
BREVO_API_URL = "https://api.sendinblue.com/v3/contacts"

# Función para obtener los metacampos de un cliente en Shopify
def get_customer_metafields(customer_id):
    shopify_url = f"https://{SHOPIFY_STORE}/admin/api/2023-10/customers/{customer_id}/metafields.json"
    
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }

    response = requests.get(shopify_url, headers=headers)
    
    if response.status_code == 200:
        metafields = response.json().get("metafields", [])
        # Buscar el metafield de tipo file_reference para obtener la imagen
        file_reference = next((m["value"] for m in metafields if m["key"] == "tengo_un_plano"), None)
        
        if file_reference:
            # Llamar a la API de Shopify para obtener la URL pública de la imagen
            image_url = get_image_url_from_file_reference(file_reference)
            if image_url:
                return image_url
            else:
                print("❌ No se pudo obtener la URL de la imagen del archivo.")
                return "Sin URL"
        else:
            print("❌ No se encontró el file_reference en los metacampos")
            return "Sin URL"
    else:
        print("❌ Error obteniendo metacampos de Shopify:", response.text)
        return "Sin URL"

# Función para obtener la URL pública de la imagen desde el file_reference
def get_image_url_from_file_reference(file_reference):
    # Verificar que el GID sea válido
    if not file_reference.startswith("gid://shopify/MediaImage/"):
        print("❌ El file_reference no tiene el formato esperado.")
        return None

    file_id = file_reference.split("/")[-1]  # Obtener solo el ID del archivo del GID
    shopify_url = f"https://{SHOPIFY_STORE}/admin/api/2023-10/files/{file_id}.json"
    
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    
    response = requests.get(shopify_url, headers=headers)
    
    if response.status_code == 200:
        file_data = response.json().get("file", {})
        if file_data.get("url"):
            return file_data.get("url")  # URL pública del archivo
        else:
            print("❌ No se encontró la URL pública del archivo.")
            return None
    else:
        print(f"❌ Error al obtener la URL del archivo: {response.text}")
        return None

# Ruta del webhook que Shopify enviará a esta API
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
        customer_id = data.get("id")  # Obtener el ID del cliente para buscar metacampos
        email = data.get("email")
        first_name = data.get("first_name", "")
        last_name = data.get("last_name", "")
        phone = data.get("phone", "")

        if not email or not customer_id:
            print("❌ ERROR: No se recibió un email o ID de cliente válido.")
            return jsonify({"error": "Falta email o ID de cliente"}), 400

        # Obtener los metacampos desde Shopify
        image_url = get_customer_metafields(customer_id)

        if image_url:
            print(f"🔍 URL de la imagen: {image_url}")
        else:
            print("❌ ERROR: No se pudo obtener la URL de la imagen.")
            image_url = "Sin URL"

        # Crear el contacto con los metacampos incluidos
        contact_data = {
            "email": email,
            "attributes": {
                "NOMBRE": first_name,
                "APELLIDOS": last_name,
                "TELEFONO_WHATSAPP": phone,
                "WHATSAPP": phone,
                "SMS": phone,
                "LANDLINE_NUMBER": phone,
                "PLANO_CLIENTE": image_url,  # URL del archivo
            }
        }

        headers = {
            "api-key": BREVO_API_KEY,
            "Content-Type": "application/json"
        }

        # Enviar los datos a Brevo
        response = requests.post(BREVO_API_URL, json=contact_data, headers=headers)

        # Imprimir la respuesta de Brevo
        print("🔍 Respuesta de Brevo:", response.status_code, response.text)

        if response.status_code == 200:
            return jsonify({"message": "Contacto creado en Brevo con metacampos"}), 200
        else:
            return jsonify({"error": "No se pudo crear el contacto en Brevo", "details": response.text}), 400

    except Exception as e:
        print("❌ ERROR procesando el webhook:", str(e))
        return jsonify({"error": "Error interno"}), 500

# Iniciar el servidor en Render
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
