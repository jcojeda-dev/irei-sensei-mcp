"""Herramienta MCP: revisar_vencimientos (pensada para correr por cron diario)."""
import datetime as dt

from supabase_client import get_client
from tools.whatsapp import enviar_whatsapp

VENTANAS = {15: "recordatorio_15", 5: "recordatorio_5"}


def _ya_enviado_hoy(usuario_id: str, tipo: str) -> bool:
    hoy = dt.date.today().isoformat()
    resp = (
        get_client()
        .table("recordatorios_enviados")
        .select("id")
        .eq("usuario_id", usuario_id)
        .eq("tipo", tipo)
        .gte("fecha_envio", hoy)
        .execute()
    )
    return bool(resp.data)


def revisar_vencimientos() -> dict:
    """
    Busca usuarios cuya membresía vence en exactamente 15 o 5 días
    y envía el recordatorio de WhatsApp correspondiente, evitando
    duplicados el mismo día.
    """
    hoy = dt.date.today()
    enviados, omitidos, fallidos = [], [], []

    for dias, tipo in VENTANAS.items():
        fecha_objetivo = (hoy + dt.timedelta(days=dias)).isoformat()
        usuarios = (
            get_client()
            .table("usuarios")
            .select("id")
            .eq("fecha_vencimiento", fecha_objetivo)
            .execute()
            .data
        )
        for u in usuarios:
            if _ya_enviado_hoy(u["id"], tipo):
                omitidos.append(u["id"])
                continue
            try:
                enviar_whatsapp(u["id"], tipo)
                enviados.append(u["id"])
            except Exception as e:  # no detener el batch por un fallo individual
                fallidos.append({"usuario_id": u["id"], "error": str(e)})

    return {"enviados": enviados, "omitidos": omitidos, "fallidos": fallidos}
