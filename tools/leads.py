"""
Interesados (leads) que llegan desde la landing o por WhatsApp.

Dos caminos de entrada, ambos terminan en un aviso por Telegram:
  B) Formulario de la landing: la web guarda la fila en `leads` y llama a
     `notificar_lead`.
  A) Mensaje al WhatsApp del negocio de un número que NO es alumno:
     `registrar_contacto_whatsapp` lo registra (o lo vincula al lead del
     formulario) y el webhook responde con una bienvenida fija, sin IA.

Seguridad: todo texto que llega aquí lo escribe un desconocido. Se limpia
(sin caracteres de control, largo máximo), se manda a Telegram como TEXTO PLANO
y el único enlace que se genera es wa.me con el número ya reducido a dígitos.
"""
import re
import unicodedata
from datetime import datetime, timedelta, timezone

from notificaciones import notificar_equipo
from supabase_client import _validar_uuid, get_client

PROGRAMAS = {
    "hajimete": "Hajimete",
    "tanren": "Tanren",
    "samurai": "Samurai",
    "no_se": "Aún no lo sabe",
}

_CLAVES = {
    "nombre": "nombre",
    "edad": "edad",
    "objetivo": "objetivo",
    "experiencia": "experiencia",
    "dias disponibles": "dias",
    "mi whatsapp": "mi_whatsapp",
    "programa de interes": "programa",
}

VENTANA_VINCULO = timedelta(days=7)
MAX_AVISOS_POR_HORA = 30


def _sin_acentos(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto or "") if unicodedata.category(c) != "Mn"
    )


def _limpiar(valor: str | None, maximo: int) -> str:
    v = re.sub(r"[\x00-\x1f\x7f]+", " ", valor or "")
    return re.sub(r"\s+", " ", v).strip()[:maximo]


def normalizar_whatsapp(valor: str | None) -> str | None:
    """'970 463 993' -> '51970463993'. None si no parece un número válido."""
    digitos = "".join(c for c in (valor or "") if c.isdigit())
    if len(digitos) == 9:
        digitos = "51" + digitos
    return digitos if 11 <= len(digitos) <= 15 else None


def _programa_de(valor: str | None) -> str:
    v = _sin_acentos(valor or "").lower()
    for clave in ("hajimete", "tanren", "samurai"):
        if clave in v:
            return clave
    return "no_se"


def parsear_mensaje_formulario(texto: str) -> dict:
    """
    Extrae los campos del mensaje que arma el formulario de la landing
    ("Nombre: ...", "Edad: ...", etc.). Ignora cualquier línea desconocida.
    """
    datos: dict = {}
    for linea in (texto or "").splitlines():
        if ":" not in linea:
            continue
        clave, _, valor = linea.partition(":")
        campo = _CLAVES.get(_sin_acentos(clave).strip().lower())
        if campo and campo not in datos:
            datos[campo] = _limpiar(valor, 200)
    return datos


def _es_mensaje_de_formulario(texto: str, datos: dict) -> bool:
    return "solicitar mi evaluacion" in _sin_acentos(texto).lower() and bool(datos.get("nombre"))


def _edad_valida(valor: str | None) -> int | None:
    try:
        edad = int(str(valor).strip())
    except (TypeError, ValueError):
        return None
    return edad if 10 <= edad <= 99 else None


