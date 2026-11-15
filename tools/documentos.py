"""Herramienta MCP: aviso a Isamu/JC cuando se sube un documento a la carpeta de un alumno."""
from notificaciones import notificar_equipo
from supabase_client import _validar_uuid, get_client

ETIQUETA_TIPO = {
    "dieta": "dieta",
    "rutina": "rutina",
    "historial_dieta": "dieta anterior (historial)",
    "historial_rutina": "rutina anterior (historial)",
    "otro": "documento",
}


def notificar_documento_subido(documento_id: str) -> dict:
    """Avisa por Telegram que se subió un documento. Best-effort; solo lee de la base."""
    _validar_uuid(documento_id, "documento_id")
    c = get_client()
    docs = (
        c.table("documentos_alumno")
        .select("titulo, tipo, usuario_id")
        .eq("id", documento_id).limit(1).execute().data
    )
    if not docs:
        return {"notificado": False, "motivo": "documento no encontrado"}
    doc = docs[0]
    alumnos = (
        c.table("usuarios").select("nombre")
        .eq("id", doc["usuario_id"]).limit(1).execute().data
    )
    nombre = alumnos[0]["nombre"] if alumnos else "un alumno"
    notificar_equipo(
        f"📁 Nuevo documento en la carpeta de {nombre}: "
        f"{ETIQUETA_TIPO.get(doc['tipo'], 'documento')} — {doc['titulo']}"
    )
    return {"notificado": True}
