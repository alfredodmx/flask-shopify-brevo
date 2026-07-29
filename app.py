from flask import Flask, request, jsonify
import requests
import json
import os
import uuid                                               # NUEVO (para el CRM)
from datetime import datetime, timezone, timedelta        # NUEVO (para el CRM)

app = Flask(__name__)

# 🔑 Obtener API Key de Brevo y Shopify desde variables de entorno
BREVO_API_KEY = os.getenv("BREVO_API_KEY")
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
SHOPIFY_STORE = "uaua8v-s7.myshopify.com"  # Reemplaza con tu dominio real de Shopify

# NUEVO: credenciales del CRM (Supabase) — las agregas en Render (Environment)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

# Diagnóstico de ARRANQUE: ¿el proceso ve las variables del CRM? (enmascarado)
print("🔧 CRM config -> SUPABASE_URL: {} | SUPABASE_SERVICE_KEY: {}".format(
    (SUPABASE_URL[:28] + "…") if SUPABASE_URL else "❌ FALTA",
    (SUPABASE_SERVICE_KEY[:12] + "…") if SUPABASE_SERVICE_KEY else "❌ FALTA",
), flush=True)

if not BREVO_API_KEY or not SHOPIFY_ACCESS_TOKEN:
    print("❌ ERROR: Las API Keys no están configuradas. Asegúrate de definir 'BREVO_API_KEY' y 'SHOPIFY_ACCESS_TOKEN'.")
    exit(1)

# Endpoint de la API de Brevo para agregar un nuevo contacto
BREVO_API_URL = "https://api.sendinblue.com/v3/contacts"
BREVO_GET_CONTACT_API_URL = "https://api.sendinblue.com/v3/contacts/{email}"

# Endpoint de la API GraphQL de Shopify
SHOPIFY_GRAPHQL_URL = f"https://{SHOPIFY_STORE}/admin/api/2023-10/graphql.json"

# 📌 Función para obtener la URL pública de un archivo (intenta con MediaImage y luego GenericFile)
def get_public_file_url(gid):
    if not gid:
        return None
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }

    # Intenta primero como MediaImage
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
        if data_image and data_image.get("data") and data_image["data"].get("node") and data_image["data"]["node"].get("image") and data_image["data"]["node"]["image"].get("url"):
            return data_image["data"]["node"]["image"]["url"]
    except requests.exceptions.RequestException as e:
        print(f"⚠️ Error al consultar como MediaImage para GID {gid}: {e}")

    # Si no se encontró como MediaImage, intenta como GenericFile
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

# 📌 Función para obtener los metacampos de un cliente en Shopify
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

        # Obtener la URL pública del plano si el GID existe
        tengo_un_plano_url = get_public_file_url(tengo_un_plano_gid) if tengo_un_plano_gid else "Sin plano"

        return modelo, precio, describe_lo_que_quieres, tengo_un_plano_url, tu_direccin_actual, indica_tu_presupuesto, tipo_de_persona
    except requests.exceptions.RequestException as e:
        print("❌ Error obteniendo metacampos de Shopify:", e)
        return "Error", "Error", "Error", "Error", "Error", "Error", "Error"

