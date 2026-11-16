"""
Envío y descarga de mensajes de WhatsApp (Meta Cloud API) para el flujo de pagos.

Seguridad:
  - Nunca se registra el token ni el contenido de los mensajes.
  - Lo que se descarga (capturas de pago) lo manda un desconocido: se valida el
    host de la URL que devuelve Meta, el tamaño máximo y que los primeros bytes
    sean realmente los de una imagen (JPEG/PNG/WEBP). El tipo que declare el
    remitente no se usa.
"""
import re
from urllib.parse import urlparse

import httpx

from config import cargar_settings

GRAPH = "https://graph.facebook.com/v20.0"
MAX_IMAGEN_BYTES = 6 * 1024 * 1024
_HOSTS_MEDIA = (".fbsbx.com", ".facebook.com", ".fbcdn.net", ".whatsapp.net", ".whatsapp.com")
_MEDIA_ID = re.compile(r"[0-9]{5,40}")


def _credenciales() -> tuple[str, str]:
    s = cargar_settings()
    if not s.whatsapp_token or not s.whatsapp_phone_number_id:
        raise RuntimeError("WhatsApp no está configurado")
    return s.whatsapp_token, s.whatsapp_phone_number_id


def _enviar(to: str, cuerpo: dict) -> bool:
    try:
        token, phone_id = _credenciales()
        r = httpx.post(
            f"{GRAPH}/{phone_id}/messages",
            json={"messaging_product": "whatsapp", "to": to, **cuerpo},
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
        if r.status_code >= 400:
            print(f"[whatsapp] envío falló: {r.status_code} {r.text[:200]}")
            return False
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[whatsapp] envío falló: {type(e).__name__}")
        return False


def enviar_texto(to: str, texto: str) -> bool:
    return _enviar(to, {"type": "text", "text": {"body": texto[:4000]}})


def enviar_imagen_link(to: str, url: str, caption: str = "") -> bool:
    return _enviar(to, {"type": "image", "image": {"link": url, "caption": caption[:900]}})


def subir_imagen(datos: bytes, mime: str) -> str | None:
    """Sube una imagen a Meta y devuelve su media_id (más fiable que enviar un enlace)."""
    try:
        token, phone_id = _credenciales()
        r = httpx.post(
            f"{GRAPH}/{phone_id}/media",
            headers={"Authorization": f"Bearer {token}"},
            data={"messaging_product": "whatsapp", "type": mime},
            files={"file": ("qr", datos, mime)},
            timeout=30,
        )
        if r.status_code >= 400:
            print(f"[whatsapp] subir imagen falló: {r.status_code} {r.text[:200]}")
            return None
        return r.json().get("id")
    except Exception as e:  # noqa: BLE001
        print(f"[whatsapp] subir imagen falló: {type(e).__name__}")
        return None


def enviar_imagen_id(to: str, media_id: str, caption: str = "") -> bool:
    return _enviar(to, {"type": "image", "image": {"id": media_id, "caption": caption[:900]}})


def _tipo_imagen(datos: bytes) -> tuple[str, str] | None:
    """(mime, extensión) según los primeros bytes; None si no es una imagen permitida."""
    if datos.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    if datos.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None


def descargar_imagen(media_id: str) -> tuple[bytes, str, str] | None:
    """Descarga una imagen recibida. Devuelve (bytes, mime, extensión) o None."""
    if not _MEDIA_ID.fullmatch(media_id or ""):
        return None
    try:
        token, _ = _credenciales()
        cabecera = {"Authorization": f"Bearer {token}"}
        meta = httpx.get(f"{GRAPH}/{media_id}", headers=cabecera, timeout=20)
        if meta.status_code >= 400:
            print(f"[whatsapp] metadatos de imagen: {meta.status_code}")
            return None
        info = meta.json()
        url = info.get("url") or ""
        partes = urlparse(url)
        if partes.scheme != "https" or not (partes.hostname or "").endswith(_HOSTS_MEDIA):
            print("[whatsapp] URL de imagen no permitida")
            return None
        if int(info.get("file_size") or 0) > MAX_IMAGEN_BYTES:
            return None

        datos = bytearray()
        with httpx.stream("GET", url, headers=cabecera, timeout=30, follow_redirects=False) as r:
            if r.status_code >= 400:
                return None
            for trozo in r.iter_bytes():
                datos.extend(trozo)
                if len(datos) > MAX_IMAGEN_BYTES:
                    return None
        tipo = _tipo_imagen(bytes(datos))
        if tipo is None:
            return None
        return bytes(datos), tipo[0], tipo[1]
    except Exception as e:  # noqa: BLE001
        print(f"[whatsapp] descarga de imagen falló: {type(e).__name__}")
        return None
