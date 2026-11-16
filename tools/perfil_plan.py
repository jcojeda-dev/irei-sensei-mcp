"""
Cuestionario por WhatsApp para armar dieta y rutina (alergias, alimentos que no
come, lugar de entrenamiento, días por semana, molestias físicas).

Se inicia solo cuando Isamu aprueba un pago (alta o renovación). Al terminar se
genera el borrador con IA y se manda a Isamu a revisión por Telegram.

Seguridad:
  - Pasos fijos y validados; lo que escribe la persona NO va a la IA en este
    módulo (se guarda tal cual, truncado y sin caracteres de control).
  - Los datos (alergias, molestias) quedan en una tabla solo-admin (RLS).
  - Cada cambio de paso es atómico (.eq("paso", paso_actual)): un mensaje
    repetido por Meta no avanza dos veces ni genera dos borradores.
"""
import re
import unicodedata
from datetime import datetime, timedelta, timezone

import wa_mensajes
from notificaciones import notificar_equipo
from supabase_client import get_client
from tools import programas

PASOS = ("alergias", "no_come", "lugar", "dias", "molestias")
PREGUNTAS = {
    "alergias": "Para armar tu dieta de forma segura: ¿tienes alguna alergia o intolerancia alimentaria? (si no tienes, escribe «ninguna»).",
    "no_come": "¿Hay alimentos que no comes o no te gustan? Por ejemplo: pescado, lácteos, menestras. (Si no, «ninguno»).",
    "lugar": "¿Dónde entrenas principalmente? Responde: gimnasio, casa o ambos.",
    "dias": "¿Cuántos días por semana puedes entrenar (de 1 a 6)? Si quieres, indica también cuáles. Ejemplo: «4 días: lunes, martes, jueves y sábado».",
    "molestias": "¿Tienes alguna lesión o molestia física que debamos tener en cuenta para tu rutina? (Si no, «ninguna»).",
}
PREGUNTA_CAMBIOS = (
    "Para actualizar tu dieta y tu rutina: ¿hay algún cambio en tus alergias, alimentos que no comes, "
    "lesiones o días de entrenamiento? Cuéntame, o responde «igual» si todo sigue igual."
)
_NADA = {"ninguna", "ninguno", "no", "nada", "no tengo", "ningun", "ninguna alergia", "sin alergias", "no ninguna"}
_IGUAL = {"igual", "sigue igual", "todo igual", "sin cambios", "no", "ninguno", "ninguna", "nada", "no hay cambios"}
_CANCELAR = re.compile(r"\s*(omitir|cancelar|saltar)\s*[.!]?\s*")
_NUMEROS = {"uno": 1, "un": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6}
VIGENCIA_PERFIL = timedelta(days=120)


def _sin_acentos(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t or "") if unicodedata.category(c) != "Mn")


def _limpio(t: str, maximo: int = 200) -> str:
    t = re.sub(r"[\x00-\x1f\x7f]+", " ", t or "")
    return re.sub(r"\s+", " ", t).strip()[:maximo]


def _cambiar(usuario_id: str, paso_actual: str, cambios: dict) -> bool:
    cambios = {**cambios, "actualizado_en": datetime.now(timezone.utc).isoformat()}
    filas = (
        get_client().table("perfil_plan").update(cambios)
        .eq("usuario_id", usuario_id).eq("paso", paso_actual).execute().data
    )
    return bool(filas)


def iniciar(usuario_id: str, whatsapp: str, nombre: str, es_renovacion: bool) -> None:
    """Abre el cuestionario y envía la primera pregunta por WhatsApp (mejor esfuerzo)."""
    db = get_client()
    actual = db.table("perfil_plan").select("*").eq("usuario_id", usuario_id).limit(1).execute().data
    reciente = False
    if actual and actual[0].get("completado_en"):
        ultimo = datetime.fromisoformat(actual[0]["completado_en"].replace("Z", "+00:00"))
        reciente = datetime.now(timezone.utc) - ultimo < VIGENCIA_PERFIL

    if es_renovacion and reciente:
        paso, pregunta = "cambios", PREGUNTA_CAMBIOS
    else:
        paso, pregunta = "alergias", PREGUNTAS["alergias"]
    db.table("perfil_plan").upsert(
        {"usuario_id": usuario_id, "paso": paso, "actualizado_en": datetime.now(timezone.utc).isoformat(),
         "cambios": None},
        on_conflict="usuario_id",
    ).execute()
    wa_mensajes.enviar_texto(
        whatsapp.lstrip("+"),
        f"{nombre}, para preparar tu dieta y tu rutina personalizadas necesito unos datos rápidos. "
        f"{pregunta}\n(Puedes escribir «omitir» si prefieres hacerlo después con Isamu).",
    )


