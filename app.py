import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr

ALERT_SMTP_HOST = os.getenv("ALERT_SMTP_HOST")
ALERT_SMTP_PORT = int(os.getenv("ALERT_SMTP_PORT", "587"))
ALERT_SMTP_USER = os.getenv("ALERT_SMTP_USER")
ALERT_SMTP_PASS = os.getenv("ALERT_SMTP_PASS")
ALERT_FROM      = os.getenv("ALERT_FROM")
ALERT_TO        = os.getenv("ALERT_TO", "")

def send_internal_alert(created: bool, email: str, attrs: dict):
    """Envía un correo interno vía SMTP (Zoho)."""
    if not (ALERT_SMTP_HOST and ALERT_SMTP_USER and ALERT_SMTP_PASS and ALERT_FROM and ALERT_TO):
        print("⚠️ SMTP de alertas no configurado; omito envío.")
        return

    subject = ("🆕 Nuevo lead" if created else "ℹ️ Lead actualizado") + f": {email}"

    html = f"""
    <h2>{'Nuevo lead' if created else 'Lead actualizado'} desde Shopify</h2>
    <p><b>Email:</b> {email}</p>
    <p><b>Nombre:</b> {attrs.get('NOMBRE','')} {attrs.get('APELLIDOS','')}</p>
    <p><b>Teléfono:</b> {attrs.get('TELEFONO_WHATSAPP','')}</p>
    <hr/>
    <p><b>Modelo:</b> {attrs.get('MODELO_CABANA','')}</p>
    <p><b>Precio:</b> {attrs.get('PRECIO_CABANA','')}</p>
    <p><b>Descripción:</b> {attrs.get('DESCRIPCION_CLIENTE','')}</p>
    <p><b>Plano (URL):</b> {attrs.get('PLANO_CLIENTE','')}</p>
    <p><b>Dirección:</b> {attrs.get('DIRECCION_CLIENTE','')}</p>
    <p><b>Presupuesto:</b> {attrs.get('PRESUPUESTO_CLIENTE','')}</p>
    <p><b>Tipo de persona:</b> {attrs.get('TIPO_DE_PERSONA','')}</p>
    <hr/>
    <p>Fuente: Shopify → Render → Brevo (Lista #7)</p>
    """

    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr(("Leads", ALERT_FROM))
    to_list = [t.strip() for t in ALERT_TO.split(",") if t.strip()]
    msg["To"] = ", ".join(to_list)

    try:
        with smtplib.SMTP(ALERT_SMTP_HOST, ALERT_SMTP_PORT) as server:
            server.starttls()
            server.login(ALERT_SMTP_USER, ALERT_SMTP_PASS)
            server.sendmail(ALERT_FROM, to_list, msg.as_string())
        print(f"📧 Alerta enviada a: {to_list}")
    except Exception as e:
        print("❌ Error enviando alerta SMTP:", str(e))


from flask import Flask, request, jsonify
import requests
import json
import os

app = Flask(__name__)

# 🔑 Variables de entorno requeridas
BREVO_API_KEY = os.getenv("BREVO_API_KEY")
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
SHOPIFY_STORE = "uaua8v-s7.myshopify.com"  # Reemplaza con tu dominio real de Shopify

# ✅ ID de la LISTA de Brevo (p.ej. 7 = "LEADS ESPACIO CONTAINER HOUSE")
#   Configúralo en Render → Environment como BREVO_LIST_ID=7
BREVO_LIST_ID = int(os.getenv("BREVO_LIST_ID", "0"))

if not BREVO_API_KEY or not SHOPIFY_ACCESS_TOKEN:
    print("❌ ERROR: Las API Keys no están configuradas. Define 'BREVO_API_KEY' y 'SHOPIFY_ACCESS_TOKEN'.")
    exit(1)

# Endpoints Brevo
BREVO_API_URL = "https://api.sendinblue.com/v3/contacts"
BREVO_GET_CONTACT_API_URL = "https://api.sendinblue.com/v3/contacts/{email}"