# ============================================================================
# NUEVO: escribe el lead también en el CRM (Supabase). Aditivo, no toca Brevo.
# best-effort: si algo falla, NO rompe el webhook. Loguea el error exacto.
# ============================================================================
def enviar_a_crm(email, first_name, last_name, phone,
                 modelo, precio, describe, plano_url, direccion,
                 presupuesto, tipo_persona):
    if not (SUPABASE_URL and SUPABASE_SERVICE_KEY and email):
        print("⚠️ CRM: faltan SUPABASE_URL / SUPABASE_SERVICE_KEY o email; se omite el CRM.", flush=True)
        return
    hdr = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }
    now = datetime.now(timezone(timedelta(hours=-3))).isoformat()
    nombre = (f"{first_name or ''} {last_name or ''}").strip() or email
    tp = (tipo_persona or "").lower()
    tipo = "empresa" if ("empresa" in tp or "jur" in tp) else "natural"

    def _limpio(v):
        v = str(v or "").strip()
        return "" if v.lower().startswith(("sin ", "error")) else v

    meta = {
        "modelo": _limpio(modelo),
        "precio": _limpio(precio),
        "descripcion": _limpio(describe),
        "presupuesto": _limpio(presupuesto),
        "tipo_persona": _limpio(tipo_persona),
        "plano_url": plano_url if (plano_url and str(plano_url).startswith("http")) else "",
    }
    base = {
        "nombre": nombre,
        "email": email,
        "telefono": phone or "",
        "direccion": _limpio(direccion),
        "tipo": tipo,
        "origen": "Shopify",
        "shopify_meta": meta,
        "fecha_modificacion": now,
    }
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/clientes", headers=hdr,
                         params={"email": f"eq.{email}", "select": "id", "limit": 1}, timeout=15)
        if not r.ok:
            print(f"⚠️ CRM: la búsqueda del lead falló ({r.status_code}): {r.text[:200]}", flush=True)
            return
        rows = r.json() or []
        if rows:  # ya existe → actualiza sus datos (no toca su etapa ni su baja)
            resp = requests.patch(f"{SUPABASE_URL}/rest/v1/clientes",
                                  headers={**hdr, "Prefer": "return=minimal"},
                                  params={"id": f"eq.{rows[0]['id']}"}, json=base, timeout=15)
            if resp.ok:
                print(f"✅ CRM: lead {email} actualizado", flush=True)
            else:
                print(f"⚠️ CRM: no se pudo actualizar ({resp.status_code}): {resp.text[:200]}", flush=True)
        else:    # nuevo → cae en la Bandeja como lead
            base.update(id=str(uuid.uuid4()), activo=True,
                        etapa_manual="lead_nuevo", fecha_creacion=now)
            resp = requests.post(f"{SUPABASE_URL}/rest/v1/clientes",
                                 headers={**hdr, "Prefer": "return=minimal"}, json=base, timeout=15)
            if resp.ok:
                print(f"✅ CRM: lead {email} creado", flush=True)
            else:
                print(f"⚠️ CRM: no se pudo crear ({resp.status_code}): {resp.text[:200]}", flush=True)
    except Exception as e:
        print("⚠️ CRM upsert error:", e, flush=True)

# 🩺 Endpoint TEMPORAL de diagnóstico del CRM (quítalo cuando ya funcione).
# Muestra, SIN exponer secretos, el largo/forma real de las variables dentro de
# Render y la respuesta cruda de Supabase (GET + un insert/delete de prueba).
@app.route('/crm-diag')
def crm_diag():
    u = SUPABASE_URL or ""
    k = SUPABASE_SERVICE_KEY or ""
    info = {
        "url_len": len(u),
        "url_tail_repr": repr(u[-14:]),          # revela espacios / '/' / \n al final
        "key_len": len(k),
        "key_head_repr": repr(k[:12]),
        "key_tail_repr": repr(k[-4:]),
        "referencia_correcta": {"url_len": 40, "key_len": 41},
    }
    try:
        h = {"apikey": k, "Authorization": f"Bearer {k}", "Content-Type": "application/json"}
        rr = requests.get(f"{u}/rest/v1/clientes", headers=h,
                          params={"select": "id", "limit": 1}, timeout=15)
        info["GET_status"] = rr.status_code
        info["GET_body"] = rr.text[:250]
        if rr.ok:
            tid = str(uuid.uuid4())
            payload = {"id": tid, "email": f"diag-{tid[:8]}@espaciotest.local",
                       "nombre": "DIAG", "origen": "Shopify", "activo": True,
                       "etapa_manual": "lead_nuevo"}
            ins = requests.post(f"{u}/rest/v1/clientes",
                                headers={**h, "Prefer": "return=minimal"}, json=payload, timeout=15)
            info["INSERT_status"] = ins.status_code
            info["INSERT_body"] = ins.text[:250]
            requests.delete(f"{u}/rest/v1/clientes", headers=h,
                            params={"id": f"eq.{tid}"}, timeout=15)
            info["cleanup"] = "fila de prueba borrada"
    except Exception as e:
        info["EXCEPTION"] = repr(e)
    return jsonify(info)


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

        # NUEVO: además de Brevo, mandar el lead al CRM (Supabase) en tiempo real
        enviar_a_crm(email, first_name, last_name, phone,
                     modelo, precio, describe_lo_que_quieres, tengo_un_plano,
                     tu_direccin_actual, indica_tu_presupuesto, tipo_de_persona)

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
                    "PLANO_CLIENTE": tengo_un_plano,  # Ahora debería ser la URL pública de cualquier archivo
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
                    "PLANO_CLIENTE": tengo_un_plano,  # Ahora debería ser la URL pública de cualquier archivo
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
