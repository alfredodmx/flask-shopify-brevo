from flask import Flask, request, jsonify
import requests
import json
import os

app = Flask(__name__)

# Obtener API Key de Brevo y Shopify desde variables de entorno
BREVO_API_KEY = os.getenv("BREVO_API_KEY")
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
SHOPIFY_STORE = "uaua8v-s7.myshopify.com"  # Reemplaza con tu dominio real de Shopify

if not BREVO_API_KEY or not SHOPIFY_ACCESS_TOKEN:
    print("❌ ERROR: Las API Keys no están configuradas. Asegúrate de definir 'BREVO_API_KEY' y 'SHOPIFY_ACCESS_TOKEN'.")
    exit(1)

# Endpoint de la API de Brevo para agregar un nuevo contacto
BREVO_API_URL = "https://api.sendinblue.com/v3/contacts"

# Función para obtener la URL pública de la imagen en los archivos de Shopify
def get_image_url(file_reference):
    # Accedemos a la API de Shopify para obtener el archivo
    shopify_url = f"https://{SHOPIFY_STORE}/admin/api/2023-10/files.json"
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    
    response = requests.get(shopify_url, headers=headers)
    if response.status_code == 200:
        files = response.json().get("files", [])
        
        # Buscar la imagen en los archivos con el ID de referencia
        for file in files:
            if file["id"] == file_reference:
                return file["public_url"]  # Devolvemos la URL pública del archivo
    return None  # Si no se encuentra la imagen, retornamos None

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
        customer_id = data.get("id")  # Obtener el ID del cliente
        email = data.get("email")
        first_name = data.get("first_name", "")
        last_name = data.get("last_name", "")
        phone = data.get("phone", "")
        file_reference = data.get("metafields", {}).get("tengo_un_plano", None)  # Extraemos el file_reference

        if not email or not customer_id:
            print("❌ ERROR: No se recibió un email o ID de cliente válido.")
            return jsonify({"error": "Falta email o ID de cliente"}), 400

        if not file_reference:
            print("❌ ERROR: No se encontró el file_reference en los metacampos.")
            return jsonify({"error": "Falta file_reference"}), 400

        # Obtener la URL de la imagen usando el file_reference
        image_url = get_image_url(file_reference)
        
        if not image_url:
            print("❌ ERROR: No se pudo obtener la URL del archivo.")
            return jsonify({"error": "No se pudo obtener la URL del archivo"}), 400

        print(f"🔍 URL de la imagen obtenida: {image_url}")

        # Aquí puedes continuar con la lógica para enviar la URL a Brevo
        contact_data = {
            "email": email,
            "attributes": {
                "NOMBRE": first_name,
                "APELLIDOS": last_name,
                "TELEFONO_WHATSAPP": phone,
                "WHATSAPP": phone,
                "SMS": phone,
                "LANDLINE_NUMBER": phone,
                "PLANO_CLIENTE": image_url  # Usamos la URL de la imagen
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
