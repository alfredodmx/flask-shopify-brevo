import requests
import json
import os

# Obtener claves API de las variables de entorno
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
SHOPIFY_STORE = "uaua8v-s7.myshopify.com"

# URL de API para crear un archivo en Shopify
SHOPIFY_API_URL = f"https://{SHOPIFY_STORE}/admin/api/2023-10/graphql.json"

def upload_image_to_shopify(image_filename):
    """
    Función para cargar la imagen a Shopify y obtener la URL pública.
    """
    # GraphQL mutation para crear una carga de archivos en Shopify
    query = """
    mutation stagedUploadsCreate($input: [StagedUploadInput!]!) {
        stagedUploadsCreate(input: $input) {
            stagedTargets {
                resourceUrl
                url
                parameters {
                    name
                    value
                }
            }
            userErrors {
                field
                message
            }
        }
    }
    """

    # Variables para la mutación, especificando el archivo
    variables = {
        "input": [{
            "filename": image_filename,
            "httpMethod": "POST",
            "mimeType": "image/jpeg",
            "resource": "FILE",  # Asegúrate de usar FILE y no IMAGE
        }]
    }

    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }

    # Realizar la solicitud de carga
    response = requests.post(SHOPIFY_API_URL, json={'query': query, 'variables': variables}, headers=headers)
    response_data = response.json()

    # Revisamos la respuesta para obtener el URL de la imagen
    if response.status_code == 200 and "data" in response_data:
        staged_target = response_data["data"]["stagedUploadsCreate"]["stagedTargets"][0]
        if staged_target:
            # Extracción de los parámetros necesarios para la subida
            params = staged_target["parameters"]
            url = staged_target["url"]
            resource_url = staged_target["resourceUrl"]
            return url, params, resource_url
        else:
            return None, None, None
    else:
        print(f"❌ Error al crear el upload de archivo: {response_data}")
        return None, None, None


def post_file_to_s3_and_get_url(image_filename):
    """
    Carga el archivo a Shopify S3 y retorna la URL pública de la imagen.
    """
    url, params, resource_url = upload_image_to_shopify(image_filename)

    if not url:
        return "Sin URL"

    # Preparar para subir el archivo a S3
    form = {
        'file': open(image_filename, 'rb')
    }

    headers = {param["name"]: param["value"] for param in params}
    
    # Subir archivo a S3
    upload_response = requests.post(url, files=form, headers=headers)

    if upload_response.status_code == 200:
        # Retornar la URL pública accesible de Shopify
        print(f"🔍 URL pública de la imagen: {resource_url}")
        return resource_url
    else:
        print(f"❌ Error al subir el archivo a S3: {upload_response.text}")
        return "Sin URL"


# Ejemplo de cómo obtener el metacampo y procesar la imagen
def process_shopify_metafields(customer_id):
    shopify_url = f"https://{SHOPIFY_STORE}/admin/api/2023-10/customers/{customer_id}/metafields.json"
    
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }

    response = requests.get(shopify_url, headers=headers)
    
    if response.status_code == 200:
        metafields = response.json().get("metafields", [])
        # Obtener el nombre del archivo del metacampo 'tengo_un_plano'
        plano_metafield = next((m for m in metafields if m["key"] == "tengo_un_plano"), None)
        if plano_metafield and "value" in plano_metafield:
            image_filename = plano_metafield["value"]
            print(f"🔍 Nombre del archivo: {image_filename}")
            return post_file_to_s3_and_get_url(image_filename)  # Obtener la URL pública
        else:
            print("❌ No se encontró el archivo en el metacampo")
            return "Sin URL"
    else:
        print(f"❌ Error obteniendo los metacamps: {response.text}")
        return "Sin URL"
