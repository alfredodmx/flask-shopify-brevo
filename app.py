from flask import Flask, request, jsonify
import requests
import json
import os

app = Flask(__name__)

# =========================
#  Configuración principal
# =========================
# Brevo / Shopify
BREVO_API_KEY = os.getenv("BREVO_API_KEY")
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
SHOPIFY_STORE = "uaua8v-s7.myshopify.com"  # tu dominio real de Shopify
BREVO_LIST_ID = int(os.getenv("BREVO_LIST_ID", "0"))     # p.ej. 7

if not BREVO_API_KEY or not SHOPIFY_ACCESS_TOKEN:
    print("❌ ERROR: Falta BREVO_API_KEY o SHOPIFY_ACCESS_TOKEN.")
    raise SystemExit(1)

# Endpoints Brevo (marketing y transaccional)
BREVO_CONTACTS_URL = "https://api.brevo.com/v3/contacts"
BREVO_GET_CONTACT_URL = "https://api.brevo.com/v3/contacts/{email}"
BREVO_SEND_EMAIL_URL = "https://api.brevo.com/v3/smtp/email"   # transaccional

# Endpoint Shopify GraphQL
SHOPIFY_GRAPHQL_URL = f"https://{SHOPIFY_STORE}/admin/api/2023-10/graphql.json"


# ======================================
#  Notificación interna por Brevo (API)
# ======================================
# Usa un remitente VERIFICADO en Brevo (el mismo que ya usas)
ALERT_FROM_EMAIL = os.getenv("ALERT_FROM_EMAIL", "")    # ej. root@espaciocontainerhouse.cl (verificado en Brevo)
ALERT_FROM_NAME  = os.getenv("ALERT_FROM_NAME", "Leads")
ALERT_TO         = os.getenv("ALERT_TO", "")            # "correo1@...,correo2@..."