def procesar(usuario: dict | None, texto: str, tipo: str) -> str | None:
    """Respuesta si hay un cuestionario abierto para este alumno; None si no. "" = nada que responder."""
    if not usuario:
        return None
    perfil = (
        get_client().table("perfil_plan").select("*").eq("usuario_id", usuario["id"])
        .not_.is_("paso", "null").limit(1).execute().data
    )
    if not perfil:
        return None
    perfil = perfil[0]
    paso = perfil["paso"]

    ultimo = datetime.fromisoformat(perfil["actualizado_en"].replace("Z", "+00:00"))
    if datetime.now(timezone.utc) - ultimo > timedelta(days=7):
        _cambiar(usuario["id"], paso, {"paso": None})
        return None  # cuestionario abandonado: se cierra solo

    if tipo != "text":
        return f"Por ahora necesito tu respuesta escrita. {PREGUNTA_CAMBIOS if paso == 'cambios' else PREGUNTAS[paso]}"
    t = _limpio(texto)
    t_norm = _sin_acentos(t.lower())

    if _CANCELAR.fullmatch(t_norm):
        if _cambiar(usuario["id"], paso, {"paso": None}):
            notificar_equipo(f"ℹ️ {usuario['nombre']} omitió el cuestionario de dieta/rutina. Puedes generarla desde el bot cuando quieras.")
        return "Sin problema. Isamu te contactará para completar tus datos. 🙌"

    if paso == "cambios":
        cambios = None if t_norm in _IGUAL else t[:300]
        if not _cambiar(usuario["id"], "cambios", {"paso": None, "cambios": cambios,
                                                   "completado_en": datetime.now(timezone.utc).isoformat()}):
            return ""
        return _terminar(usuario)

    valor, error = _validar(paso, t, t_norm)
    if error:
        return f"{error} {PREGUNTAS[paso]}"
    cambios = dict(valor)
    siguiente = PASOS[PASOS.index(paso) + 1] if paso != PASOS[-1] else None
    cambios["paso"] = siguiente
    if siguiente is None:
        cambios["completado_en"] = datetime.now(timezone.utc).isoformat()
    if not _cambiar(usuario["id"], paso, cambios):
        return ""  # mensaje repetido por Meta
    return PREGUNTAS[siguiente] if siguiente else _terminar(usuario)


def _validar(paso: str, t: str, t_norm: str) -> tuple[dict, str | None]:
    if paso in ("alergias", "no_come", "molestias"):
        if len(t) < 2:
            return {}, "Cuéntame brevemente."
        return {paso: "Ninguna" if t_norm in _NADA else t.lstrip("=+-@ ")[:200]}, None
    if paso == "lugar":
        if re.search(r"\b(ambos|los dos|las dos|casa y gym|gym y casa)\b", t_norm):
            return {"lugar": "ambos"}, None
        casa = re.search(r"\b(casa|hogar|domicilio)\b", t_norm)
        gym = re.search(r"\b(gimnasio|gym|gimnasia)\b", t_norm)
        if casa and gym:
            return {"lugar": "ambos"}, None
        if casa:
            return {"lugar": "casa"}, None
        if gym:
            return {"lugar": "gimnasio"}, None
        return {}, "No te entendí."
    if paso == "dias":
        m = re.search(r"\b([1-6])\b", t_norm)
        n = int(m.group(1)) if m else next((v for k, v in _NUMEROS.items() if re.search(rf"\b{k}\b", t_norm)), None)
        if not n or not 1 <= n <= 6:
            return {}, "Indícame un número de días entre 1 y 6."
        return {"dias_semana": n, "dias_texto": t[:200]}, None
    return {}, "No entendí tu respuesta."


def _terminar(usuario: dict) -> str:
    programas.en_segundo_plano(programas.generar_borrador, usuario["id"], "cuestionario")
    return ("¡Listo, gracias! 🙌 Con esto Isamu prepara tu dieta y tu rutina. "
            "Te las enviamos por aquí apenas las revise.")
