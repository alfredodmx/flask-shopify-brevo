from flask import Flask, request, jsonify
import requests
import json
import os

app = Flask(__name__)

# ... (tu configuración y variables de entorno) ...

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
        "query": """
            query {
              node(id: "%s") {
                ... on MediaImage {
                  image {
                    url
                  }
                }
              }
            }
        """ % gid
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
        "query": """
            query {
              node(id: "%s") {
                ... on GenericFile {
                  url
                }
              }
            }
        """ % gid
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

    return None # Si no se encontró URL pública de ninguna manera

# 📌 Función para obtener los metacampos de un cliente en Shopify (modificada para usar get_public_file_url)
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

        # Intenta obtener la URL pública del archivo (imagen o no)
        tengo_un_plano_url = get_public_file_url(tengo_un_plano_gid) if tengo_un_plano_gid else "Sin archivo"

        return modelo, precio, describe_lo_que_quieres, tengo_un_plano_url, tu_direccin_actual, indica_tu_presupuesto, tipo_de_persona
    except requests.exceptions.RequestException as e:
        print("❌ Error obteniendo metacampos de Shopify:", e)
        return "Error", "Error", "Error", "Error", "Error", "Error", "Error"

# ... (tu función receive_webhook y el resto del código sin cambios importantes) ...

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
