"""
Config centralizada. Todas las credenciales vienen de variables de entorno
(nunca hardcodeadas, nunca en el repo). Ver .env.example.
"""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)


def _requerida(nombre: str) -> str:
    valor = os.environ.get(nombre)
    if not valor:
        raise RuntimeError(f"Falta la variable de entorno obligatoria: {nombre}")
    return valor


def _opcional(nombre: str) -> str | None:
    return os.environ.get(nombre) or None


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_service_role_key: str  # SOLO server-side. Nunca exponer al frontend.
    anthropic_api_key: str
    google_service_account_json: str  # ruta al archivo del service account
    google_sheet_id: str
    # Opcionales hasta configurar WhatsApp Cloud API:
    whatsapp_token: str | None = None
    whatsapp_phone_number_id: str | None = None


def cargar_settings() -> Settings:
    return Settings(
        supabase_url=_requerida("SUPABASE_URL"),
        supabase_service_role_key=_requerida("SUPABASE_SERVICE_ROLE_KEY"),
        anthropic_api_key=_requerida("ANTHROPIC_API_KEY"),
        google_service_account_json=_requerida("GOOGLE_SERVICE_ACCOUNT_JSON"),
        google_sheet_id=_requerida("GOOGLE_SHEET_ID"),
        whatsapp_token=_opcional("WHATSAPP_TOKEN"),
        whatsapp_phone_number_id=_opcional("WHATSAPP_PHONE_NUMBER_ID"),
    )
