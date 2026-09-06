"""
Cliente único de Supabase para el MCP server.

Usa la SERVICE ROLE KEY (no la anon key): el MCP server corre en un
entorno confiable (no en el navegador del usuario) y necesita
saltarse RLS para operar sobre cualquier usuario. Por eso:
  - Esta key NUNCA debe llegar al frontend ni a un repo público.
  - Toda escritura debe pasar por las funciones de este módulo,
    que validan el input antes de tocar la base de datos.
"""
from functools import lru_cache
from supabase import create_client, Client

from config import cargar_settings


@lru_cache(maxsize=1)
def get_client() -> Client:
    settings = cargar_settings()
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


class ValidacionError(ValueError):
    """Error de validación de input antes de tocar la DB."""


def _validar_uuid(valor: str, nombre_campo: str) -> str:
    import uuid
    try:
        uuid.UUID(valor)
    except (ValueError, AttributeError, TypeError):
        raise ValidacionError(f"{nombre_campo} no es un UUID válido")
    return valor
