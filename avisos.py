"""
Avisos por correo a Isamu/JC cuando la IA deriva una consulta.

Usa la API de Resend (plan gratuito). Variables de entorno:
  RESEND_API_KEY        clave de Resend (solo en el servidor, nunca en el repo)
  AVISO_EMAIL_DESTINO   uno o varios correos separados por coma
  AVISO_EMAIL_REMITENTE (opcional) por defecto "Irei Sensei <onboarding@resend.dev>"

Seguridad: el correo se envía como TEXTO PLANO (no HTML) para que un
mensaje malicioso de un tercero no pueda inyectar contenido; los textos
se recortan y nunca se registran claves en los logs.
"""
import os

import httpx

_MAX = 800


def _recortar(texto: str) -> str:
    texto = (texto or "").replace("\r", " ").strip()
    return texto if len(texto) <= _MAX else texto[:_MAX] + "…"


def avisar_derivacion(
    nombre: str | None, whatsapp: str, mensaje: str, respuesta_ia: str
) -> None:
    """Best-effort: si falla, solo se registra y NO rompe la respuesta al alumno."""
    api_key = (os.environ.get("RESEND_API_KEY") or "").strip()
    destinos = [
        d.strip() for d in (os.environ.get("AVISO_EMAIL_DESTINO") or "").split(",") if d.strip()
    ]
    if not api_key or not destinos:
        print("[aviso] RESEND_API_KEY o AVISO_EMAIL_DESTINO no configurados; no se envía correo")
        return

    remitente = (
        os.environ.get("AVISO_EMAIL_REMITENTE") or "Irei Sensei <onboarding@resend.dev>"
    ).strip()
    quien = nombre or "Número no registrado"
    cuerpo = (
        f"Un alumno necesita a Isamu.\n\n"
        f"Alumno: {quien}\n"
        f"WhatsApp: +{''.join(c for c in whatsapp if c.isdigit())}\n\n"
        f"Mensaje del alumno:\n{_recortar(mensaje)}\n\n"
        f"Lo que respondió la IA:\n{_recortar(respuesta_ia)}\n\n"
        f"Revisa todas las conversaciones en el panel admin (/admin/conversaciones)."
    )
    try:
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "from": remitente,
                "to": destinos,
                "subject": f"Consulta para Isamu: {quien}",
                "text": cuerpo,
            },
            timeout=15,
        )
        if r.status_code >= 400:
            print(f"[aviso] Resend respondió {r.status_code}: {r.text[:200]}")
    except Exception as e:  # noqa: BLE001
        print(f"[aviso] No se pudo enviar el correo: {type(e).__name__}")