def send_internal_alert_via_brevo(created: bool, email: str, attrs: dict):
    """Envía notificación interna usando el endpoint transaccional de Brevo (HTTPS).
       Evita SMTP y puertos bloqueados en Render."""
    if not ALERT_FROM_EMAIL or not ALERT_TO:
        print("⚠️ Alerta Brevo: faltan ALERT_FROM_EMAIL o ALERT_TO; omito envío.")
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
    <p>Fuente: Shopify → Render → Brevo (Lista #{BREVO_LIST_ID})</p>
    """

    to_list = [{"email": t.strip()} for t in ALERT_TO.split(",") if t.strip()]
    if not to_list:
        print("⚠️ Alerta Brevo: ALERT_TO vacío; omito envío.")
        return

    headers = {
        "api-key": BREVO_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "sender": {"email": ALERT_FROM_EMAIL, "name": ALERT_FROM_NAME},
        "to": to_list,
        "subject": subject,
        "htmlContent": html
    }
    try:
        print(f"➡️ Enviando alerta por Brevo API a { [t['email'] for t in to_list] }")
        r = requests.post(BREVO_SEND_EMAIL_URL, headers=headers, json=payload, timeout=20)
        if r.status_code in (200, 201, 202):
            print("📧 Alerta transaccional enviada (Brevo).")
        else:
            print(f"❌ Error Brevo transaccional: {r.status_code} {r.text}")
    except Exception as e:
        print("❌ Excepción enviando alerta (Brevo API):", str(e))


# =========================
#  Helpers Shopify
# =========================
def get_public_file_url(gid):
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
        ri = requests.post(SHOPIFY_GRAPHQL_URL, headers=headers, json=query_image, verify=False, timeout=15)
        ri.raise_for_status()
        di = ri.json()
        if (di and di.get("data") and di["data"].get("node")
                and di["data"]["node"].get("image")
                and di["data"]["node"]["image"].get("url")):
            return di["data"]["node"]["image"]["url"]
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
        rf = requests.post(SHOPIFY_GRAPHQL_URL, headers=headers, json=query_file, verify=False, timeout=15)
        rf.raise_for_status()
        df = rf.json()
        if df and df.get("data") and df["data"].get("node") and df["data"]["node"].get("url"):
            return df["data"]["node"]["url"]
        else:
            print(f"⚠️ No URL pública como GenericFile para GID {gid}. Respuesta: {df}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"⚠️ GenericFile GID {gid}: {e}")
        return None

    return None


def get_customer_metafields(customer_id):
    shopify_url = f"https://{SHOPIFY_STORE}/admin/api/2023-10/customers/{customer_id}/metafields.json"
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    try:
        r = requests.get(shopify_url, headers=headers, verify=False, timeout=15)
        r.raise_for_status()
        metafields = r.json().get("metafields", [])
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
#  Webhook principal
# =========================
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

        # ¿Existe contacto en Brevo?
        response = requests.get(BREVO_GET_CONTACT_URL.format(email=email), headers=headers, timeout=15)

        if response.status_code == 200:
            print(f"⚠️ {email} ya existe en Brevo. Se actualizará.")
            update_payload = {"attributes": attributes}
            if BREVO_LIST_ID > 0:
                update_payload["listIds"] = [BREVO_LIST_ID]
                update_payload["unlinkListIds"] = []

            update_response = requests.put(
                BREVO_GET_CONTACT_URL.format(email=email),
                json=update_payload,
                headers=headers,
                timeout=15
            )

            # (Opcional) enviar alerta también cuando se actualiza
            send_internal_alert_via_brevo(created=False, email=email, attrs=attributes)

            if update_response.status_code in (200, 204):  # Brevo a veces devuelve 204 sin body
                print(f"✅ Brevo update OK: {update_response.status_code}")
                return jsonify({"message": "Contacto actualizado en Brevo"}), 200
            else:
                print(f"❌ Brevo update ERROR: {update_response.status_code} {update_response.text}")
                return jsonify({"error": "No se pudo actualizar el contacto en Brevo",
                                "details": update_response.text}), 400

        elif response.status_code == 404:
            print(f"✅ {email} no existe. Se creará nuevo.")
            contact_data = {"email": email, "attributes": attributes}
            if BREVO_LIST_ID > 0:
                contact_data["listIds"] = [BREVO_LIST_ID]

            create_response = requests.post(BREVO_CONTACTS_URL, json=contact_data, headers=headers, timeout=15)

            # Enviar alerta al crear
            send_internal_alert_via_brevo(created=True, email=email, attrs=attributes)

            if create_response.status_code == 201:
                return jsonify({"message": "Contacto creado en Brevo con metacampos"}), 201
            else:
                print(f"❌ Brevo create ERROR: {create_response.status_code} {create_response.text}")
                return jsonify({"error": "No se pudo crear el contacto en Brevo",
                                "details": create_response.text}), 400
        else:
            return jsonify({"error": "Error al verificar si el contacto existe",
                            "details": response.text}), 400

    except Exception as e:
        print("❌ ERROR procesando el webhook:", str(e))
        return jsonify({"error": "Error interno"}), 500


# =========================
#  Endpoint de prueba
# =========================
@app.route('/debug/alert', methods=['POST', 'GET'])
def debug_alert():
    dummy_attrs = {
        "NOMBRE": "Test",
        "APELLIDOS": "Webhook",
        "TELEFONO_WHATSAPP": "+56 9 0000 0000",
        "MODELO_CABANA": "Demo",
        "PRECIO_CABANA": "$0",
        "DESCRIPCION_CLIENTE": "Esto es una prueba.",
        "PLANO_CLIENTE": "https://example.com/plano.jpg",
        "DIRECCION_CLIENTE": "Calle Falsa 123",
        "PRESUPUESTO_CLIENTE": "$1",
        "TIPO_DE_PERSONA": "Prueba"
    }
    send_internal_alert_via_brevo(True, "test@ejemplo.cl", dummy_attrs)
    return jsonify({"ok": True, "msg": "Prueba de alerta por Brevo enviada (revisa logs/casillas)."}), 200


# =========================
#  Server (Render)
# =========================
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
