"""
Envío de mensajes por Telegram (sin lógica de negocio, sin imports pesados,
para poder usarlo desde cualquier módulo sin ciclos).

Variables de entorno:
  TELEGRAM_BOT_TOKEN    token del bot (BotFather). Solo en el servidor.
  TELEGRAM_ALLOWED_IDS  IDs numéricos de Telegram autorizados, separados por coma
                        (Isamu y JC). Solo ellos reciben avisos y pueden dar órdenes.

Seguridad: nunca se registra el token ni se imprimen URLs de la API (el
token viaja en la URL), solo el tipo de error. Los textos se envían SIN
parse_mode, así que nada de lo que escriba un tercero se interpreta como
formato o enlaces ocultos.
"""
import os

import httpx

API = "https://api.telegram.org"
_MAX = 4000  # el límite de Telegram es 4096


def _token() -> str:
    return (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()


def ids_permitidos() -> set[int]:
    ids: set[int] = set()
    for parte in (os.environ.get("TELEGRAM_ALLOWED_IDS") or "").split(","):
        parte = parte.strip()
        if parte.isdigit():
            ids.add(int(parte))
    return ids


def _post(metodo: str, cuerpo: dict) -> bool:
    token = _token()
    if not token:
        print("[telegram] TELEGRAM_BOT_TOKEN no configurado; no se envía")
        return False
    try:
        r = httpx.post(f"{API}/bot{token}/{metodo}", json=cuerpo, timeout=15)
        if r.status_code >= 400:
            print(f"[telegram] {metodo} respondió {r.status_code}: {r.text[:200]}")
            return False
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[telegram] {metodo} falló: {type(e).__name__}")
        return False


def enviar_telegram(
    chat_id: int, texto: str, botones: list[list[dict]] | None = None
) -> bool:
    """Envía texto plano; si hay botones (inline), van en el último fragmento."""
    texto = (texto or "").strip() or "(sin contenido)"
    partes = [texto[i : i + _MAX] for i in range(0, len(texto), _MAX)]
    ok = True
    for i, parte in enumerate(partes):
        cuerpo = {
            "chat_id": chat_id,
            "text": parte,
            "disable_web_page_preview": True,
        }
        if botones and i == len(partes) - 1:
            cuerpo["reply_markup"] = {"inline_keyboard": botones}
        ok = _post("sendMessage", cuerpo) and ok
    return ok


def responder_callback(callback_id: str, texto: str = "") -> None:
    _post("answerCallbackQuery", {"callback_query_id": callback_id, "text": texto[:150]})


def notificar_equipo(texto: str) -> None:
    """Avisa a todos los IDs autorizados (Isamu y JC). Best-effort."""
    for chat_id in ids_permitidos():
        enviar_telegram(chat_id, texto)
