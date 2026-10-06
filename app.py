from flask import Flask, request, jsonify
import requests
import json
import os
import re                                                 # NUEVO (parseo de nota)
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

# Emails ROOT para las notificaciones (opcional, coma-separados). Los admin se
# detectan solos por su rol; agrega acá los root que NO tengan rol 'admin'.
CRM_ROOT_EMAILS = os.getenv("CRM_ROOT_EMAILS", "")

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

# ============================================================================
# NUEVO: cada formulario del sitio pone su propia ETIQUETA en el cliente de
# Shopify → el CRM sabe de QUÉ formulario vino el lead. Y el WhatsApp + el
# "¿Qué necesita?" viajan en la NOTA del cliente (Shopify no deja que el
# formulario público escriba el teléfono del cliente directo). Acá se leen ambos.
# ============================================================================
TAG_FORMULARIO = {
    "formulario cotiza": "Formulario Cotiza",                             # sección hero-cotiza.liquid
    "cotizacion-hero":   "Formulario Cotiza",                             # etiqueta anterior del hero (compat)
    "formulario modelo prediseñado": "Formulario Modelo Prediseñado",     # multistep-quote-form.liquid (producto)
    "cotizacion-multipaso": "Formulario Modelo Prediseñado",             # etiqueta anterior del multipaso (compat)
    "formulario personalizado": "Formulario Personalizado",              # container-configurator.liquid
    "configurador-container": "Formulario Personalizado",                # etiqueta anterior del configurador (compat)
}


def formulario_de_tags(tags):
    """Devuelve el nombre del formulario según las etiquetas (string coma-separado)
    del cliente de Shopify; '' si ninguna etiqueta está mapeada."""
    for t in str(tags or "").split(","):
        f = TAG_FORMULARIO.get(t.strip().lower())
        if f:
            return f
    return ""


def formulario_de_note(note):
    """Devuelve el formulario leído de la NOTA del cliente ('Formulario: FORMULARIO COTIZA
    · WhatsApp: ... · Interés: ...'). Shopify IGNORA la etiqueta (contact[tags]) desde el
    formulario público, así que el formulario viaja en la nota. Mapea al nombre bonito;
    si no está en el mapa, devuelve el crudo. '' si la nota no lo trae."""
    m = re.search(r"formulario\s*[:\-]\s*(.+?)(?:\s*·|\s*$)", str(note or ""), re.I)
    if not m:
        return ""
    crudo = m.group(1).strip()
    return TAG_FORMULARIO.get(crudo.lower(), crudo)


def tel_de_note(note):
    """Extrae el WhatsApp/teléfono que el formulario guarda en la NOTA del cliente
    ('WhatsApp: +56 9 ... · Interés: ...'). '' si no encuentra."""
    m = re.search(r"(?:whatsapp|tel[eé]fono|fono|celular)\s*[:\-]?\s*([+()\d][\d\s()\-+]{6,})",
                  str(note or ""), re.I)
    return m.group(1).strip() if m else ""


def interes_de_note(note):
    """Extrae el '¿Qué necesita?' (Interés) que el formulario guarda en la NOTA."""
    m = re.search(r"inter[eé]s\s*[:\-]\s*(.+?)(?:\s*·\s*whatsapp|\s*$)", str(note or ""), re.I | re.S)
    return m.group(1).strip() if m else ""


def parse_note(note):
    """Convierte la NOTA del cliente ('Etiqueta: valor · Etiqueta: valor · …') en un dict
    {etiqueta_en_minúsculas: valor}. Cada formulario del sitio arma su nota con estos pares
    separados por ' · '; así el CRM recibe todos los campos (modelo, valor, región, plazo,
    presupuesto, mensaje, etc.) sin depender de metacampos ni de la etiqueta de Shopify."""
    out = {}
    for parte in str(note or "").split("·"):
        if ":" in parte:
            k, v = parte.split(":", 1)
            k = k.strip().lower()
            if k and k not in out:
                out[k] = v.strip()
    return out


def _primero(d, *claves):
    """Primer valor no vacío entre varias posibles etiquetas del dict de la nota."""
    for k in claves:
        v = str(d.get(k) or "").strip()
        if v:
            return v
    return ""


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
# NUEVO: notificar a admin/root (campana del CRM) cuando cae un lead de Shopify.
# ============================================================================
def _destinatarios_admin():
    """Emails de admin/root a notificar: rol 'admin'/'root' del directorio de
    usuarios + CRM_ROOT_EMAILS. best-effort → set() si algo falla."""
    dests = set()
    for e in (CRM_ROOT_EMAILS or "").split(","):
        e = e.strip().lower()
        if e:
            dests.add(e)
    if not (SUPABASE_URL and SUPABASE_SERVICE_KEY):
        return dests
    try:
        hdr = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
        r = requests.get(f"{SUPABASE_URL}/auth/v1/admin/users", headers=hdr,
                         params={"per_page": 1000, "page": 1}, timeout=15)
        for u in ((r.json() or {}).get("users") or []):
            meta = u.get("user_metadata") or u.get("raw_user_meta_data") or {}
            if str(meta.get("rol", "")).strip().lower() in ("admin", "root"):
                em = (u.get("email") or "").strip().lower()
                if em:
                    dests.add(em)
    except Exception as e:
        print("⚠️ Notif: no pude leer usuarios:", e, flush=True)
    return dests


