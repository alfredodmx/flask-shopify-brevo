# app.py
from flask import Flask, request, jsonify
import requests
import json
import os
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr

app = Flask(__name__)

# =========================
#  SMTP (Zoho) - Alertas
# =========================
ALERT_SMTP_HOST = os.getenv("ALERT_SMTP_HOST")          # smtp.zoho.com (587) o smtppro.zoho.com (465)
ALERT_SMTP_PORT = int(os.getenv("ALERT_SMTP_PORT", "587"))
ALERT_SMTP_USER = os.getenv("ALERT_SMTP_USER")          # p.ej.: root@espaciocontainerhouse.cl
ALERT_SMTP_PASS = os.getenv("ALERT_SMTP_PASS")          # App Password de Zoho (no el nombre)
ALERT_FROM      = os.getenv("ALERT_FROM")               # mismo buzón que el USER o alias permitido
ALERT_TO        = os.getenv("ALERT_TO", "")             # "dest1@...,dest2@..."

def send_internal_alert(created: bool, email: str, attrs: dict):
    """
    Envía un correo interno vía SMTP (Zoho).
    Si el puerto es 465 se usa SSL directo (SMTP_SSL).
    Si el puerto es 587 se usa STARTTLS.
    """
    if not (ALERT_SMTP_HOST and ALERT_SMTP_USER and ALERT_SMTP_PASS and ALERT_FROM and ALERT_TO):
        print("⚠️ SMTP de alertas no configurado; omito envío.",
              ALERT_SMTP_HOST, ALERT_SMTP_USER, ALERT_FROM, ALERT_TO)
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
    <p>Fuente: Shopify → Render → Brevo (Lista #{os.getenv("BREVO_LIST_ID","?")})</p>
    """

    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr(("Leads", ALERT_FROM))
    to_list = [t.strip() for t in ALERT_TO.split(",") if t.strip()]
    msg["To"] = ", ".join(to_list)

    print(f"➡️ Intentando enviar alerta SMTP via {ALERT_SMTP_HOST}:{ALERT_SMTP_PORT} "
          f"como {ALERT_SMTP_USER} FROM {ALERT_FROM} TO {to_list}")

    try:
        if int(ALERT_SMTP_PORT) == 465:
            with smtplib.SMTP_SSL(ALERT_SMTP_HOST, int(ALERT_SMTP_PORT), timeout=20) as server:
                server.login(ALERT_SMTP_USER, ALERT_SMTP_PASS)
                server.sendmail(ALERT_FROM, to_list, msg.as_string())
        else:
            with smtplib.SMTP(ALERT_SMTP_HOST, int(ALERT_SMTP_PORT), timeout=20) as server:
                server.starttls()
                server.login(ALERT_SMTP_USER, ALERT_SMTP_PASS)
                server.sendmail(ALERT_FROM, to_list, msg.as_string())

        print(f"📧 Alerta enviada a: {to_list}")
    except Exception as e:
        print("❌ Error enviando alerta SMTP:", repr(e))


# =========================
#  Brevo / Shopify config
# =========================
BREVO_API_KEY = os.getenv("BREVO_API_KEY")
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
SHOPIFY_STORE = "uaua8v-s7.myshopify.com"  # tu dominio real de Shopify

# Lista de Brevo (ID 7 = LEADS ESPACIO CONTAINER HOUSE)
BREVO_LIST_ID = int(os.getenv("BREVO_LIST_ID", "0"))

if not BREVO_API_KEY or not SHOPIFY_ACCESS_TOKEN:
    print("❌ ERROR: Falta BREVO_API_KEY o SHOPIFY_ACCESS_TOKEN.")
    raise SystemExit(1)

# Endpoints Brevo
BREVO_API_URL = "https://api.sendinblue.com/v3/contacts"
BREVO_GET_CONTACT_API_URL = "https://api.sendinblue.com/v3/contacts/{email}"

# Endpoint Shopify GraphQL
SHOPIFY_GRAPHQL_URL = f"https://{SHOPIFY_STORE}/admin/api/2023-10/graphql.json"


# =========================
#  Helpers Shopify
# =========================
def get_public_file_url(gid):
    """Devuelve URL pública desde un GID de Shopify (MediaImage o GenericFile)."""
    if not gid:
        return None
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }

    # 1) MediaImage
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
        response_image = requests.post(
            SHOPIFY_GRAPHQL_URL, headers=headers, json=query_image, verify=False, timeout=15
        )
        response_image.raise_for_status()
        data_image = response_image.json()
        if (data_image and data_image.get("data") and data_image["data"].get("node")
                and data_image["data"]["node"].get("image")
                and data_image["data"]["node"]["image"].get("url")):
            return data_image["data"]["node"]["image"]["url"]
    except requests.exceptions.RequestException as e:
        print(f"⚠️ MediaImage GID {gid}: {e}")

    # 2) GenericFile
    query_file = {
        "query": f"""
            query {{
              node(id: "{gid}") {{
                ... on GenericFile {{ url }}
              }}
            }}
        """
    }
    try:
        response_file = requests.post(
            SHOPIFY_GRAPHQL_URL, headers=headers, json=query_file, verify=False, timeout=15
        )
        response_file.raise_for_status()
        data_file = response_file.json()
        if data_file and data_file.get("data") and data_file["data"].get("node") and data_file["data"]["node"].get("url"):
            return data_file["data"]["node"]["url"]
        else:
            print(f"⚠️ No URL pública como GenericFile para GID {gid}. Respuesta: {data_file}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"⚠️ GenericFile GID {gid}: {e}")
        return None


def get_customer_metafields(customer_id):
    """Obtiene metacampos REST del cliente en Shopify."""
    shopify_url = f"https://{SHOPIFY_STORE}/admin/api/2023-10/customers/{customer_id}/metafields.json"
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    try:
        response = requests.get(shopify_url, headers=headers, verify=False, timeout=15)
        response.raise_for_status()
        metafields = response.json().get("metafields", [])
        modelo = next((m["value"] for m in metafields if m["key"] == "modelo"), "Sin modelo")
        precio = next((m["value"] for m in metafields if m["key"] == "precio"), "Sin precio")
        describe_lo_que_quieres = next((m["value"] for m in metafields if m["key"] == "describe_lo_que_quieres"), "Sin descripción")
        tengo_un_plano_gid = next((m["value"] for m in metafields if m["key"] == "tengo_un_plano"), None)
        tu_direccin_actual = next((m["value"] for m in metafields if m["key"] == "tu_direccin_actual"), "Sin dirección")
        indica_tu_presupuesto = next((m["value"] for m in metafields if m["key"] == "indica_tu_presupuesto"), "Sin presupuesto")
        tipo_de_persona = next((m["value"] for m in metafields if m["key"] == "tipo_de_persona"), "Sin persona")

        tengo_un_plano_url = get_public_file_url(tengo_un_plano_gid) if tengo_un_plano_gid else "Sin plano"

        return (modelo, precio, describe_lo_que_quieres, tengo_un_plano_url,
                tu_direccin_actual, indica_tu_presupuesto, tipo_de_persona)
    except requests.exceptions.RequestException as e:
        print("❌ Error obteniendo metacampos de Shopify:", e)
        return "Error", "Error", "Error", "Error", "Error", "Error", "Error"


# =========================
#  Endpoints
# =========================
@app.route("/debug/smtp", methods=["POST"])
def debug_smtp():
    """Dispara un correo de prueba SMTP sin tocar Brevo/Shopify."""
    attrs = {
        "NOMBRE": "Test",
        "APELLIDOS": "SMTP",
        "TELEFONO_WHATSAPP": "",
        "MODELO_CABANA": "",
        "PRECIO_CABANA": "",
        "DESCRIPCION_CLIENTE": "Prueba directa SMTP desde Render",
        "PLANO_CLIENTE": "",
        "DIRECCION_CLIENTE": "",
        "PRESUPUESTO_CLIENTE": "",
        "TIPO_DE_PERSONA": ""
    }
    try:
        send_internal_alert(created=True, email="smtp-test@espaciocontainerhouse.cl", attrs=attrs)
        return jsonify({"ok": True, "msg": "SMTP test disparado. Revisa logs y casillas."}), 200
    except Exception as e:
        return jsonify({"ok": False, "err": str(e)}), 500


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
            print("❌ ERROR: Falta email o ID de cliente")
            return jsonify({"error": "Falta email o ID de cliente"}), 400

        # Metacampos Shopify
        (modelo, precio, describe_lo_que_quieres, tengo_un_plano,
         tu_direccin_actual, indica_tu_presupuesto, tipo_de_persona) = get_customer_metafields(customer_id)

        print("Metacampos:", modelo, precio, describe_lo_que_quieres, tengo_un_plano,
              tu_direccin_actual, indica_tu_presupuesto, tipo_de_persona)

        # Atributos para Brevo
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
            "PLANO_CLIENTE": tengo_un_plano,
            "DIRECCION_CLIENTE": tu_direccin_actual,
            "PRESUPUESTO_CLIENTE": indica_tu_presupuesto,
            "TIPO_DE_PERSONA": tipo_de_persona
        }

        headers = {"api-key": BREVO_API_KEY, "Content-Type": "application/json"}
        email_id = email  # si quisieras, aquí podrías URL-encodear

        # ¿Existe contacto en Brevo?
        response = requests.get(BREVO_GET_CONTACT_API_URL.format(email=email_id), headers=headers, timeout=15)

        if response.status_code == 200:
            print(f"⚠️ {email} ya existe en Brevo. Se actualizará.")
            update_payload = {"attributes": attributes}
            if BREVO_LIST_ID > 0:
                update_payload["listIds"] = [BREVO_LIST_ID]
                update_payload["unlinkListIds"] = []

            update_response = requests.put(
                BREVO_GET_CONTACT_API_URL.format(email=email_id),
                json=update_payload,
                headers=headers,
                timeout=15
            )

            if update_response.status_code in (200, 204):
                # 🔔 Enviamos alerta también en actualización (útil para depurar)
                send_internal_alert(created=False, email=email, attrs=attributes)
                return jsonify({"message": "Contacto actualizado en Brevo"}), 200
            else:
                print("❌ Brevo update ERROR:", update_response.status_code, update_response.text[:500])
                return jsonify({"error": "No se pudo actualizar el contacto en Brevo",
                                "details": update_response.text}), 400

        elif response.status_code == 404:
            print(f"✅ {email} no existe. Se creará nuevo.")
            contact_data = {"email": email, "attributes": attributes}
            if BREVO_LIST_ID > 0:
                contact_data["listIds"] = [BREVO_LIST_ID]

            create_response = requests.post(BREVO_API_URL, json=contact_data, headers=headers, timeout=15)

            if create_response.status_code == 201:
                # 🔔 AVISO por Zoho al crear
                send_internal_alert(created=True, email=email, attrs=attributes)
                return jsonify({"message": "Contacto creado en Brevo con metacampos"}), 201
            else:
                print("❌ Brevo create ERROR:", create_response.status_code, create_response.text[:500])
                return jsonify({"error": "No se pudo crear el contacto en Brevo",
                                "details": create_response.text}), 400
        else:
            print("❌ Brevo check ERROR:", response.status_code, response.text[:500])
            return jsonify({"error": "Error al verificar si el contacto existe",
                            "details": response.text}), 400

    except Exception as e:
        print("❌ ERROR procesando el webhook:", str(e))
        return jsonify({"error": "Error interno"}), 500


# =========================
#  Server (Render)
# =========================
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
