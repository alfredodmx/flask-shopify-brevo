# app.py
from flask import Flask, request, jsonify
import os, json, re, logging
import requests

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("app")

# ====== ENV obligatorios (Render) ======
BREVO_API_KEY        = os.getenv("BREVO_API_KEY")            # API key HTTP de Brevo
BREVO_SENDER         = os.getenv("BREVO_SENDER")             # p.ej. info@espaciocontainerhouse.cl (verificado)
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
SHOPIFY_STORE        = os.getenv("SHOPIFY_STORE")            # p.ej. uaua8v-s7.myshopify.com
NOTIFY_EMAILS        = os.getenv("NOTIFY_EMAILS", "")        # comas: "a@a.com,b@b.com,c@c.com"

if not BREVO_API_KEY or not BREVO_SENDER or not SHOPIFY_ACCESS_TOKEN or not SHOPIFY_STORE:
    log.error("ENV faltantes: BREVO_API_KEY, BREVO_SENDER, SHOPIFY_ACCESS_TOKEN, SHOPIFY_STORE son requeridos.")
    # No forzamos exit para poder ver el / de vida, pero fallarán los endpoints que los necesitan.

# ====== Constantes de API ======
BREVO_CONTACT_GET     = "https://api.brevo.com/v3/contacts/{email}"
BREVO_CONTACT_CREATE  = "https://api.brevo.com/v3/contacts"
BREVO_SEND_EMAIL      = "https://api.brevo.com/v3/smtp/email"
SHOPIFY_GRAPHQL_URL   = f"https://{SHOPIFY_STORE}/admin/api/2023-10/graphql.json"
SHOPIFY_METAFIELDS_REST = "https://{store}/admin/api/2023-10/customers/{cid}/metafields.json"

# ====== Utilidades ======
def split_emails(csv: str):
    return [e.strip() for e in csv.split(",") if e.strip()]

def brevo_headers():
    return {
        "api-key": BREVO_API_KEY,
        "accept": "application/json",
        "content-type": "application/json",
    }

def normalize_phone_cl(raw: str) -> str | None:
    """
    Normaliza teléfonos chilenos a E.164 (+56).
    Acepta entradas con espacios, guiones, ( ), etc.
    Regresa None si no es posible normalizar.
    Reglas simples:
      - Quitar todo excepto dígitos.
      - Quitar 0 inicial si lo hay (discado nacional).
      - Si empieza con 56 y tiene 11 dígitos totales: ok -> +{num}
      - Si tiene 9 dígitos (móvil típico 9xxxxxxxx): prefijar +56
      - Si tiene 8 dígitos (línea fija en algunas zonas): prefijar +56 y un 2? (muy variable). Mejor rechazar.
    """
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)

    # +56XXXXXXXXX -> a veces llega ya con 56
    if digits.startswith("56"):
        # móviles chilenos suelen ser 9 + 8 dígitos = 9 + 8 = 9 y con 56 delante => 11 total
        if len(digits) == 11:
            return f"+{digits}"
        else:
            return None

    # Quitar 0 nacional si aplica
    if digits.startswith("0"):
        digits = digits[1:]

    # 9 dígitos → móvil moderno (9xxxxxxxx)
    if len(digits) == 9:
        return f"+56{digits}"

    # 8 dígitos (posible fijo sin prefijo de ciudad) → incierto, mejor no cargar
    return None