# Endpoint Shopify GraphQL
SHOPIFY_GRAPHQL_URL = f"https://{SHOPIFY_STORE}/admin/api/2023-10/graphql.json"

# 📌 Función para obtener la URL pública de un archivo (MediaImage o GenericFile)
def get_public_file_url(gid):
    if not gid:
        return None
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }

    # 1) Intento como MediaImage
    query_image = {
        "query": f"""
            query {{
              node(id: "{gid}") {{
                ... on MediaImage {{
                  image {{
                    url
                  }}
                }}
              }}
            }}
        """
    }
    try:
        response_image = requests.post(SHOPIFY_GRAPHQL_URL, headers=headers, json=query_image, verify=False)
        response_image.raise_for_status()
        data_image = response_image.json()
        if (data_image and data_image.get("data") and data_image["data"].get("node")
                and data_image["data"]["node"].get("image")
                and data_image["data"]["node"]["image"].get("url")):
            return data_image["data"]["node"]["image"]["url"]
    except requests.exceptions.RequestException as e:
        print(f"⚠️ Error al consultar como MediaImage para GID {gid}: {e}")

    # 2) Intento como GenericFile
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
        response_file = requests.post(SHOPIFY_GRAPHQL_URL, headers=headers, json=query_file, verify=False)
        response_file.raise_for_status()
        data_file = response_file.json()
        if data_file and data_file.get("data") and data_file["data"].get("node") and data_file["data"]["node"].get("url"):
            return data_file["data"]["node"]["url"]
        else:
            print(f"⚠️ No se encontró URL pública como GenericFile para GID {gid}. Respuesta: {data_file}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"⚠️ Error al consultar como GenericFile para GID {gid}: {e}")
        return None

    return None

# 📌 Metacampos del cliente desde Shopify (REST)
def get_customer_metafields(customer_id):
    shopify_url = f"https://{SHOPIFY_STORE}/admin/api/2023-10/customers/{customer_id}/metafields.json"
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    try:
        response = requests.get(shopify_url, headers=headers, verify=False)
        response.raise_for_status()
        metafields = response.json().get("metafields", [])
        modelo = next((m["value"] for m in metafields if m["key"] == "modelo"), "Sin modelo")
        precio = next((m["value"] for m in metafields if m["key"] == "precio"), "Sin precio")
        describe_lo_que_quieres = next((m["value"] for m in metafields if m["key"] == "describe_lo_que_quieres"), "Sin descripción")
        tengo_un_plano_gid = next((m["value"] for m in metafields if m["key"] == "tengo_un_plano"), None)
        tu_direccin_actual = next((m["value"] for m in metafields if m["key"] == "tu_direccin_actual"), "Sin dirección")
        indica_tu_presupuesto = next((m["value"] for m in metafields if m["key"] == "indica_tu_presupuesto"), "Sin presupuesto")
        tipo_de_persona = next((m["value"] for m in metafields if m["key"] == "tipo_de_persona"), "Sin persona")

        # URL pública del plano si hay GID
        tengo_un_plano_url = get_public_file_url(tengo_un_plano_gid) if tengo_un_plano_gid else "Sin plano"

        return (modelo, precio, describe_lo_que_quieres, tengo_un_plano_url,
                tu_direccin_actual, indica_tu_presupuesto, tipo_de_persona)
    except requests.exceptions.RequestException as e:
        print("❌ Error obteniendo metacampos de Shopify:", e)
        return "Error", "Error", "Error", "Error", "Error", "Error", "Error"

# 📩 Webhook que recibe Shopify
@app.route('/webhook/shopify', methods=['POST'])
def receive_webhook():
    try:
        raw_data = request.data.decode('utf-8')
        print("📩 Webhook recibido (RAW):", raw_data)

        data = request.get_json(silent=True)
        if not data:
            print("❌ ERROR: No se pudo interpretar el JSON correctamente.")
            return jsonify({"error": "Webhook sin JSON válido"}), 400

        print("📩 Webhook recibido de Shopify (JSON):", json.dumps(data, indent=4))

        # Datos básicos
        customer_id = data.get("id")
        email = (data.get("email") or "").strip().lower()
        first_name = data.get("first_name", "")
        last_name = data.get("last_name", "")
        phone = data.get("phone", "")

        if not email or not customer_id:
            print("❌ ERROR: No se recibió un email o ID de cliente válido.")
            return jsonify({"error": "Falta email o ID de cliente"}), 400

        # 🔍 Metacampos desde Shopify
        (modelo, precio, describe_lo_que_quieres, tengo_un_plano,
         tu_direccin_actual, indica_tu_presupuesto, tipo_de_persona) = get_customer_metafields(customer_id)

        print("Valores de metacampos:", modelo, precio, describe_lo_que_quieres, tengo_un_plano,
              tu_direccin_actual, indica_tu_presupuesto, tipo_de_persona)

        # 🎯 Atributos comunes para Brevo
        attributes = {
            "NOMBRE": first_name,
            "APELLIDOS": last_name,
            "TELEFONO_WHATSAPP": phone,
            "WHATSAPP": phone,
            "SMS": phone,
            "LANDLINE_NUMBER": phone,
            "MODELO_CABANA": modelo,
            "PRECIO_CABANA": precio,
            "DESCRIPCION_CLIENTE": describe_lo_que_quieres,
            "PLANO_CLIENTE": tengo_un_plano,   # URL pública o "Sin plano"
            "DIRECCION_CLIENTE": tu_direccin_actual,
            "PRESUPUESTO_CLIENTE": indica_tu_presupuesto,
            "TIPO_DE_PERSONA": tipo_de_persona
        }

        headers = {
            "api-key": BREVO_API_KEY,
            "Content-Type": "application/json"
        }

        # 📌 Verificar si el contacto existe
        response = requests.get(BREVO_GET_CONTACT_API_URL.format(email=email), headers=headers)

        if response.status_code == 200:
            # 🔁 Actualizar contacto existente
            print(f"⚠️ El contacto {email} ya existe en Brevo. Se actualizará.")
            update_payload = {
                "attributes": attributes
            }
            # Agregar a la lista automáticamente si BREVO_LIST_ID > 0
            if BREVO_LIST_ID > 0:
                update_payload["listIds"] = [BREVO_LIST_ID]
                update_payload["unlinkListIds"] = []  # no quitar de otras listas

            update_response = requests.put(
                BREVO_GET_CONTACT_API_URL.format(email=email),
                json=update_payload,
                headers=headers
            )

            if update_response.status_code == 200:
                return jsonify({"message": "Contacto actualizado en Brevo"}), 200
            else:
                return jsonify({"error": "No se pudo actualizar el contacto en Brevo",
                                "details": update_response.text}), 400

        elif response.status_code == 404:
            # 🆕 Crear contacto nuevo
            print(f"✅ El contacto {email} no existe. Se creará uno nuevo.")
            contact_data = {
                "email": email,
                "attributes": attributes
            }
            # Agregar a la lista automáticamente si BREVO_LIST_ID > 0
            if BREVO_LIST_ID > 0:
                contact_data["listIds"] = [BREVO_LIST_ID]

            create_response = requests.post(BREVO_API_URL, json=contact_data, headers=headers)

            if create_response.status_code == 201:
                return jsonify({"message": "Contacto creado en Brevo con metacampos"}), 201
            else:
                return jsonify({"error": "No se pudo crear el contacto en Brevo",
                                "details": create_response.text}), 400

        else:
            return jsonify({"error": "Error al verificar si el contacto existe",
                            "details": response.text}), 400

    except Exception as e:
        print("❌ ERROR procesando el webhook:", str(e))
        return jsonify({"error": "Error interno"}), 500

# 🔥 Iniciar el servidor (Render)
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
