"""
Webhook de WhatsApp: recibe mensajes entrantes de alumnos y responde
automáticamente preguntas simples usando Claude, con datos reales del
alumno (nunca inventados) como contexto.

Reglas de seguridad y de producto:
  - El agente SOLO responde con datos que existen en la base de datos
    (membresía, plan, fecha de vencimiento). Nunca inventa información.
  - Preguntas sobre salud, nutrición fuera del plan ya generado, quejas,
    pagos en disputa, o cualquier cosa ambigua se derivan a Isamu con
    un mensaje de espera, en vez de que el agente improvise.
  - Toda solicitud entrante se verifica con la firma de Meta
    (X-Hub-Signature-256) antes de procesarse, para evitar que
    cualquiera pueda enviar webhooks falsos a este endpoint.
"""
import hashlib
import hmac
import os

import anthropic
import httpx
from fastapi import APIRouter, Request, Response

from avisos import avisar_derivacion
from config import cargar_settings
from supabase_client import get_client
from notificaciones import notificar_equipo
from tools import pagos
from tools.leads import mensaje_bienvenida, registrar_contacto_whatsapp

router = APIRouter()

WHATSAPP_VERIFY_TOKEN = os.environ["WHATSAPP_VERIFY_TOKEN"]
WHATSAPP_APP_SECRET = os.environ["WHATSAPP_APP_SECRET"]

PROMPT_SISTEMA = """Eres el asistente de WhatsApp de Irei Sensei, un negocio de \
entrenamiento personal. Respondes en nombre de Isamu (el entrenador) a \
preguntas simples de sus alumnos, en español, en tono cercano y breve \
(2-4 líneas, como un mensaje de WhatsApp real).

Solo puedes usar los datos del alumno que se te dan explícitamente abajo. \
NUNCA inventes fechas, montos, planes, ni des consejos de nutrición o \
entrenamiento que no estén en esos datos.

Si la pregunta es sobre: su membresía, su plan, cuándo vence, cómo \
descargar su plan alimenticio, o el horario/ubicación del gimnasio \
(Irei Sensei, atención de lunes a sábado) — respóndela con los datos \
dados.

Si la pregunta es sobre: salud, lesiones, síntomas, pagos en disputa, \
quejas, cambios de horario con el entrenador, o cualquier cosa que no \
puedas responder con los datos dados — NO improvises. Responde algo \
como: "Buena pregunta, dame un momento que consulto esto con Isamu y \
te escribo apenas tenga la respuesta." y nada más. En ESE caso, empieza \
tu respuesta exactamente con la marca [DERIVAR] seguida de un espacio \
(la marca se elimina antes de enviar el mensaje al alumno).

Si la persona quiere adquirir una membresía, inscribirse, renovar o pagar, \
responde SOLO con la marca [PAGO] (nada más); el sistema se encarga del resto. \
Nunca des números de cuenta ni montos tú mismo.
"""

MARCA_DERIVAR = "[DERIVAR]"
MARCA_PAGO = "[PAGO]"


def _verificar_firma_meta(cuerpo_crudo: bytes, firma_header: str | None) -> bool:
    if not firma_header or not firma_header.startswith("sha256="):
        return False
    firma_esperada = hmac.new(
        WHATSAPP_APP_SECRET.encode(), cuerpo_crudo, hashlib.sha256
    ).hexdigest()
    firma_recibida = firma_header.removeprefix("sha256=")
    return hmac.compare_digest(firma_esperada, firma_recibida)


def _responder_con_ia(usuario: dict | None, texto: str, settings) -> tuple[str, bool]:
    """Respuesta de la IA con datos reales del alumno. Devuelve (texto, derivar).
    Si quiere pagar/renovar devuelve (MARCA_PAGO, False)."""
    if usuario:
        contexto = (
            f"Nombre: {usuario['nombre']}\n"
            f"Plan: {usuario.get('plan') or 'sin plan asignado'}\n"
            f"Estado de membresía: {usuario['estado_membresia']}\n"
            f"Fecha de vencimiento: {usuario.get('fecha_vencimiento') or 'no registrada'}"
        )
    else:
        contexto = "No se encontró a este número en la base de alumnos."

    client_ai = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    respuesta_ia = client_ai.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        system=PROMPT_SISTEMA,
        messages=[{
            "role": "user",
            "content": f"Datos del alumno:\n{contexto}\n\nPregunta del alumno: {texto}",
        }],
    )
    respuesta = respuesta_ia.content[0].text.strip()
    if respuesta.startswith(MARCA_PAGO):
        return MARCA_PAGO, False
    derivar = MARCA_DERIVAR in respuesta
    return respuesta.replace(MARCA_DERIVAR, "").strip(), derivar


@router.get("/webhook/whatsapp")
def verificar_webhook(
    hub_mode: str | None = None,
    hub_challenge: str | None = None,
    hub_verify_token: str | None = None,
):
    if hub_mode == "subscribe" and hub_verify_token == WHATSAPP_VERIFY_TOKEN:
        return Response(content=hub_challenge, media_type="text/plain")
    return Response(status_code=403)


