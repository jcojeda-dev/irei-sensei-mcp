"""
Wrapper HTTP delgado sobre las funciones del MCP, pensado para que el
panel /admin de Next.js las invoque server-to-server (nunca desde el
navegador del usuario).

Ejecutar en desarrollo:
    uvicorn http_api:app --reload --port 8000

En producción (Railway/Render) se corre igual, expuesto en su dominio.

Seguridad:
  - Requiere el header X-MCP-Secret con el valor de MCP_API_SECRET.
    Sin el secreto correcto, cualquier request se rechaza (401) antes
    de tocar ninguna herramienta.
  - No expone las 8 herramientas libremente: solo las que el panel
    admin necesita están registradas en ACCIONES_PERMITIDAS. Ninguna
    ruta ejecuta código arbitrario.
"""
import os

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from tools.sheets import sync_sheet_to_db
from tools.plan_alimenticio import generar_plan_alimenticio, generar_pdf_plan
from tools.usuarios import listar_usuarios, actualizar_membresia
from tools.whatsapp import enviar_whatsapp
from tools.recordatorios import revisar_vencimientos
from whatsapp_webhook import router as whatsapp_webhook_router

MCP_API_SECRET = os.environ["MCP_API_SECRET"]

app = FastAPI(title="Irei Sensei MCP HTTP wrapper")
app.include_router(whatsapp_webhook_router)

ACCIONES_PERMITIDAS = {
    "sync_sheet_to_db": lambda params: sync_sheet_to_db(),
    "listar_usuarios": lambda params: listar_usuarios(params.get("filtro_estado")),
    "generar_plan_alimenticio": lambda params: generar_plan_alimenticio(params["usuario_id"]),
    "generar_pdf_plan": lambda params: generar_pdf_plan(params["usuario_id"]),
    "actualizar_membresia": lambda params: actualizar_membresia(
        params["usuario_id"], params["nueva_fecha_vencimiento"]
    ),
    "enviar_whatsapp": lambda params: enviar_whatsapp(
        params["usuario_id"], params["tipo_mensaje"]
    ),
    "revisar_vencimientos": lambda params: revisar_vencimientos(),
}


@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    """Endpoint público sin datos sensibles, para health check y keep-alive."""
    return {"status": "ok"}


class ToolRequest(BaseModel):
    usuario_id: str | None = None
    filtro_estado: str | None = None
    nueva_fecha_vencimiento: str | None = None
    tipo_mensaje: str | None = None


def _verificar_secreto(x_mcp_secret: str | None):
    if x_mcp_secret != MCP_API_SECRET:
        raise HTTPException(status_code=401, detail="Secreto inválido")


@app.post("/tools/{nombre_herramienta}")
def ejecutar_herramienta(
    nombre_herramienta: str,
    body: ToolRequest,
    x_mcp_secret: str | None = Header(default=None),
):
    _verificar_secreto(x_mcp_secret)

    funcion = ACCIONES_PERMITIDAS.get(nombre_herramienta)
    if funcion is None:
        raise HTTPException(status_code=404, detail="Herramienta no encontrada")

    try:
        resultado = funcion(body.model_dump(exclude_none=True))
        return resultado
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