def safe_attrs_with_phone(attrs: dict, phone_value: str | None) -> dict:
    """ Construye atributos para Brevo, con/tel si válido, sino los omite. """
    # Atributos base (ajusta claves a tus atributos existentes en Brevo)
    out = {
        "NOMBRE": attrs.get("NOMBRE"),
        "APELLIDOS": attrs.get("APELLIDOS"),
        "MODELO_CABANA": attrs.get("MODELO_CABANA"),
        "PRECIO_CABANA": attrs.get("PRECIO_CABANA"),
        "DESCRIPCION_CLIENTE": attrs.get("DESCRIPCION_CLIENTE"),
        "PLANO_CLIENTE": attrs.get("PLANO_CLIENTE"),
        "DIRECCION_CLIENTE": attrs.get("DIRECCION_CLIENTE"),
        "PRESUPUESTO_CLIENTE": attrs.get("PRESUPUESTO_CLIENTE"),
        "TIPO_DE_PERSONA": attrs.get("TIPO_DE_PERSONA"),
    }
    if phone_value:
        out["TELEFONO_WHATSAPP"] = phone_value
        out["WHATSAPP"]          = phone_value
        out["SMS"]               = phone_value
        out["LANDLINE_NUMBER"]   = phone_value
    # limpia None (Brevo no obliga, pero evitamos ruido)
    return {k: v for k, v in out.items() if v not in (None, "", "Error")}

def upsert_brevo_contact(email: str, attrs: dict) -> tuple[int, dict]:
    """
    Upsert de contacto Brevo:
    1) GET: si 200 -> PUT (update), si 404 -> POST (create)
    2) Si 400 por 'Invalid phone number', reintenta sin campos de teléfono
    Retorna (status_code, body_json)
    """
    if not BREVO_API_KEY:
        return 500, {"error": "BREVO_API_KEY missing"}

    # Intento con teléfono normalizado (si existe)
    phone_raw = attrs.get("phone")
    phone_e164 = normalize_phone_cl(phone_raw) if phone_raw else None
    # Construir atributos
    base_attrs = {
        "NOMBRE": attrs.get("NOMBRE"),
        "APELLIDOS": attrs.get("APELLIDOS"),
        "MODELO_CABANA": attrs.get("MODELO_CABANA"),
        "PRECIO_CABANA": attrs.get("PRECIO_CABANA"),
        "DESCRIPCION_CLIENTE": attrs.get("DESCRIPCION_CLIENTE"),
        "PLANO_CLIENTE": attrs.get("PLANO_CLIENTE"),
        "DIRECCION_CLIENTE": attrs.get("DIRECCION_CLIENTE"),
        "PRESUPUESTO_CLIENTE": attrs.get("PRESUPUESTO_CLIENTE"),
        "TIPO_DE_PERSONA": attrs.get("TIPO_DE_PERSONA"),
    }

    def do_request(with_phone: bool):
        attributes = safe_attrs_with_phone(base_attrs, phone_e164 if with_phone else None)
        # GET (existe?)
        r = requests.get(BREVO_CONTACT_GET.format(email=email), headers=brevo_headers(), timeout=20)
        if r.status_code == 200:
            payload = {"email": email, "attributes": attributes}
            u = requests.put(BREVO_CONTACT_GET.format(email=email),
                             headers=brevo_headers(), json=payload, timeout=20)
            return u.status_code, (u.json() if u.headers.get("content-type","").startswith("application/json") else {"text": u.text})
        elif r.status_code == 404:
            payload = {"email": email, "attributes": attributes}
            c = requests.post(BREVO_CONTACT_CREATE, headers=brevo_headers(), json=payload, timeout=20)
            return c.status_code, (c.json() if c.headers.get("content-type","").startswith("application/json") else {"text": c.text})
        else:
            return r.status_code, (r.json() if r.headers.get("content-type","").startswith("application/json") else {"text": r.text})

    # 1er intento con teléfono (si fue normalizado)
    sc, body = do_request(with_phone=True)
    if sc == 400 and isinstance(body, dict) and "message" in body and "phone" in body["message"].lower():
        log.warning("Brevo 400 por teléfono. Reintentando sin atributos telefónicos…")
        sc, body = do_request(with_phone=False)
    return sc, body

