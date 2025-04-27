from flask import Flask, request, jsonify
import requests
import json
import os

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
BREVO_GET_CONTACT_API_URL = "https://api.sendinblue.com/v3/contacts/{email}"

# Endpoint de la API GraphQL de Shopify
SHOPIFY_GRAPHQL_URL = f"https://{SHOPIFY_STORE}/admin/api/2023-10/graphql.json"

# 📌 Función para obtener la URL pública de un MediaImage usando su GID
def get_public_image_url(gid):
    if not gid:
        return None
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    query = {
        "query": """
            query {
              mediaImage(id: "%s") {
                url
              }
            }
        """ % gid
    }
    try:
        response = requests.post(SHOPIFY_GRAPHQL_URL, headers=headers, json=query)
        response.raise_for_status()  # Lanza una excepción para errores HTTP
        data = response.json()
        if data and data.get("data") and data["data"].get("mediaImage") and data["data"]["mediaImage"].get("url"):
            return data["data"]["mediaImage"]["url"]
        else:
            print(f"⚠️ No se pudo obtener la URL pública para el GID: {gid}. Respuesta de Shopify: {data}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"❌ Error al consultar la API GraphQL de Shopify para el GID {gid}: {e}")
        return None

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
        tengo_un_plano_gid = next((m["value"] for m in metafields if m["key"] == "tengo_un_plano"), None)
        tu_direccin_actual = next((m["value"] for m in metafields if m["key"] == "tu_direccin_actual"), "Sin dirección")
        indica_tu_presupuesto = next((m["value"] for m in metafields if m["key"] == "indica_tu_presupuesto"), "Sin presupuesto")
        tipo_de_persona = next((m["value"] for m in metafields if m["key"] == "tipo_de_persona"), "Sin persona")

        # Obtener la URL pública del plano si el GID existe
        tengo_un_plano_url = get_public_image_url(tengo_un_plano_gid) if tengo_un_plano_gid else "Sin plano"

        return modelo, precio, describe_lo_que_quieres, tengo_un_plano_url, tu_direccin_actual, indica_tu_presupuesto, tipo_de_persona
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

        # 📌 Verificar si el contacto ya existe en Brevo
        headers = {
            "api-key": BREVO_API_KEY,
            "Content-Type": "application/json"
        }

        response = requests.get(BREVO_GET_CONTACT_API_URL.format(email=email), headers=headers)

        if response.status_code == 200:
            # Si el contacto ya existe, podemos optar por actualizarlo
            print(f"⚠️ El contacto con el correo {email} ya existe en Brevo. Se actualizará.")
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
                    "PLANO_CLIENTE": tengo_un_plano,  # Ahora debería ser la URL pública
                    "DIRECCION_CLIENTE": tu_direccin_actual,
                    "PRESUPUESTO_CLIENTE": indica_tu_presupuesto,
                    "TIPO_DE_PERSONA": tipo_de_persona
                }
            }

            # Actualizamos los datos del contacto existente
            update_response = requests.put(BREVO_GET_CONTACT_API_URL.format(email=email), json=contact_data, headers=headers)

            if update_response.status_code == 200:
                return jsonify({"message": "Contacto actualizado en Brevo"}), 200
            else:
                return jsonify({"error": "No se pudo actualizar el contacto en Brevo", "details": update_response.text}), 400
        elif response.status_code == 404:
            # Si el contacto no existe, creamos uno nuevo
            print(f"✅ El contacto con el correo {email} no existe. Se creará uno nuevo.")
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
                    "PLANO_CLIENTE": tengo_un_plano,  # Ahora debería ser la URL pública
                    "DIRECCION_CLIENTE": tu_direccin_actual,
                    "PRESUPUESTO_CLIENTE": indica_tu_presupuesto,
                    "TIPO_DE_PERSONA": tipo_de_persona
                }
            }

            # 🚀 Enviar los datos a Brevo para crear el nuevo contacto
            create_response = requests.post(BREVO_API_URL, json=contact_data, headers=headers)

            if create_response.status_code == 201:  # El código de creación exitosa suele ser 201
                return jsonify({"message": "Contacto creado en Brevo con metacampos"}), 201
            else:
                return jsonify({"error": "No se pudo crear el contacto en Brevo", "details": create_response.text}), 400
        else:
            return jsonify({"error": "Error al verificar si el contacto existe", "details": response.text}), 400

    except Exception as e:
        print("❌ ERROR procesando el webhook:", str(e))
        return jsonify({"error": "Error interno"}), 500

# 🔥 Iniciar el servidor en Render
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
