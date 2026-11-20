"""
Herramienta MCP: enviar_whatsapp.

Medida anti-vulnerabilidad clave: el agente NUNCA compone texto libre
para enviar por WhatsApp. Solo puede disparar una de las plantillas
de esta lista blanca, previamente aprobadas por Meta. Esto evita que
un prompt-injection (ej. desde el contenido del Sheet o de Claude)
logre enviar mensajes arbitrarios a los alumnos.
"""
import os
import re
from datetime import date

import httpx

from config import cargar_settings
from supabase_client import get_client, _validar_uuid

PLANTILLAS_PERMITIDAS = {
    "recordatorio_15": "membresia_por_vencer_15",  # nombre de plantilla en Meta
    "recordatorio_5": "membresia_por_vencer_5",
    "plan_enviado": "plan_alimenticio_listo",
}


# Versión de los recordatorios con clases restantes y fecha límite.
# Parámetros de la plantilla: {{1}} nombre, {{2}} clases restantes, {{3}} fecha de vencimiento.
# Solo se usan si WHATSAPP_PLANTILLAS_SESIONES=1 (cuando Meta ya las aprobó);
# si no, o si el alumno no tiene contador de clases, se usa la plantilla de siempre.
PLANTILLAS_CON_SESIONES = {
    "recordatorio_15": "membresia_por_vencer_15_sesiones",
    "recordatorio_5": "membresia_por_vencer_5_sesiones",
}
USAR_PLANTILLAS_SESIONES = (os.environ.get("WHATSAPP_PLANTILLAS_SESIONES") or "").strip() in ("1", "true", "si", "sí")


def datos_recordatorio(whatsapp: str, fecha_vencimiento: str | None) -> dict | None:
    """Clases restantes (del Sheet) y fecha de vencimiento en dd/mm/aaaa, o None si no aplica."""
    if not fecha_vencimiento:
        return None
    try:
        from tools.sheets import resumen_sesiones

        r = resumen_sesiones(whatsapp)
    except Exception as e:  # noqa: BLE001
        print(f"[whatsapp] no se pudo leer el contador de clases: {type(e).__name__}")
        return None
    if not r or r.get("sesiones_restantes") is None:
        return None
    return {
        "restantes": max(int(r["sesiones_restantes"]), 0),
        "fecha": date.fromisoformat(str(fecha_vencimiento)[:10]).strftime("%d/%m/%Y"),
    }


# Idioma con el que se crearon las plantillas en Meta (es, es_MX, es_ES, es_AR...).
# Se configura con WHATSAPP_TEMPLATE_LANG; se valida el formato por seguridad.
_idioma = (os.environ.get("WHATSAPP_TEMPLATE_LANG") or "es").strip()
CODIGO_IDIOMA = _idioma if re.fullmatch(r"[a-z]{2}(_[A-Z]{2})?", _idioma) else "es"


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
        client.table("usuarios").select("whatsapp,nombre,fecha_vencimiento").eq("id", usuario_id)
        .single().execute().data
    )
    if not usuario:
        raise ValueError("Usuario no encontrado")

    plantilla = PLANTILLAS_PERMITIDAS[tipo_mensaje]
    parametros = [{"type": "text", "text": usuario["nombre"]}]
    extra = None
    if tipo_mensaje in PLANTILLAS_CON_SESIONES:
        extra = datos_recordatorio(usuario["whatsapp"], usuario.get("fecha_vencimiento"))
        if extra and USAR_PLANTILLAS_SESIONES:
            plantilla = PLANTILLAS_CON_SESIONES[tipo_mensaje]
            parametros += [
                {"type": "text", "text": str(extra["restantes"])},
                {"type": "text", "text": extra["fecha"]},
            ]
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
            "language": {"code": CODIGO_IDIOMA},
            "components": [{
                "type": "body",
                "parameters": parametros,
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

    return {
        "enviado": True, "usuario_id": usuario_id, "tipo": tipo_mensaje, "plantilla": plantilla,
        "clases_restantes": extra["restantes"] if extra else None,
    }
