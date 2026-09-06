"""Herramientas MCP: consulta y gestión de usuarios/alumnos."""
from datetime import date
from typing import Literal

from supabase_client import get_client, _validar_uuid, ValidacionError

EstadoMembresia = Literal["activa", "por_vencer", "vencida", "sin_membresia"]


def get_usuario(usuario_id: str) -> dict:
    """Devuelve los datos de un usuario por su UUID."""
    _validar_uuid(usuario_id, "usuario_id")
    resp = (
        get_client()
        .table("usuarios")
        .select("*")
        .eq("id", usuario_id)
        .single()
        .execute()
    )
    return resp.data


def listar_usuarios(filtro_estado: EstadoMembresia | None = None) -> list[dict]:
    """Lista usuarios, opcionalmente filtrados por estado de membresía."""
    query = get_client().table("usuarios").select("*")
    if filtro_estado:
        query = query.eq("estado_membresia", filtro_estado)
    return query.execute().data


def actualizar_membresia(usuario_id: str, nueva_fecha_vencimiento: str) -> dict:
    """
    Actualiza la fecha de vencimiento de un usuario.
    El trigger de Postgres recalcula estado_membresia automáticamente.
    nueva_fecha_vencimiento: string ISO 'YYYY-MM-DD'.
    """
    _validar_uuid(usuario_id, "usuario_id")
    try:
        date.fromisoformat(nueva_fecha_vencimiento)
    except ValueError:
        raise ValidacionError("nueva_fecha_vencimiento debe tener formato YYYY-MM-DD")

    resp = (
        get_client()
        .table("usuarios")
        .update({"fecha_vencimiento": nueva_fecha_vencimiento})
        .eq("id", usuario_id)
        .execute()
    )
    return resp.data[0] if resp.data else {}