@router.post("/webhook/whatsapp")
async def recibir_mensaje(request: Request):
    cuerpo_crudo = await request.body()
    firma = request.headers.get("x-hub-signature-256")

    if not _verificar_firma_meta(cuerpo_crudo, firma):
        return Response(status_code=401)

    payload = await request.json()

    try:
        cambio = payload["entry"][0]["changes"][0]["value"]
        mensaje = cambio["messages"][0]
        whatsapp_remitente = mensaje["from"]
    except (KeyError, IndexError):
        # No es un mensaje entrante (puede ser un status update).
        return Response(status_code=200)

    tipo = mensaje.get("type", "")
    texto = (mensaje.get("text", {}) or {}).get("body", "") if tipo == "text" else ""
    media_id = (mensaje.get("image", {}) or {}).get("id") if tipo == "image" else None
    if tipo not in ("text", "image") or (tipo == "text" and not texto):
        return Response(status_code=200)

    client = get_client()
    # Meta envía el número sin "+"; en la base puede estar con o sin "+".
    solo_digitos = "".join(c for c in whatsapp_remitente if c.isdigit())
    resultado = (
        client.table("usuarios")
        .select("id, nombre, plan, fecha_vencimiento, estado_membresia")
        .in_("whatsapp", [solo_digitos, f"+{solo_digitos}"])
        .limit(1)
        .execute()
    )
    filas = resultado.data if resultado is not None else []
    usuario = filas[0] if filas else None

    settings = cargar_settings()
    texto_respuesta = None
    derivar = False

    # 1) Flujo de pagos (alta nueva / renovación / captura). "" = nada que responder.
    try:
        texto_respuesta = pagos.procesar(solo_digitos, usuario, tipo, texto, media_id)
    except Exception as e:  # noqa: BLE001
        print(f"[whatsapp] flujo de pagos falló: {type(e).__name__}")
        notificar_equipo(f"⚠️ Falló el flujo de pago de {solo_digitos}. Contáctalo a mano.")
        texto_respuesta = "Tuve un problema técnico con tu solicitud. Isamu te contactará en breve. 🙏"

    if texto_respuesta is None and usuario is None:
        # Número que no es alumno: posible interesado nuevo (formulario o botones
        # de la landing). Se registra, se avisa por Telegram y se responde con una
        # bienvenida FIJA (sin IA), para no improvisar con desconocidos.
        try:
            contacto = registrar_contacto_whatsapp(solo_digitos, texto)
            if contacto.get("bienvenida"):
                texto_respuesta = mensaje_bienvenida(contacto.get("nombre"))
        except Exception as e:  # noqa: BLE001
            print(f"[whatsapp] No se pudo registrar el contacto: {type(e).__name__}")

    if texto_respuesta is None:
        texto_respuesta, derivar = _responder_con_ia(usuario, texto, settings)
        if texto_respuesta == MARCA_PAGO:
            try:
                texto_respuesta = pagos.procesar(
                    solo_digitos, usuario, "text", texto, None, forzar_inicio=True
                ) or ""
            except Exception as e:  # noqa: BLE001
                print(f"[whatsapp] inicio de pago falló: {type(e).__name__}")
                notificar_equipo(f"⚠️ Falló el flujo de pago de {solo_digitos}. Contáctalo a mano.")
                texto_respuesta = "Tuve un problema técnico con tu solicitud. Isamu te contactará en breve. 🙏"

    if texto_respuesta == "":
        return Response(status_code=200)  # mensaje repetido: ya se procesó

    url = (
        f"https://graph.facebook.com/v20.0/"
        f"{settings.whatsapp_phone_number_id}/messages"
    )
    payload_envio = {
        "messaging_product": "whatsapp",
        "to": whatsapp_remitente,
        "type": "text",
        "text": {"body": texto_respuesta},
    }
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
    envio = httpx.post(url, json=payload_envio, headers=headers, timeout=15)
    if envio.status_code >= 400:
        # Solo se registra el error de Meta; nunca el token ni el mensaje.
        token = settings.whatsapp_token or ""
        print(f"[whatsapp] Error enviando respuesta: {envio.status_code} {envio.text[:300]}")
        # Diagnóstico seguro: solo largo y primeros 3 caracteres, nunca el token completo.
        print(f"[whatsapp] token largo={len(token)} inicio={token[:3]!r} phone_id largo={len(settings.whatsapp_phone_number_id or '')}")

    # Guardar la conversación y avisar si hay que derivar (best-effort:
    # un fallo aquí nunca debe impedir que el alumno ya recibió su respuesta).
    try:
        client.table("conversaciones").insert({
            "usuario_id": usuario["id"] if usuario else None,
            "whatsapp": solo_digitos,
            "mensaje_alumno": (texto or "[imagen]")[:2000],
            "respuesta_ia": texto_respuesta[:2000],
            "derivar": derivar,
        }).execute()
        if derivar:
            avisar_derivacion(
                usuario["nombre"] if usuario else None,
                solo_digitos,
                texto,
                texto_respuesta,
            )
    except Exception as e:  # noqa: BLE001
        print(f"[whatsapp] No se pudo guardar la conversación: {type(e).__name__}")

    return Response(status_code=200)