def send_brevo_email(to_list, subject="Alerta", html="<p>Hola</p>", tags=None) -> tuple[int, dict]:
    """
    Envío transaccional Brevo por API HTTP (NO SMTP).
    to_list: lista de emails
    tags: lista de strings (opcional)
    """
    if not BREVO_API_KEY or not BREVO_SENDER:
        return 500, {"error": "BREVO_API_KEY o BREVO_SENDER missing"}

    payload = {
        "sender": {"email": BREVO_SENDER},
        "to": [{"email": e} for e in to_list],
        "subject": subject,
        "htmlContent": html,
    }
    if tags:
        payload["tags"] = tags

    r = requests.post(BREVO_SEND_EMAIL, headers=brevo_headers(), json=payload, timeout=25)
    try:
        body = r.json()
    except Exception:
        body = {"text": r.text}
    log.info("Brevo send status=%s body=%s", r.status_code, body)
    return r.status_code, body

# ====== Shopify helpers ======
def get_public_file_url(gid: str | None) -> str | None:
    if not gid:
        return None
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    # Primero intenta MediaImage
    q1 = {"query": f"""
        query {{
          node(id: "{gid}") {{
            ... on MediaImage {{
              image {{ url }}
            }}
          }}
        }}
    """}
    try:
        r1 = requests.post(SHOPIFY_GRAPHQL_URL, headers=headers, json=q1, timeout=25)
        r1.raise_for_status()
        j1 = r1.json()
        url = j1.get("data", {}).get("node", {}).get("image", {}).get("url")
        if url:
            return url
    except Exception as e:
        log.warning("MediaImage GID %s error: %s", gid, e)

    # Luego intenta GenericFile
    q2 = {"query": f"""
        query {{
          node(id: "{gid}") {{
            ... on GenericFile {{ url }}
          }}
        }}
    """}
    try:
        r2 = requests.post(SHOPIFY_GRAPHQL_URL, headers=headers, json=q2, timeout=25)
        r2.raise_for_status()
        j2 = r2.json()
        url2 = j2.get("data", {}).get("node", {}).get("url")
        if url2:
            return url2
        log.warning("Sin URL pública para GID %s. Respuesta: %s", gid, j2)
    except Exception as e:
        log.warning("GenericFile GID %s error: %s", gid, e)
    return None

def get_customer_metafields(customer_id: int):
    url = SHOPIFY_METAFIELDS_REST.format(store=SHOPIFY_STORE, cid=customer_id)
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    try:
        r = requests.get(url, headers=headers, timeout=25)
        r.raise_for_status()
        metafields = r.json().get("metafields", [])
        def get_val(key, default=None):
            m = next((x for x in metafields if x.get("key") == key), None)
            return m.get("value") if m else default

        modelo  = get_val("modelo", "Sin modelo")
        precio  = get_val("precio", "Sin precio")
        descr   = get_val("describe_lo_que_quieres", "Sin descripción")
        plano_gid = get_val("tengo_un_plano", None)
        plano_url = get_public_file_url(plano_gid) if plano_gid else None
        direccion = get_val("tu_direccin_actual", "Sin dirección")
        presupuesto = get_val("indica_tu_presupuesto", "Sin presupuesto")
        persona = get_val("tipo_de_persona", "Sin persona")

        return modelo, precio, descr, (plano_url or "Sin plano"), direccion, presupuesto, persona
    except Exception as e:
        log.error("Metafields error: %s", e)
        return "Error", "Error", "Error", "Error", "Error", "Error", "Error"

# ====== Endpoints ======
@app.route("/", methods=["GET"])
def root():
    return "OK", 200

