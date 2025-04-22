from flask import Flask, request, jsonify
import requests
import json
import os

# 🔑 Crear la instancia de Flask
app = Flask(__name__)

# 🔑 Obtener API Key de Brevo y Shopify desde variables de entorno
BREVO_API_KEY = os.getenv("BREVO_API_KEY")
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")  # Agregamos la API Key de Shopify
SHOPIFY_STORE = "uaua8v-s7.myshopify.com"  # Reemplaza con tu dominio real de Shopify

if not BREVO_API_KEY or not SHOPIFY_ACCESS_TOKEN:
    print("❌ ERROR: Las API Keys no están configuradas. Asegúrate de definir 'BREVO_API_KEY' y 'SHOPIFY_ACCESS_TOKEN'.")
    exit(1)

# Endpoint de la API de Brevo para agregar un nuevo contacto
BREVO_API_URL = "https://api.sendinblue.com/v3/contacts"

# 📌 Función para obtener la URL pública de la imagen desde el ID del archivo
def get_image_url_from_shopify(image_id):
    print(f"🔍 Obteniendo URL para el archivo con ID: {image_id}")
    
    # URL de la API de archivos de Shopify
    shopify_url = f"https://{SHOPIFY_STORE}/admin/api/2023-10/files/{image_id}.json"
    
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }

    response = requests.get(shopify_url, headers=headers)
    
    if response.status_code == 200:
        file_data = response.json().get("file", {})
        print(f"🔍 Datos del archivo: {file_data}")  # Depuración de los datos del archivo
        file_url = file_data.get("url", "Sin URL")
        return file_url
    else:
        print("❌ Error obteniendo la URL del archivo de Shopify:", response.text)
        return "Sin URL"

# 📌 Función para obtener los metacampos de un cliente en Shopify
def get_customer_metafields(customer_id):
    shopify_url = f"https://{SHOPIFY_STORE}/admin/api/2023-10/customers/{customer_id}/metafields.json"
    
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }

    response = requests.get(shopify_url, headers=headers)
    
    if response.status_code == 200:
        metafields = response.json().get("metafields", [])
        modelo = next((m["value"] for m in metafields if m["key"] == "modelo"), "Sin modelo")
        precio = next((m["value"] for m in metafields if m["key"] == "precio"), "Sin precio")
        describe_lo_que_quieres = next((m["value"] for m in metafields if m["key"] == "describe_lo_que_quieres"), "Sin descripción")
        
        # Obtener el metacampo 'tengo_un_plano' (el que contiene la imagen)
        plano_metafield = next((m for m in metafields if m["key"] == "tengo_un_plano"), None)
        if plano_metafield and "value" in plano_metafield:
            # Asumimos que el valor del metacampo 'tengo_un_plano' es el ID del archivo
            image_id = plano_metafield["value"].split("/")[-1]  # Obtener el ID de la imagen (por ejemplo: 27194235715618)
            print(f"🔍 ID de la imagen extraído: {image_id}")  # Depuración del ID de la imagen
            tengo_un_plano = get_image_url_from_shopify(image_id)  # Obtener la URL pública de la imagen
        else:
            tengo_un_plano = "Sin plano"
        
        tu_direccin_actual = next((m["value"] for m in metafields if m["key"] == "tu_direccin_actual"), "Sin dirección")
        indica_tu_presupuesto = next((m["value"] for m in metafields if m["key"] == "indica_tu_presupuesto"), "Sin presupuesto")
        tipo_de_persona = next((m["value"] for m in metafields if m["key"] == "tipo_de_persona"), "Sin persona")
        
        # Corregido: retornar todas las variables necesarias
        return modelo, precio, describe_lo_que_quieres, tengo_un_plano, tu_direccin_actual, indica_tu_presupuesto, tipo_de_persona
    else:
        print("❌ Error obteniendo metacampos de Shopify:", response.text)
        return "Error", "Error", "Error", "Error", "Error", "Error", "Error"

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
        customer_id = data.get("id")  # Obtener el ID del cliente para buscar metacampos
        email = data.get("email")
        first_name = data.get("first_name", "")
        last_name = data.get("last_name", "")
        phone = data.get("phone", "")

        if not email or not customer_id:
            print("❌ ERROR: No se recibió un email o ID de cliente válido.")
            return jsonify({"error": "Falta email o ID de cliente"}), 400

        # 🔍 Obtener los metacampos desde Shopify
        modelo, precio, describe_lo_que_quieres, tengo_un_plano, tu_direccin_actual, indica_tu_presupuesto, tipo_de_persona = get_customer_metafields(customer_id)

        # Verificar que los metacampos no estén vacíos
        print("Valores de metacampos:", modelo, precio, describe_lo_que_quieres, tengo_un_plano, tu_direccin_actual, indica_tu_presupuesto, tipo_de_persona)

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
                "MODELO_CABANA": modelo,
                "PRECIO_CABANA": precio,
                "DESCRIPCION_CLIENTE": describe_lo_que_quieres,
                "PLANO_CLIENTE": tengo_un_plano,  # Ahora contiene la URL pública de la imagen
                "DIRECCION_CLIENTE": tu_direccin_actual,
                "PRESUPUESTO_CLIENTE": indica_tu_presupuesto,
                "TIPO_DE_PERSONA": tipo_de_persona
            }
        }

        headers = {
            "api-key": BREVO_API_KEY,
            "Content-Type": "application/json"
        }

        # 🚀 Imprimir qué datos se están enviando a Brevo
        print("📤 Enviando datos a Brevo:", json.dumps(contact_data, indent=4))

        # 🚀 Enviar los datos a Brevo
        response = requests.post(BREVO_API_URL, json=contact_data, headers=headers)

        # 🔍 Imprimir la respuesta de Brevo
        print("🔍 Respuesta de Brevo:", response.status_code, response.text)

        if response.status_code == 200:
            return jsonify({"message": "Contacto creado en Brevo con metacampos"}), 200
        else:
            return jsonify({"error": "No se pudo crear el contacto en Brevo", "details": response.text}), 400

    except Exception as e:
        print("❌ ERROR procesando el webhook:", str(e))
        return jsonify({"error": "Error interno"}), 500

# 🔥 Iniciar el servidor en Render
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