def notificar_lead_shopify(nombre, cliente_id):
    """Crea una notificación (campana del CRM) para cada admin/root ante un lead
    NUEVO de Shopify. best-effort: nunca rompe el webhook."""
    dests = _destinatarios_admin()
    if not (dests and SUPABASE_URL and SUPABASE_SERVICE_KEY):
        print("⚠️ Notif: sin destinatarios admin/root (¿CRM_ROOT_EMAILS?).", flush=True)
        return
    now = datetime.now(timezone(timedelta(hours=-3))).isoformat()
    filas = [{
        "id": str(uuid.uuid4()), "user_email": em, "tipo": "lead",
        "titulo": f"Nuevo lead de Shopify: {nombre}",
        "detalle": "Cayó en la Bandeja. Revísalo y asígnalo.",
        "cliente_id": cliente_id, "leido": False, "fecha": now,
    } for em in dests]
    try:
        hdr = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
               "Content-Type": "application/json"}
        resp = requests.post(f"{SUPABASE_URL}/rest/v1/notificaciones",
                             headers={**hdr, "Prefer": "return=minimal"}, json=filas, timeout=15)
        if resp.ok:
            print(f"✅ Notif: avisados {len(filas)} admin/root del lead {nombre}", flush=True)
        else:
            print(f"⚠️ Notif: no se pudo guardar ({resp.status_code}): {resp.text[:200]}", flush=True)
    except Exception as e:
        print("⚠️ Notif insert error:", e, flush=True)


