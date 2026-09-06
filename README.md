# Irei Sensei — MCP Admin Agent (esqueleto)

Esqueleto del agente admin. Las herramientas están definidas y validan
input, pero 3 puntos tienen `TODO` / `NotImplementedError` porque
dependen de credenciales que aún no configuras:

- `tools/sheets.py::_leer_filas_sheet` — falta la integración real con
  Google Sheets API (service account).
- `tools/plan_alimenticio.py::generar_pdf_plan` — falta el render con
  WeasyPrint + subida a Storage.
- Parseo seguro del JSON que devuelve Claude en `generar_plan_alimenticio`
  (por ahora se guarda el texto crudo).

## Instalación

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # y llenar con tus credenciales reales
```

## Seguridad (ya aplicado en el esqueleto)

- La `service_role_key` de Supabase solo vive en este servidor
  (variable de entorno), nunca en el frontend.
- Todo `usuario_id` se valida como UUID antes de tocar la DB
  (`_validar_uuid`), evitando inyección de valores raros.
- `enviar_whatsapp` solo permite plantillas de una lista blanca
  (`PLANTILLAS_PERMITIDAS`) — el agente no puede componer texto libre
  hacia los alumnos, lo que bloquea abuso vía prompt-injection.
- `revisar_vencimientos` evita reenvíos duplicados el mismo día.
- Ningún secreto está hardcodeado; `config.py` falla rápido si falta
  una variable de entorno obligatoria.

## Pendiente antes de producción

1. Implementar `_leer_filas_sheet` (Google Sheets API).
2. Implementar `generar_pdf_plan` (WeasyPrint + Storage).
3. Parsear y validar el JSON de Claude con un schema (ej. Pydantic)
   antes de guardarlo o convertirlo en PDF.
4. Crear y aprobar en Meta las 3 plantillas de WhatsApp
   (`membresia_por_vencer_15`, `membresia_por_vencer_5`,
   `plan_alimenticio_listo`).
5. Desplegar en Railway/Render con cron diario para
   `revisar_vencimientos`.