@app.route("/debug/alert", methods=["POST"])
def debug_alert():
    """
    Prueba manual de envío por API:
    POST /debug/alert?to=a@a.com,b@b.com&subject=Hola
    Body opcional: {"html":"<p>Hola</p>","tags":["test"]}
    """
    to = split_emails(request.args.get("to") or NOTIFY_EMAILS)
    if not to:
        return jsonify({"ok": False, "error": "Sin destinatarios (query ?to=... o NOTIFY_EMAILS)"}), 400
    subject = request.args.get("subject") or "Alerta de prueba"
    body = request.get_json(silent=True) or {}
    html = body.get("html", "<p>Hola desde Render</p>")
    tags = body.get("tags")

    sc, br = send_brevo_email(to, subject=subject, html=html, tags=tags)
    return jsonify({"ok": sc in (200, 201, 202), "status": sc, "brevo": br, "to": to}), 200

@app.route("/webhook/brevo-events", methods=["POST"])
def brevo_events():
    try:
        data = request.get_json(force=True)
        log.info("📩 Brevo webhook event: %s", data)
        # Aquí podrías guardar en DB si quieres
        return "", 200
    except Exception as e:
        log.error("webhook Brevo error: %s", e)
        return "", 400

@app.route("/webhook/shopify", methods=["POST"])
def webhook_shopify():
    """
    Espera payload de cliente Shopify (ej: customers/create o update).
    Upsert en Brevo y notifica a NOTIFY_EMAILS.
    """
    try:
        raw = request.data.decode("utf-8", errors="ignore")
        log.info("📩 Shopify RAW: %s", raw[:1500])
        data = request.get_json(silent=True) or {}
        log.info("Shopify JSON: %s", json.dumps(data)[:1500])

        customer_id = data.get("id")
        email = data.get("email")
        first_name = data.get("first_name", "")
        last_name  = data.get("last_name", "")
        phone      = data.get("phone", "")

        if not email or not customer_id:
            return jsonify({"ok": False, "error": "Falta email o id de cliente"}), 400

        # Metacampos
        modelo, precio, descr, plano_url, direccion, presupuesto, persona = get_customer_metafields(customer_id)

        # Upsert Brevo
        attrs = {
            "NOMBRE": first_name,
            "APELLIDOS": last_name,
            "phone": phone,
            "MODELO_CABANA": modelo,
            "PRECIO_CABANA": precio,
            "DESCRIPCION_CLIENTE": descr,
            "PLANO_CLIENTE": plano_url,
            "DIRECCION_CLIENTE": direccion,
            "PRESUPUESTO_CLIENTE": presupuesto,
            "TIPO_DE_PERSONA": persona,
        }
        sc, body = upsert_brevo_contact(email, attrs)

        # Notificación por correo a múltiples destinatarios
        notify_to = split_emails(NOTIFY_EMAILS)
        subject = f"Nuevo/Actualizado cliente Shopify: {email}"
        html = f"""
        <h3>Cliente Shopify</h3>
        <ul>
          <li><b>Email:</b> {email}</li>
          <li><b>Nombre:</b> {first_name} {last_name}</li>
          <li><b>Teléfono bruto:</b> {phone}</li>
          <li><b>Teléfono normalizado:</b> {normalize_phone_cl(phone) or "(no válido)"}</li>
          <li><b>Modelo:</b> {modelo}</li>
          <li><b>Precio:</b> {precio}</li>
          <li><b>Descripción:</b> {descr}</li>
          <li><b>Plano URL:</b> {plano_url}</li>
          <li><b>Dirección:</b> {direccion}</li>
          <li><b>Presupuesto:</b> {presupuesto}</li>
          <li><b>Tipo de persona:</b> {persona}</li>
        </ul>
        <p>Status Brevo upsert: <code>{sc}</code></p>
        """
        # Enviar solo si hay destinatarios configurados
        if notify_to:
            send_brevo_email(notify_to, subject=subject, html=html, tags=["shopify-webhook"])

        # Devolvemos 202 para no bloquear reintentos de Shopify y adjuntamos el resultado de Brevo
        return jsonify({"ok": True, "brevo_status": sc, "brevo_body": body}), 202

    except Exception as e:
        log.error("❌ ERROR webhook_shopify: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "Error interno"}), 500

# ====== Main ======
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