def mensaje_bienvenida(nombre: str | None) -> str:
    """Texto fijo (sin IA). El nombre se reduce a letras para que no cuele nada raro."""
    limpio = re.sub(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ' -]", "", nombre or "").strip()[:30]
    saludo = f"¡Hola, {limpio}!" if limpio else "¡Hola!"
    return (
        f"{saludo} 👋 Gracias por escribir a Irei Sensei. Ya recibimos tu solicitud y "
        "Isamu te contactará muy pronto para coordinar tu evaluación. "
        "Si tienes alguna duda mientras tanto, escríbela aquí."
    )


def _texto_aviso(lead: dict, titulo: str) -> str:
    lineas = [titulo]
    if lead.get("nombre"):
        lineas.append(f"Nombre: {lead['nombre']}")
    lineas.append(f"WhatsApp: +{lead['whatsapp']}")
    lineas.append(f"Programa: {PROGRAMAS.get(lead.get('programa'), 'Aún no lo sabe')}")
    for etiqueta, campo in (
        ("Edad", "edad"), ("Objetivo", "objetivo"),
        ("Experiencia", "experiencia"), ("Días", "dias"),
    ):
        if lead.get(campo) not in (None, ""):
            lineas.append(f"{etiqueta}: {lead[campo]}")
    if lead.get("origen") == "whatsapp" and lead.get("mensaje_original") and not lead.get("nombre"):
        lineas.append(f"Mensaje: {_limpiar(lead['mensaje_original'], 300)}")
    return "\n".join(lineas)


def _boton_whatsapp(whatsapp: str) -> list[list[dict]]:
    # whatsapp ya está restringido a dígitos por la base de datos.
    return [[{"text": "💬 Escribirle por WhatsApp", "url": f"https://wa.me/{whatsapp}"}]]


def _avisos_recientes() -> int:
    desde = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    filas = (
        get_client().table("leads").select("id")
        .eq("notificado", True).gte("creado_en", desde).limit(MAX_AVISOS_POR_HORA + 1)
        .execute().data
    )
    return len(filas)


def notificar_lead(lead_id: str) -> dict:
    """Envía el aviso por Telegram de un lead (una sola vez) y lo marca como notificado."""
    _validar_uuid(lead_id, "lead_id")
    c = get_client()
    filas = c.table("leads").select("*").eq("id", lead_id).limit(1).execute().data
    if not filas:
        return {"notificado": False, "motivo": "lead no encontrado"}
    lead = filas[0]
    if lead.get("notificado"):
        return {"notificado": False, "motivo": "ya notificado"}
    if _avisos_recientes() >= MAX_AVISOS_POR_HORA:
        # Freno anti-spam: el lead queda guardado y se verá en el panel.
        print("[leads] límite de avisos por hora alcanzado; no se envía a Telegram")
        return {"notificado": False, "motivo": "límite por hora"}

    if lead["origen"] == "formulario" and not lead.get("llego_por_whatsapp"):
        titulo = "🆕 Nuevo interesado (formulario web)\nAún no envió el mensaje por WhatsApp."
    elif lead["origen"] == "formulario":
        titulo = "🆕 Nuevo interesado (formulario web)"
    else:
        titulo = "🆕 Nuevo contacto por WhatsApp"

    ok = notificar_equipo(_texto_aviso(lead, titulo), botones=_boton_whatsapp(lead["whatsapp"]))
    if ok:
        c.table("leads").update({"notificado": True}).eq("id", lead_id).execute()
    return {"notificado": ok}


def registrar_contacto_whatsapp(whatsapp: str, texto: str) -> dict:
    """
    Un número que no es alumno escribió al WhatsApp del negocio.
    Devuelve {"bienvenida": bool, "nombre": str|None}.
      - Si ya hay un lead reciente de ese número (o del "Mi WhatsApp" del mensaje)
        que aún no había escrito por WhatsApp: lo vincula y da la bienvenida.
      - Si ya había escrito antes: no repite la bienvenida (sigue la IA).
      - Si no hay lead: lo crea, avisa por Telegram y da la bienvenida.
    """
    numero = normalizar_whatsapp(whatsapp)
    if numero is None:
        return {"bienvenida": False}

    datos = parsear_mensaje_formulario(texto)
    candidatos = {numero}
    otro = normalizar_whatsapp(datos.get("mi_whatsapp"))
    if otro:
        candidatos.add(otro)

    c = get_client()
    desde = (datetime.now(timezone.utc) - VENTANA_VINCULO).isoformat()
    existentes = (
        c.table("leads").select("id, nombre, whatsapp, llego_por_whatsapp")
        .in_("whatsapp", list(candidatos)).gte("creado_en", desde)
        .order("creado_en", desc=True).limit(1).execute().data
    )
    if existentes:
        lead = existentes[0]
        if lead["llego_por_whatsapp"]:
            return {"bienvenida": False}
        c.table("leads").update(
            {"llego_por_whatsapp": True, "mensaje_original": _limpiar(texto, 1000)}
        ).eq("id", lead["id"]).execute()
        quien = lead.get("nombre") or "Un interesado"
        notificar_equipo(
            f"✅ {quien} ya envió su mensaje por WhatsApp (+{lead['whatsapp']}).",
            botones=_boton_whatsapp(lead["whatsapp"]),
        )
        return {"bienvenida": True, "nombre": lead.get("nombre")}

    fila = {
        "whatsapp": numero,
        "origen": "whatsapp",
        "llego_por_whatsapp": True,
        "mensaje_original": _limpiar(texto, 1000),
        "programa": _programa_de(datos.get("programa") or texto),
    }
    if _es_mensaje_de_formulario(texto, datos):
        fila.update({
            "nombre": _limpiar(datos.get("nombre"), 80) or None,
            "edad": _edad_valida(datos.get("edad")),
            "objetivo": _limpiar(datos.get("objetivo"), 200) or None,
            "experiencia": _limpiar(datos.get("experiencia"), 200) or None,
            "dias": _limpiar(datos.get("dias"), 100) or None,
        })
    creado = c.table("leads").insert(fila).execute().data[0]
    try:
        notificar_lead(creado["id"])
    except Exception as e:  # noqa: BLE001
        print(f"[leads] aviso falló: {type(e).__name__}")
    return {"bienvenida": True, "nombre": fila.get("nombre")}


def reintentar_avisos_pendientes() -> dict:
    """Reenvía los avisos que no salieron (p. ej. servidor dormido). Se llama desde el cron diario."""
    desde = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    pendientes = (
        get_client().table("leads").select("id")
        .eq("notificado", False).gte("creado_en", desde).limit(20).execute().data
    )
    enviados = 0
    for p in pendientes:
        try:
            if notificar_lead(p["id"]).get("notificado"):
                enviados += 1
        except Exception as e:  # noqa: BLE001
            print(f"[leads] reintento falló: {type(e).__name__}")
    return {"pendientes": len(pendientes), "enviados": enviados}
