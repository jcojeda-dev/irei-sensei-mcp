"""
Herramientas MCP:
  - generar_plan_alimenticio: llama a Claude con la evaluación+objetivo
    del usuario y devuelve un plan estructurado.
  - generar_pdf_plan: convierte ese plan en PDF membretado y lo sube
    a Supabase Storage.
"""
import datetime as dt
import json
from pathlib import Path

import anthropic
from jinja2 import Environment, FileSystemLoader, select_autoescape

from config import cargar_settings
from supabase_client import get_client, _validar_uuid

BASE_DIR = Path(__file__).resolve().parent.parent
RUTA_LOGO = BASE_DIR / "assets" / "logo_irei_sensei.png"
RUTA_PLANTILLA = BASE_DIR / "templates"
BUCKET_PDFS = "planes-alimenticios"

MESES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

PROMPT_SISTEMA = (
    "Eres un nutricionista deportivo. Generas planes alimenticios "
    "mensuales seguros y realistas en JSON estructurado, en español, "
    "para un alumno de entrenamiento personal. No das recomendaciones "
    "médicas ni diagnósticos; si el objetivo o los datos parecen "
    "riesgosos (ej. déficit calórico extremo), lo señalas en el campo "
    "'advertencias' en vez de generarlo tal cual. Responde SOLO con "
    "JSON válido con esta forma exacta: "
    '{"comidas": [{"nombre": str, "descripcion": str}, ...], '
    '"advertencias": [str, ...]}'
)


class PlanInvalidoError(ValueError):
    """El JSON devuelto por el modelo no tiene la forma esperada."""


def _obtener_ultima_evaluacion(usuario_id: str) -> dict:
    resp = (
        get_client()
        .table("evaluaciones")
        .select("*")
        .eq("usuario_id", usuario_id)
        .order("fecha", desc=True)
        .limit(1)
        .single()
        .execute()
    )
    return resp.data


def _parsear_plan(texto: str) -> dict:
    """
    Parsea de forma segura el JSON devuelto por el modelo (sin eval/exec)
    y valida su forma antes de aceptarlo.
    """
    texto = texto.strip()
    if texto.startswith("```"):
        texto = texto.strip("`")
        if texto.lower().startswith("json"):
            texto = texto[4:]
        texto = texto.strip()

    try:
        datos = json.loads(texto)
    except json.JSONDecodeError as e:
        raise PlanInvalidoError(f"El plan generado no es JSON válido: {e}")

    if not isinstance(datos, dict):
        raise PlanInvalidoError("El plan debe ser un objeto JSON")

    comidas = datos.get("comidas")
    if not isinstance(comidas, list) or not comidas:
        raise PlanInvalidoError("El plan no incluye una lista de comidas válida")
    for c in comidas:
        if not isinstance(c, dict) or "nombre" not in c or "descripcion" not in c:
            raise PlanInvalidoError("Cada comida debe tener 'nombre' y 'descripcion'")

    advertencias = datos.get("advertencias", [])
    if not isinstance(advertencias, list):
        raise PlanInvalidoError("'advertencias' debe ser una lista")

    return {"comidas": comidas, "advertencias": advertencias}


def generar_plan_alimenticio(usuario_id: str) -> dict:
    """
    Genera el plan alimenticio del mes en curso para un usuario,
    basado en su última evaluación, y lo guarda en
    programas_alimenticios (columna `contenido`, sin PDF todavía).
    """
    _validar_uuid(usuario_id, "usuario_id")
    settings = cargar_settings()
    evaluacion = _obtener_ultima_evaluacion(usuario_id)
    if not evaluacion:
        raise ValueError("El usuario no tiene evaluaciones registradas")

    client_ai = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    mensaje = client_ai.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system=PROMPT_SISTEMA,
        messages=[{
            "role": "user",
            "content": (
                f"Peso: {evaluacion.get('peso_kg')} kg, "
                f"% grasa: {evaluacion.get('porcentaje_grasa')}, "
                f"Objetivo: {evaluacion.get('objetivo')}."
            ),
        }],
    )
    plan = _parsear_plan(mensaje.content[0].text)

    mes_actual = dt.date.today().replace(day=1).isoformat()
    resp = (
        get_client()
        .table("programas_alimenticios")
        .upsert(
            {"usuario_id": usuario_id, "mes": mes_actual, "contenido": plan},
            on_conflict="usuario_id,mes",
        )
        .execute()
    )
    return resp.data[0] if resp.data else {}


def generar_pdf_plan(usuario_id: str) -> dict:
    """
    Toma el plan guardado del mes actual, genera el PDF membretado
    (logo Irei Sensei) y sube el archivo a Supabase Storage.
    Devuelve la URL pública/firmada del PDF.
    """
    _validar_uuid(usuario_id, "usuario_id")
    try:
        from weasyprint import HTML
    except OSError as e:
        raise RuntimeError(
            "WeasyPrint no puede cargar sus librerías del sistema (GTK/Pango/"
            "Cairo). En Windows instala el GTK3 Runtime; en el servidor de "
            f"producción (Linux) esto no debería ocurrir. Detalle: {e}"
        )
    client = get_client()

    usuario = (
        client.table("usuarios").select("nombre").eq("id", usuario_id)
        .single().execute().data
    )
    if not usuario:
        raise ValueError("Usuario no encontrado")

    mes_actual_date = dt.date.today().replace(day=1)
    mes_actual = mes_actual_date.isoformat()
    programa = (
        client.table("programas_alimenticios")
        .select("*")
        .eq("usuario_id", usuario_id)
        .eq("mes", mes_actual)
        .single()
        .execute()
        .data
    )
    if not programa or not programa.get("contenido"):
        raise ValueError(
            "No hay plan generado para este usuario en el mes actual. "
            "Corre generar_plan_alimenticio primero."
        )

    contenido = programa["contenido"]
    if isinstance(contenido, str):
        contenido = _parsear_plan(contenido)

    evaluacion = _obtener_ultima_evaluacion(usuario_id) or {}

    env = Environment(
        loader=FileSystemLoader(str(RUTA_PLANTILLA)),
        autoescape=select_autoescape(["html"]),
    )
    plantilla = env.get_template("plan_alimenticio.html")

    if not RUTA_LOGO.exists():
        raise FileNotFoundError(
            f"Falta el logo en {RUTA_LOGO}. Coloca el archivo "
            "logo_irei_sensei.png en la carpeta assets/ antes de generar PDFs."
        )

    html_renderizado = plantilla.render(
        logo_path=RUTA_LOGO.as_uri(),
        mes_legible=f"{MESES_ES[mes_actual_date.month - 1]} {mes_actual_date.year}",
        nombre=usuario["nombre"],
        objetivo=evaluacion.get("objetivo") or "No especificado",
        comidas=contenido["comidas"],
        advertencias=contenido.get("advertencias", []),
    )

    pdf_bytes = HTML(string=html_renderizado).write_pdf()

    nombre_archivo = f"{usuario_id}/{mes_actual}.pdf"
    client.storage.from_(BUCKET_PDFS).upload(
        nombre_archivo,
        pdf_bytes,
        {"content-type": "application/pdf", "upsert": "true"},
    )
    resultado = client.storage.from_(BUCKET_PDFS).create_signed_url(
        nombre_archivo, 60 * 60 * 24 * 30  # 30 días
    )
    pdf_url = resultado.get("signedURL") or resultado.get("signed_url")

    client.table("programas_alimenticios").update(
        {"pdf_url": pdf_url}
    ).eq("id", programa["id"]).execute()

    return {"usuario_id": usuario_id, "pdf_url": pdf_url}
