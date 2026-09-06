"""
Herramienta MCP: sincroniza el Google Sheet de evaluaciones/alumnos
hacia Supabase (usuarios, evaluaciones, avance_entrenamiento).

Autenticación: Service Account de Google (JSON), con acceso de
"Lector" compartido explícitamente sobre el Sheet. El MCP nunca usa
credenciales de una cuenta personal de Gmail.
"""
from functools import lru_cache

from google.oauth2 import service_account
from googleapiclient.discovery import build

from config import cargar_settings
from supabase_client import get_client

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Nombre de la hoja y rango a leer. Ajustar según la plantilla real.
RANGO = "Alumnos!A1:H"

COLUMNAS_ESPERADAS = [
    "nombre", "whatsapp", "email", "plan",
    "peso_kg", "porcentaje_grasa", "objetivo", "notas",
]


@lru_cache(maxsize=1)
def _servicio_sheets():
    settings = cargar_settings()
    credenciales = service_account.Credentials.from_service_account_file(
        settings.google_service_account_json, scopes=SCOPES
    )
    return build("sheets", "v4", credentials=credenciales)


def _leer_filas_sheet() -> list[dict]:
    """
    Lee el Google Sheet configurado y devuelve una lista de dicts,
    una por fila, usando la primera fila como encabezados.
    """
    settings = cargar_settings()
    resultado = (
        _servicio_sheets()
        .spreadsheets()
        .values()
        .get(spreadsheetId=settings.google_sheet_id, range=RANGO)
        .execute()
    )
    valores = resultado.get("values", [])
    if not valores:
        return []

    encabezados = [h.strip().lower() for h in valores[0]]
    filas = []
    for fila_cruda in valores[1:]:
        fila_completa = fila_cruda + [""] * (len(encabezados) - len(fila_cruda))
        filas.append(dict(zip(encabezados, fila_completa)))
    return filas


def _validar_fila(fila: dict) -> list[str]:
    """Devuelve la lista de columnas obligatorias faltantes o vacías."""
    return [c for c in ("nombre", "whatsapp") if not fila.get(c)]


def _a_float_o_none(valor: str):
    valor = (valor or "").strip().replace(",", ".")
    if not valor:
        return None
    try:
        return float(valor)
    except ValueError:
        return None


def sync_sheet_to_db() -> dict:
    """
    Lee el Google Sheet y hace upsert en Supabase (usuarios + evaluaciones).
    Filas con datos obligatorios faltantes se reportan, no se descartan
    silenciosamente.
    """
    filas = _leer_filas_sheet()
    client = get_client()

    procesadas, errores = 0, []
    for i, fila in enumerate(filas, start=2):  # fila 1 = encabezados
        faltantes = _validar_fila(fila)
        if faltantes:
            errores.append(f"Fila {i}: faltan columnas {faltantes}")
            continue

        client.table("usuarios").upsert(
            {
                "nombre": fila["nombre"].strip(),
                "whatsapp": fila["whatsapp"].strip(),
                "email": (fila.get("email") or "").strip() or None,
                "plan": (fila.get("plan") or "").strip().lower() or None,
            },
            on_conflict="whatsapp",
        ).execute()

        usuario = (
            client.table("usuarios")
            .select("id")
            .eq("whatsapp", fila["whatsapp"].strip())
            .single()
            .execute()
            .data
        )

        peso = _a_float_o_none(fila.get("peso_kg", ""))
        grasa = _a_float_o_none(fila.get("porcentaje_grasa", ""))
        objetivo = (fila.get("objetivo") or "").strip()

        if peso is not None or grasa is not None or objetivo:
            client.table("evaluaciones").insert(
                {
                    "usuario_id": usuario["id"],
                    "peso_kg": peso,
                    "porcentaje_grasa": grasa,
                    "objetivo": objetivo or None,
                    "notas": (fila.get("notas") or "").strip() or None,
                }
            ).execute()

        procesadas += 1

    return {"procesadas": procesadas, "errores": errores}
