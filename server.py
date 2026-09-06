"""
MCP server del agente admin de Irei Sensei.

Ejecutar:
    python server.py

Requiere las variables de entorno de .env.example ya configuradas
(usar python-dotenv en desarrollo local, o variables reales del
hosting en producción — NUNCA subir el .env real al repo).
"""
from mcp.server.fastmcp import FastMCP

from tools.usuarios import get_usuario, listar_usuarios, actualizar_membresia
from tools.sheets import sync_sheet_to_db
from tools.plan_alimenticio import generar_plan_alimenticio, generar_pdf_plan
from tools.whatsapp import enviar_whatsapp
from tools.recordatorios import revisar_vencimientos

mcp = FastMCP("irei-sensei-admin")

# Cada wrapper es delgado a propósito: la lógica real vive en tools/*.py,
# esta capa solo expone las funciones como tools MCP.

mcp.tool()(get_usuario)
mcp.tool()(listar_usuarios)
mcp.tool()(actualizar_membresia)
mcp.tool()(sync_sheet_to_db)
mcp.tool()(generar_plan_alimenticio)
mcp.tool()(generar_pdf_plan)
mcp.tool()(enviar_whatsapp)
mcp.tool()(revisar_vencimientos)


if __name__ == "__main__":
    mcp.run()
