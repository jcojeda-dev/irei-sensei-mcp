"""
Herramienta MCP: sincroniza el Google Sheet de evaluaciones/alumnos
hacia Supabase (usuarios, evaluaciones, avance_entrenamiento).

Autenticación: Service Account de Google (JSON), con acceso de
"Lector" compartido explícitamente sobre el Sheet. El MCP nunca usa
credenciales de una cuenta personal de Gmail.

GOOGLE_SERVICE_ACCOUNT_JSON puede ser:
  - una ruta a un archivo .json (uso local, como en desarrollo), o
  - el contenido JSON completo pegado directo en la variable de
    entorno (uso en producción, ej. Railway, donde no hay archivo).
"""
import calendar
import json
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

from google.oauth2 import service_account
from googleapiclient.discovery import build

from config import cargar_settings
from supabase_client import get_client

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Nombre de la hoja y rango a leer. Ajustar según la plantilla real.
RANGO = "Alumnos!A1:L"

# Se leen por NOMBRE de encabezado (el orden de las columnas no importa).
COLUMNAS_ESPERADAS = [
    "nombre", "whatsapp", "email", "plan",
    "peso_kg", "porcentaje_grasa", "objetivo", "notas",
    "fecha_medicion", "fecha_inicio", "meses", "fecha_fin",
]


def _credenciales_service_account(valor: str):
    """
    Detecta si 'valor' es contenido JSON directo o una ruta a archivo,
    y construye las credenciales en cualquiera de los dos casos.
    """
    valor_limpio = valor.strip()
    if valor_limpio.startswith("{"):
        info = json.loads(valor_limpio)
        return service_account.Credentials.from_service_account_info(
            info, scopes=SCOPES
        )
    return service_account.Credentials.from_service_account_file(
        valor_limpio, scopes=SCOPES
    )


@lru_cache(maxsize=1)
def _servicio_sheets():
    settings = cargar_settings()
    credenciales = _credenciales_service_account(settings.google_service_account_json)
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


def _normalizar_whatsapp(valor: str) -> str | None:
    """
    Deja solo dígitos con código de país de Perú: '970 463 993' -> '51970463993'.
    Devuelve None si no parece un número válido.
    """
    digitos = "".join(c for c in (valor or "") if c.isdigit())
    if len(digitos) == 9:
        digitos = "51" + digitos
    return digitos if 11 <= len(digitos) <= 15 else None


def _a_fecha(valor: str) -> date | None:
    """Acepta 2026-10-28, 28/10/2026 o 28-10-2026 (día primero, como en Perú)."""
    valor = (valor or "").strip()
    if not valor:
        return None
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(valor, formato).date()
        except ValueError:
            continue
    raise ValueError(f"fecha no reconocida: {valor!r} (usa AAAA-MM-DD o DD/MM/AAAA)")


def _sumar_meses(inicio: date, meses: int) -> date:
    mes_total = inicio.month - 1 + meses
    anio, mes = inicio.year + mes_total // 12, mes_total % 12 + 1
    dia = min(inicio.day, calendar.monthrange(anio, mes)[1])
    return date(anio, mes, dia)


def _hoy_lima() -> date:
    # Perú = UTC-5 todo el año; evita depender de tzdata en la imagen Docker.
    return datetime.now(timezone(timedelta(hours=-5))).date()


def _membresia_de_fila(fila: dict) -> tuple[date | None, date | None]:
    """Devuelve (fecha_inicio, fecha_fin). La fecha fin se calcula si falta."""
    inicio = _a_fecha(fila.get("fecha_inicio", ""))
    fin = _a_fecha(fila.get("fecha_fin", ""))
    meses_txt = (fila.get("meses") or "").strip()
    if fin is None and inicio is not None and meses_txt:
        meses = int(float(meses_txt.replace(",", ".")))
        if not 1 <= meses <= 36:
            raise ValueError(f"meses fuera de rango: {meses}")
        fin = _sumar_meses(inicio, meses)
    return inicio, fin


def sync_sheet_to_db() -> dict:
    """
    Lee el Google Sheet y hace upsert en Supabase (usuarios + evaluaciones).

    Cada fila es UNA MEDICIÓN (se conserva el historial). La membresía
    (fecha_inicio, meses, fecha_fin) de cada alumno sale de la fila con la
    fecha de finalización más reciente. Filas con datos inválidos se
    reportan, no se descartan silenciosamente.
    """
    filas = _leer_filas_sheet()
    client = get_client()

    procesadas, errores = 0, []
    membresias: dict[str, tuple[date, date]] = {}  # whatsapp -> (inicio, fin)

    for i, fila in enumerate(filas, start=2):  # fila 1 = encabezados
        faltantes = _validar_fila(fila)
        if faltantes:
            errores.append(f"Fila {i}: faltan columnas {faltantes}")
            continue

        try:
            whatsapp = _normalizar_whatsapp(fila["whatsapp"])
            if whatsapp is None:
                raise ValueError(f"whatsapp inválido: {fila['whatsapp']!r}")

            fecha_medicion = _a_fecha(fila.get("fecha_medicion", "")) or _hoy_lima()
            inicio, fin = _membresia_de_fila(fila)

            datos_usuario = {
                "nombre": fila["nombre"].strip(),
                "whatsapp": whatsapp,
            }
            email = (fila.get("email") or "").strip()
            plan = (fila.get("plan") or "").strip().lower()
            if email:
                datos_usuario["email"] = email
            if plan:
                datos_usuario["plan"] = plan

            client.table("usuarios").upsert(
                datos_usuario, on_conflict="whatsapp"
            ).execute()

            usuario = (
                client.table("usuarios")
                .select("id")
                .eq("whatsapp", whatsapp)
                .single()
                .execute()
                .data
            )

            peso = _a_float_o_none(fila.get("peso_kg", ""))
            grasa = _a_float_o_none(fila.get("porcentaje_grasa", ""))
            objetivo = (fila.get("objetivo") or "").strip()

            if peso is not None or grasa is not None or objetivo:
                # Una medición por alumno y fecha: re-sincronizar no duplica.
                client.table("evaluaciones").upsert(
                    {
                        "usuario_id": usuario["id"],
                        "fecha": fecha_medicion.isoformat(),
                        "peso_kg": peso,
                        "porcentaje_grasa": grasa,
                        "objetivo": objetivo or None,
                        "notas": (fila.get("notas") or "").strip() or None,
                    },
                    on_conflict="usuario_id,fecha",
                ).execute()

            if fin is not None:
                previa = membresias.get(whatsapp)
                if previa is None or fin > previa[1]:
                    membresias[whatsapp] = (inicio or fin, fin)

            procesadas += 1
        except Exception as e:  # noqa: BLE001
            errores.append(f"Fila {i}: {e}")

    # Membresía: solo se actualiza si el Sheet trae datos; nunca se borra
    # una fecha que se haya asignado desde el panel.
    for whatsapp, (inicio, fin) in membresias.items():
        client.table("usuarios").update(
            {"fecha_inicio": inicio.isoformat(), "fecha_vencimiento": fin.isoformat()}
        ).eq("whatsapp", whatsapp).execute()

    return {
        "procesadas": procesadas,
        "membresias_actualizadas": len(membresias),
        "errores": errores,
    }
