"""
Herramienta MCP: enviar_whatsapp.

Medida anti-vulnerabilidad clave: el agente NUNCA compone texto libre
para enviar por WhatsApp. Solo puede disparar una de las plantillas
de esta lista blanca, previamente aprobadas por Meta. Esto evita que
un prompt-injection (ej. desde el contenido del Sheet o de Claude)
logre enviar mensajes arbitrarios a los alumnos.
"""
import httpx

from config import cargar_settings
from supabase_client import get_client, _validar_uuid

PLANTILLAS_PERMITIDAS = {
    "recordatorio_15": "membresia_por_vencer_15",  # nombre de plantilla en Meta
    "recordatorio_5": "membresia_por_vencer_5",
    "plan_enviado": "plan_alimenticio_listo",
}


def enviar_whatsapp(usuario_id: str, tipo_mensaje: str) -> dict:
    """
    Envía un mensaje de WhatsApp a un usuario usando una plantilla
    pre-aprobada. tipo_mensaje debe ser una clave de PLANTILLAS_PERMITIDAS.
    """
    _validar_uuid(usuario_id, "usuario_id")
    if tipo_mensaje not in PLANTILLAS_PERMITIDAS:
        raise ValueError(
            f"tipo_mensaje inválido. Debe ser uno de: "
            f"{list(PLANTILLAS_PERMITIDAS)}"
        )

    settings = cargar_settings()
    if not settings.whatsapp_token or not settings.whatsapp_phone_number_id:
        raise RuntimeError(
            "WhatsApp aún no está configurado (falta WHATSAPP_TOKEN o "
            "WHATSAPP_PHONE_NUMBER_ID en .env)."
        )
    client = get_client()
    usuario = (
        client.table("usuarios").select("whatsapp,nombre").eq("id", usuario_id)
        .single().execute().data
    )
    if not usuario:
        raise ValueError("Usuario no encontrado")

    plantilla = PLANTILLAS_PERMITIDAS[tipo_mensaje]
    url = (
        f"https://graph.facebook.com/v20.0/"
        f"{settings.whatsapp_phone_number_id}/messages"
    )
    payload = {
        "messaging_product": "whatsapp",
        "to": usuario["whatsapp"],
        "type": "template",
        "template": {
            "name": plantilla,
            "language": {"code": "es"},
            "components": [{
                "type": "body",
                "parameters": [{"type": "text", "text": usuario["nombre"]}],
            }],
        },
    }
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}

    resp = httpx.post(url, json=payload, headers=headers, timeout=15)
    if resp.status_code >= 400:
        # Se incluye la respuesta de Meta (no contiene el token) para poder
        # diagnosticar plantilla/idioma/número sin adivinar.
        raise RuntimeError(f"Meta respondió {resp.status_code}: {resp.text[:300]}")

    client.table("recordatorios_enviados").insert(
        {"usuario_id": usuario_id, "tipo": tipo_mensaje}
    ).execute()

    return {"enviado": True, "usuario_id": usuario_id, "tipo": tipo_mensaje}