# ============================================================================
# NUEVO: escribe el lead también en el CRM (Supabase). Aditivo, no toca Brevo.
# best-effort: si algo falla, NO rompe el webhook. Loguea el error exacto.
# Ahora también guarda: FORMULARIO (de las etiquetas), y TELÉFONO + INTERÉS
# (del campo phone o de la NOTA del cliente).
# ============================================================================
def enviar_a_crm(email, first_name, last_name, phone,
                 modelo, precio, describe, plano_url, direccion,
                 presupuesto, tipo_persona, tags="", note=""):
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

    # Todos los campos que el formulario deja en la NOTA (modelo, valor, región, plazo,
    # presupuesto, mensaje, whatsapp, interés, formulario). Shopify ignora contact[tags] y
    # el teléfono desde el formulario público → todo viaja en la nota.
    np = parse_note(note)

    # De qué FORMULARIO vino: 1º la etiqueta (por si Shopify la guarda), 2º la nota.
    formulario = formulario_de_tags(tags)
    if not formulario:
        _fn = _primero(np, "formulario")
        formulario = TAG_FORMULARIO.get(_fn.lower(), _fn)

    interes = _primero(np, "interés", "interes")
    telefono = (str(phone or "").strip()) or _primero(np, "whatsapp", "teléfono", "telefono", "fono", "celular")

    meta = {
        # Los metacampos (formulario Forms) tienen prioridad; si no, lo que trae la nota.
        "modelo": _limpio(modelo) or _primero(np, "modelo"),
        "precio": _limpio(precio) or _primero(np, "valor", "precio"),
        "descripcion": _limpio(describe) or _primero(np, "mensaje", "descripción", "descripcion"),
        "presupuesto": _limpio(presupuesto) or _primero(np, "presupuesto"),
        "region": _primero(np, "región", "region"),
        "plazo": _primero(np, "plazo", "plazo ideal"),
        # Configurador (FORMULARIO PERSONALIZADO):
        "modulo": _primero(np, "módulo", "modulo"),
        "puertas_ventanas": _primero(np, "puertas y ventanas", "puertas/ventanas", "puertas"),
        "revestimiento": _primero(np, "revestimiento"),
        "distribucion": _primero(np, "distribución", "distribucion"),
        "tipo_persona": _limpio(tipo_persona),
        "plano_url": plano_url if (plano_url and str(plano_url).startswith("http")) else "",
        "formulario": formulario,          # NUEVO: qué formulario del sitio generó el lead
        "interes": interes,                # NUEVO: "¿Qué necesita?" del formulario
    }
    base = {
        "nombre": nombre,
        "email": email,
        "telefono": telefono,
        "direccion": _limpio(direccion) or _primero(np, "dirección", "direccion"),
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
                print(f"✅ CRM: lead {email} actualizado (formulario='{formulario}')", flush=True)
            else:
                print(f"⚠️ CRM: no se pudo actualizar ({resp.status_code}): {resp.text[:200]}", flush=True)
        else:    # nuevo → cae en la Bandeja como lead
            base.update(id=str(uuid.uuid4()), activo=True,
                        etapa_manual="lead_nuevo", fecha_creacion=now)
            resp = requests.post(f"{SUPABASE_URL}/rest/v1/clientes",
                                 headers={**hdr, "Prefer": "return=minimal"}, json=base, timeout=15)
            if resp.ok:
                print(f"✅ CRM: lead {email} creado (formulario='{formulario}')", flush=True)
                notificar_lead_shopify(nombre, base["id"])   # avisa a admin/root
            else:
                print(f"⚠️ CRM: no se pudo crear ({resp.status_code}): {resp.text[:200]}", flush=True)
    except Exception as e:
        print("⚠️ CRM upsert error:", e, flush=True)

# ============================================================================
# NUEVO: CORREO DE AVISO DEL LEAD, ADAPTADO AL TIPO DE FORMULARIO.
# Arma y ENVÍA el correo de "nuevo lead" desde el propio Flask, mostrando SOLO los
# campos del formulario del que vino (COTIZA / MODELO PREDISEÑADO / PERSONALIZADO).
# Se envía por la API transaccional de Brevo (misma BREVO_API_KEY). ADITIVO y
# best-effort: NUNCA rompe el webhook.
#
# Variables de entorno (Render) que controlan este envío:
#   LEAD_MAIL_ENABLED = "1" para activarlo (por defecto "0" = apagado → no envía nada).
#   LEAD_MAIL_TO       = destinatarios coma-separados. Si está vacío usa NOTIFY_EMAILS,
#                        y si tampoco, ALERT_TO.
#   BREVO_SENDER / ALERT_FROM_EMAIL / ALERT_FROM = remitente (verificado en Brevo).
#   ALERT_FROM_NAME    = nombre visible del remitente.
# ============================================================================
NOTIFY_EMAILS = os.getenv("NOTIFY_EMAILS", "")
ALERT_TO = os.getenv("ALERT_TO", "")
LEAD_MAIL_ENABLED = os.getenv("LEAD_MAIL_ENABLED", "0").strip().lower() in ("1", "true", "si", "sí", "yes", "on")
LEAD_MAIL_TO = os.getenv("LEAD_MAIL_TO", "")
BREVO_SENDER = (os.getenv("BREVO_SENDER", "") or os.getenv("ALERT_FROM_EMAIL", "")
                or os.getenv("ALERT_FROM", "")).strip()
LEAD_MAIL_FROM_NAME = os.getenv("ALERT_FROM_NAME", "Espacio Container House")
BREVO_SEND_EMAIL_URL = "https://api.brevo.com/v3/smtp/email"
# Canal Zoho SMTP: MISMA infraestructura que ya entrega el correo viejo a Recibidos
# (DKIM/SPF/DMARC del dominio alineados). Se prefiere Zoho; Brevo queda de respaldo.
ALERT_SMTP_HOST = os.getenv("ALERT_SMTP_HOST", "").strip()
try:
    ALERT_SMTP_PORT = int(os.getenv("ALERT_SMTP_PORT", "587") or "587")
except Exception:
    ALERT_SMTP_PORT = 587
ALERT_SMTP_USER = os.getenv("ALERT_SMTP_USER", "").strip()
ALERT_SMTP_PASS = os.getenv("ALERT_SMTP_PASS", "")
ALERT_FROM = (os.getenv("ALERT_FROM", "") or os.getenv("ALERT_FROM_EMAIL", "")
              or ALERT_SMTP_USER or BREVO_SENDER).strip()
# Resend: API HTTP (NO bloqueada por Render) con el dominio mail.espaciocontainerhouse.cl
# verificado (DKIM/SPF) → entrega en Recibidos. Es el canal que ya usa el CRM del sistema.
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
RESEND_FROM = os.getenv("RESEND_FROM", "Espacio Container House <ventas@mail.espaciocontainerhouse.cl>").strip()
RESEND_SEND_URL = "https://api.resend.com/emails"

_MARCA_NAVY = "#182230"
_MARCA_NARANJA = "#F56E14"


def _esc_html(s):
    return (str(s or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def _campos_por_formulario(formulario, nombre, email, whatsapp, np):
    """[(etiqueta, valor)] SOLO con los campos del formulario correspondiente. El orden es
    el que debe verse en el correo. 'base' = datos de contacto comunes a los 3 formularios."""
    f = str(formulario or "").strip().lower()
    base = [("Nombre", nombre), ("Email", email), ("WhatsApp", whatsapp)]
    if f == "formulario cotiza":
        return base + [("¿Qué necesita?", _primero(np, "interés", "interes"))]
    if f == "formulario modelo prediseñado":
        return base + [
            ("Modelo", _primero(np, "modelo")),
            ("Valor", _primero(np, "valor", "precio")),
            ("Región", _primero(np, "región", "region")),
            ("Plazo", _primero(np, "plazo", "plazo ideal")),
            ("Presupuesto", _primero(np, "presupuesto")),
            ("Mensaje", _primero(np, "mensaje")),
        ]
    if f == "formulario personalizado":
        return base + [
            ("Módulo", _primero(np, "módulo", "modulo")),
            ("Puertas y ventanas", _primero(np, "puertas y ventanas", "puertas/ventanas", "puertas")),
            ("Revestimiento", _primero(np, "revestimiento")),
            ("Distribución", _primero(np, "distribución", "distribucion")),
            ("Presupuesto", _primero(np, "presupuesto")),
            ("Mensaje", _primero(np, "mensaje")),
        ]
    # Formulario no identificado → base + todo lo que traiga la nota (menos 'formulario').
    extra = [(k[:1].upper() + k[1:], v) for k, v in np.items() if k != "formulario"]
    return base + extra


def _html_correo_lead(formulario, nombre, email, whatsapp, np):
    """HTML (email-safe, estilos inline) del correo de aviso, con los campos del formulario."""
    _form_label = str(formulario or "Sin identificar").upper()
    _wa_digits = re.sub(r"[^\d]", "", str(whatsapp or ""))
    filas = ""
    for etq, val in _campos_por_formulario(formulario, nombre, email, whatsapp, np):
        val = str(val or "").strip()
        if not val and etq not in ("Nombre", "Email", "WhatsApp"):
            continue                              # oculta campos opcionales vacíos
        if etq == "WhatsApp" and _wa_digits:
            val_html = (f'<a href="https://wa.me/{_wa_digits}" style="color:{_MARCA_NARANJA};'
                        f'text-decoration:none;font-weight:700;">{_esc_html(val)}</a>')
        elif etq == "Email" and val:
            val_html = (f'<a href="mailto:{_esc_html(val)}" style="color:{_MARCA_NARANJA};'
                        f'text-decoration:none;font-weight:700;">{_esc_html(val)}</a>')
        else:
            val_html = _esc_html(val or "—")
        filas += (
            '<tr>'
            f'<td style="padding:12px 18px;border-bottom:1px solid #eef1f5;font-size:11px;font-weight:700;'
            f'text-transform:uppercase;letter-spacing:.05em;color:#8a94a6;white-space:nowrap;vertical-align:top;">'
            f'{_esc_html(etq)}</td>'
            f'<td style="padding:12px 18px;border-bottom:1px solid #eef1f5;font-size:15px;line-height:1.5;'
            f'color:{_MARCA_NAVY};font-weight:600;">{val_html}</td>'
            '</tr>')

    _cta = ""
    if _wa_digits:
        _cta = (f'<tr><td colspan="2" style="padding:20px 18px 6px;text-align:center;">'
                f'<a href="https://wa.me/{_wa_digits}" style="display:inline-block;background:#25D366;'
                f'color:#fff;text-decoration:none;font-weight:700;font-size:14px;padding:12px 26px;'
                f'border-radius:6px;">Responder por WhatsApp</a></td></tr>')

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f4f6f9;font-family:Arial,Helvetica,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f9;padding:24px 12px;">
    <tr><td align="center">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 8px 24px rgba(24,34,48,.08);">
        <tr><td style="background:{_MARCA_NAVY};padding:26px 24px;">
          <span style="display:inline-block;background:rgba(245,110,20,.16);color:{_MARCA_NARANJA};font-size:11px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;padding:6px 12px;border-radius:999px;border:1px solid rgba(245,110,20,.5);">{_esc_html(_form_label)}</span>
          <h1 style="margin:14px 0 0;color:#ffffff;font-size:22px;font-weight:800;letter-spacing:-.01em;">Nuevo lead registrado 🔥</h1>
          <p style="margin:6px 0 0;color:rgba(255,255,255,.6);font-size:13px;">Desde el sitio web · Espacio Container House</p>
        </td></tr>
        <tr><td style="padding:8px 6px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{filas}{_cta}</table>
        </td></tr>
        <tr><td style="padding:16px 24px 24px;">
          <p style="margin:0;color:#9aa4b2;font-size:12px;line-height:1.6;text-align:center;">
            Este lead ya quedó en el CRM, en la Bandeja. Revísalo y asígnalo a un ejecutivo.
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


def _leer_config_notif(clave, default=None):
    """Lee un valor de la tabla notificaciones_config de Supabase (clave/valor) — la MISMA
    que usa la pestaña NOTIFICACIONES del sistema para configurar este correo. best-effort:
    si Supabase no responde, devuelve `default` (y el llamador cae a las variables de entorno)."""
    if not (SUPABASE_URL and SUPABASE_SERVICE_KEY):
        return default
    try:
        hdr = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
        r = requests.get(f"{SUPABASE_URL}/rest/v1/notificaciones_config", headers=hdr,
                         params={"clave": f"eq.{clave}", "select": "valor", "limit": 1}, timeout=10)
        if r.ok and r.json():
            v = r.json()[0].get("valor")
            return v if v is not None else default
    except Exception as e:
        print("⚠️ Correo de lead: no pude leer notificaciones_config:", e, flush=True)
    return default


_DEFAULT_ASUNTO_LEAD = "🔥 Nuevo lead — {formulario} · {nombre}"


def _render_asunto_lead(tpl, formulario, nombre, email, whatsapp):
    """Reemplaza las variables {formulario} {nombre} {email} {whatsapp} del asunto configurable."""
    _f = str(formulario or "Sin identificar").upper()
    out = str(tpl or _DEFAULT_ASUNTO_LEAD)
    for k, v in (("{formulario}", _f), ("{nombre}", nombre or ""),
                 ("{email}", email or ""), ("{whatsapp}", whatsapp or "")):
        out = out.replace(k, v)
    return out.strip() or f"Nuevo lead — {_f}"


def _enviar_smtp_zoho(destinatarios, asunto, html, reply_to="", reply_name=""):
    """Envía el correo por Zoho SMTP (STARTTLS). Devuelve (ok, error)."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    _from = ALERT_FROM or ALERT_SMTP_USER
    msg = MIMEMultipart("alternative")
    msg["Subject"] = asunto
    msg["From"] = f"{LEAD_MAIL_FROM_NAME} <{_from}>"
    msg["To"] = ", ".join(destinatarios)
    if reply_to:
        msg["Reply-To"] = f"{reply_name} <{reply_to}>" if reply_name else reply_to
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        srv = smtplib.SMTP(ALERT_SMTP_HOST, ALERT_SMTP_PORT, timeout=20)
        srv.ehlo(); srv.starttls(); srv.ehlo()
        srv.login(ALERT_SMTP_USER, ALERT_SMTP_PASS)
        srv.sendmail(_from, destinatarios, msg.as_string())
        srv.quit()
        return True, None
    except Exception as e:
        return False, str(e)


def _enviar_correo(destinatarios, asunto, html, reply_to="", reply_name=""):
    """Envía por RESEND (API HTTP, dominio mail.espaciocontainerhouse.cl verificado → Inbox)
    con Brevo transaccional de respaldo. NO se usa Zoho SMTP: Render bloquea el SMTP saliente.
    Devuelve (ok, canal, detalle)."""
    if not destinatarios:
        return False, "ninguno", "sin destinatarios"
    # 1) Resend (preferido)
    _resend_err = "sin RESEND_API_KEY"
    if RESEND_API_KEY:
        payload = {"from": RESEND_FROM, "to": destinatarios, "subject": asunto, "html": html}
        if reply_to:
            payload["reply_to"] = reply_to
        try:
            r = requests.post(RESEND_SEND_URL,
                              headers={"Authorization": f"Bearer {RESEND_API_KEY}",
                                       "Content-Type": "application/json"},
                              json=payload, timeout=20)
            if r.status_code in (200, 201):
                return True, "resend", None
            _resend_err = f"{r.status_code}: {(r.text or '')[:150]}"
        except Exception as e:
            _resend_err = str(e)
    # 2) Brevo (respaldo)
    if not (BREVO_API_KEY and BREVO_SENDER):
        return False, "ninguno", f"resend: {_resend_err}; brevo: sin key/sender"
    payload = {"sender": {"email": BREVO_SENDER, "name": LEAD_MAIL_FROM_NAME},
               "to": [{"email": e} for e in destinatarios], "subject": asunto, "htmlContent": html}
    if reply_to:
        payload["replyTo"] = {"email": reply_to, "name": reply_name or reply_to}
    try:
        r = requests.post(BREVO_SEND_EMAIL_URL,
                          headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json",
                                   "accept": "application/json"},
                          json=payload, timeout=20)
        if r.status_code in (200, 201):
            return True, "brevo", f"resend falló ({_resend_err})"
        return False, "brevo", f"resend: {_resend_err}; brevo {r.status_code}: {(r.text or '')[:150]}"
    except Exception as e:
        return False, "brevo", f"resend: {_resend_err}; brevo error: {e}"


def enviar_correo_lead(email, first_name, last_name, note, tags=""):
    """Envía el correo de aviso de nuevo lead con el contenido según el tipo de formulario.
    La config (encendido, destinatarios, asunto) se lee de Supabase notificaciones_config —
    editable desde la pestaña NOTIFICACIONES del sistema — con fallback a variables de entorno.
    ADITIVO y best-effort (nunca rompe el webhook). Devuelve True si se envió."""
    # Encendido: 1º la config del sistema; si no existe, la variable de entorno.
    _cfg_en = _leer_config_notif("lead_mail_enabled", None)
    if _cfg_en is not None:
        enabled = str(_cfg_en).strip().lower() in ("1", "true", "si", "sí", "yes", "on")
    else:
        enabled = LEAD_MAIL_ENABLED
    if not enabled:
        print("ℹ️ Correo de lead: desactivado (pestaña NOTIFICACIONES / LEAD_MAIL_ENABLED).", flush=True)
        return False
    # Destinatarios: 1º la config del sistema; si no, las variables de entorno.
    _to_raw = (_leer_config_notif("lead_mail_to", "") or LEAD_MAIL_TO or NOTIFY_EMAILS or ALERT_TO or "")
    destinatarios = [e.strip() for e in str(_to_raw).replace("\n", ",").replace(";", ",").split(",") if e.strip()]
    if not (BREVO_API_KEY and BREVO_SENDER and destinatarios):
        print("⚠️ Correo de lead: falta BREVO_API_KEY / remitente (BREVO_SENDER) / destinatarios.", flush=True)
        return False
    np = parse_note(note)
    formulario = formulario_de_note(note) or formulario_de_tags(tags) or _primero(np, "formulario")
    formulario = TAG_FORMULARIO.get(str(formulario).strip().lower(), formulario)   # normaliza al nombre bonito
    nombre = (f"{first_name or ''} {last_name or ''}").strip() or (email or "")
    whatsapp = tel_de_note(note) or _primero(np, "whatsapp", "teléfono", "telefono", "fono", "celular")
    asunto = _render_asunto_lead(_leer_config_notif("lead_mail_asunto", "") or _DEFAULT_ASUNTO_LEAD,
                                 formulario, nombre, email, whatsapp)
    html = _html_correo_lead(formulario, nombre, email, whatsapp, np)
    ok, canal, detalle = _enviar_correo(destinatarios, asunto, html, reply_to=email, reply_name=nombre)
    if ok:
        print(f"✅ Correo de lead enviado por {canal} a {len(destinatarios)} dest. (formulario='{formulario}')", flush=True)
        return True
    print(f"⚠️ Correo de lead: no se pudo enviar ({detalle})", flush=True)
    return False


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
        tags = data.get("tags", "")   # NUEVO: etiqueta del formulario (p.ej. "FORMULARIO COTIZA")
        note = data.get("note", "")   # NUEVO: WhatsApp + "¿Qué necesita?" que deja el formulario

        if not email or not customer_id:
            print("❌ ERROR: No se recibió un email o ID de cliente válido.")
            return jsonify({"error": "Falta email o ID de cliente"}), 400

        # 🔍 Obtener los metacampos desde Shopify
        modelo, precio, describe_lo_que_quieres, tengo_un_plano, tu_direccin_actual, indica_tu_presupuesto, tipo_de_persona = get_customer_metafields(customer_id)

        # GUARD: solo tratamos como LEAD a los clientes que vienen de los 3 formularios reales
        # (la nota trae 'Formulario: ...' o la etiqueta mapea a un formulario). Así el newsletter
        # del footer u otras creaciones de cliente NO generan lead en el CRM ni correo de aviso.
        # (Además, ahora los formularios envían DIRECTO por /lead-form, así que este webhook casi
        #  siempre será newsletter/manual → se omite correctamente.)
        _es_lead_form = ("formulario:" in str(note or "").lower()) or bool(formulario_de_tags(tags))
        if _es_lead_form:
            # NUEVO: además de Brevo, mandar el lead al CRM (Supabase) en tiempo real
            enviar_a_crm(email, first_name, last_name, phone,
                         modelo, precio, describe_lo_que_quieres, tengo_un_plano,
                         tu_direccin_actual, indica_tu_presupuesto, tipo_de_persona,
                         tags=tags, note=note)
            # NUEVO: correo de aviso del lead, con el contenido SEGÚN el tipo de formulario
            # (COTIZA / MODELO PREDISEÑADO / PERSONALIZADO). best-effort → nunca rompe el webhook.
            try:
                enviar_correo_lead(email, first_name, last_name, note, tags=tags)
            except Exception as _e:
                print("⚠️ enviar_correo_lead falló (ignorado):", _e, flush=True)
        else:
            print(f"ℹ️ Webhook: cliente {email} NO es lead de formulario (newsletter/manual) → no CRM, no correo.", flush=True)

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

# 📊 Webhook de RESEND: guarda en el CRM (Supabase `crm_correos`) el estado de cada
# correo (entregado / abierto / click / rebote / spam) EN TIEMPO REAL. Aditivo y
# best-effort: si algo falla responde 200 igual (no queremos reintentos en loop).
# Actualiza la fila cuyo `resend_id` = el email_id del evento. Los flags son
# 'sticky' (una vez true quedan true) → robustos ante eventos fuera de orden.
@app.route('/webhook/resend', methods=['POST'])
def receive_resend_webhook():
    try:
        data = request.get_json(silent=True) or {}
        etype = str(data.get("type") or "")            # ej: email.delivered, email.opened
        info = data.get("data") or {}
        email_id = info.get("email_id") or info.get("id") or ""
        if not etype.startswith("email.") or not email_id:
            return jsonify({"ok": True, "ignorado": True}), 200
        ev = etype.split(".", 1)[1]                     # delivered/opened/clicked/bounced/complained/...
        if not (SUPABASE_URL and SUPABASE_SERVICE_KEY):
            print("⚠️ Resend webhook: faltan credenciales de Supabase.", flush=True)
            return jsonify({"ok": False}), 200
        hdr = {
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
        }
        now = datetime.now(timezone(timedelta(hours=-3))).isoformat()
        patch = {"last_event": ev, "last_event_at": data.get("created_at") or now}
        if ev == "opened":
            patch["opened"] = True
        elif ev == "clicked":
            patch["clicked"] = True
            patch["opened"] = True                     # un click implica apertura
            _link = ((info.get("click") or {}).get("link") or "").strip()
            if _link:
                patch["click_url"] = _link             # legado: último enlace clickeado
                # Acumular TODOS los enlaces: lee los actuales y agrega el nuevo sin duplicar.
                try:
                    g = requests.get(f"{SUPABASE_URL}/rest/v1/crm_correos", headers=hdr,
                                     params={"resend_id": f"eq.{email_id}", "select": "click_urls"},
                                     timeout=15)
                    cur = g.json()[0].get("click_urls") if (g.ok and g.json()) else []
                    if not isinstance(cur, list):
                        cur = []
                    if _link not in cur:
                        cur.append(_link)
                    patch["click_urls"] = cur
                except Exception:
                    patch["click_urls"] = [_link]
        elif ev == "bounced":
            patch["bounced"] = True
        elif ev == "complained":
            patch["complained"] = True
        r = requests.patch(f"{SUPABASE_URL}/rest/v1/crm_correos",
                           headers={**hdr, "Prefer": "return=minimal"},
                           params={"resend_id": f"eq.{email_id}"}, json=patch, timeout=15)
        if r.ok:
            print(f"✅ Resend: {ev} -> {email_id}", flush=True)
        else:
            print(f"⚠️ Resend: no se pudo guardar ({r.status_code}): {r.text[:200]}", flush=True)
        return jsonify({"ok": True}), 200
    except Exception as e:
        print("⚠️ Resend webhook error:", e, flush=True)
        return jsonify({"ok": True}), 200

# ============================================================================
# NUEVO: ENVÍO DIRECTO DESDE LOS FORMULARIOS (AJAX), SIN CREAR CUENTA EN SHOPIFY.
# Los 3 formularios del sitio hacen fetch() a este endpoint con {first_name, email, note}
# (la MISMA nota 'Formulario: ... · ...' que ya arman). Así NO se toca el endpoint de
# cuentas de Shopify → sin captcha /challenge, sin Cloudflare, sin lentitud, y cada lead
# llega bien etiquetado (imposible que un formulario dispare el correo de otro). Reusa la
# misma lógica de CRM + correo. CORS habilitado para el sitio. best-effort.
# ============================================================================
_LEAD_FORM_ORIGINS = os.getenv("LEAD_FORM_ORIGINS", "*")


def _cors_headers(resp):
    try:
        origin = request.headers.get("Origin", "")
    except Exception:
        origin = ""
    allow = "*"
    if _LEAD_FORM_ORIGINS and _LEAD_FORM_ORIGINS != "*":
        _allowed = [o.strip() for o in _LEAD_FORM_ORIGINS.split(",") if o.strip()]
        allow = origin if origin in _allowed else (_allowed[0] if _allowed else "*")
    resp.headers["Access-Control-Allow-Origin"] = allow
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Max-Age"] = "86400"
    return resp


@app.route('/lead-form', methods=['POST', 'OPTIONS'])
def lead_form():
    from flask import make_response
    if request.method == 'OPTIONS':
        return _cors_headers(make_response(('', 204)))
    data = request.get_json(silent=True) or {}
    first_name = str(data.get("first_name") or data.get("nombre") or "").strip()
    last_name = str(data.get("last_name") or "").strip()
    email = str(data.get("email") or "").strip()
    note = str(data.get("note") or "").strip()
    website = str(data.get("website") or "").strip()   # honeypot anti-bot
    if website:
        return _cors_headers(make_response(jsonify({"ok": True, "ignored": "bot"}), 200))
    if not email or "formulario:" not in note.lower():
        return _cors_headers(make_response(jsonify({"ok": False, "error": "datos insuficientes"}), 400))
    print(f"📥 lead-form (directo): {email} | {note[:140]}", flush=True)
    try:
        enviar_a_crm(email, first_name, last_name, "", "", "", "", "", "", "", "",
                     tags="", note=note)
    except Exception as e:
        print("⚠️ lead-form CRM error:", e, flush=True)
    try:
        enviar_correo_lead(email, first_name, last_name, note, tags="")
    except Exception as e:
        print("⚠️ lead-form correo error:", e, flush=True)
    return _cors_headers(make_response(jsonify({"ok": True}), 200))


# 🔎 DIAGNÓSTICO TEMPORAL del correo de leads (se quita después).
# GET /diag/lead-mail?k=ech-diag-2026           → reporta la config EFECTIVA que lee el Flask.
# GET /diag/lead-mail?k=ech-diag-2026&send=1     → intenta un envío de prueba SOLO a los
#   destinatarios configurados (no crea contacto ni CRM, no dispara la automatización vieja)
#   y devuelve el código + respuesta de Brevo (así vemos si rechaza el remitente).
@app.route('/diag/lead-mail', methods=['GET'])
def diag_lead_mail():
    if request.args.get("k") != "ech-diag-2026":
        return jsonify({"error": "no autorizado"}), 403
    _cfg_en = _leer_config_notif("lead_mail_enabled", None)
    _cfg_to = _leer_config_notif("lead_mail_to", None)
    _cfg_asunto = _leer_config_notif("lead_mail_asunto", None)
    _to_raw = (_cfg_to or LEAD_MAIL_TO or NOTIFY_EMAILS or ALERT_TO or "")
    destinatarios = [e.strip() for e in str(_to_raw).replace("\n", ",").replace(";", ",").split(",") if e.strip()]
    out = {
        "cfg_lead_mail_enabled": _cfg_en,
        "cfg_lead_mail_to": _cfg_to,
        "cfg_lead_mail_asunto": _cfg_asunto,
        "env_LEAD_MAIL_ENABLED": LEAD_MAIL_ENABLED,
        "destinatarios_efectivos": destinatarios,
        "resend_configurado": bool(RESEND_API_KEY),
        "resend_from": RESEND_FROM,
        "zoho_smtp_configurado": bool(ALERT_SMTP_HOST and ALERT_SMTP_USER and ALERT_SMTP_PASS),
        "brevo_sender": BREVO_SENDER,
        "has_brevo_key": bool(BREVO_API_KEY),
        "supabase_ok": bool(SUPABASE_URL and SUPABASE_SERVICE_KEY),
    }
    _html = _html_correo_lead("Formulario Cotiza", "Prueba Diagnóstico",
                              destinatarios[0] if destinatarios else "test@test.cl", "+56912345678",
                              {"interés": "Cabaña habitacional 30 m²"})
    if request.args.get("zohotest") == "1" and destinatarios:
        # Prueba SOLO Zoho SMTP y devuelve su error crudo (sin caer a Brevo).
        ok, err = _enviar_smtp_zoho(destinatarios, "🔥 PRUEBA Zoho SMTP — correo de leads", _html)
        out["zoho_send_ok"] = ok
        out["zoho_send_error"] = err
        out["zoho_host"] = ALERT_SMTP_HOST
        out["zoho_port"] = ALERT_SMTP_PORT
        out["zoho_user"] = ALERT_SMTP_USER
    if request.args.get("send") == "1":
        if not destinatarios:
            out["send"] = "faltan destinatarios"
            return jsonify(out), 200
        ok, canal, detalle = _enviar_correo(destinatarios,
                                            "🔥 PRUEBA diagnóstico — correo de leads (Flask)", _html)
        out["send_ok"] = ok
        out["send_canal"] = canal
        out["send_detalle"] = detalle
    return jsonify(out), 200


# 🔎 DIAGNÓSTICO TEMPORAL: lista TODOS los webhooks que Shopify tiene registrados
# (para ubicar qué servicios reciben los eventos de cliente, p.ej. el que manda el correo
# viejo por Zoho). GET /diag/webhooks?k=ech-diag-2026
@app.route('/diag/webhooks', methods=['GET'])
def diag_webhooks():
    if request.args.get("k") != "ech-diag-2026":
        return jsonify({"error": "no autorizado"}), 403
    try:
        r = requests.get(f"https://{SHOPIFY_STORE}/admin/api/2023-10/webhooks.json",
                         headers={"X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
                                  "Content-Type": "application/json"},
                         timeout=20, verify=False)
        data = r.json() if r.status_code == 200 else {}
        hooks = [{"topic": w.get("topic"), "address": w.get("address"),
                  "created_at": w.get("created_at"), "id": w.get("id")}
                 for w in (data.get("webhooks") or [])]
        return jsonify({"status": r.status_code, "count": len(hooks), "webhooks": hooks}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 200


# 🔥 Iniciar el servidor en Render
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
