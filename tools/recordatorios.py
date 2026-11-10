"""Herramienta MCP: revisar_vencimientos (pensada para correr por cron diario)."""
import datetime as dt

from notificaciones import notificar_equipo
from supabase_client import get_client
from tools.whatsapp import enviar_whatsapp

VENTANAS = {15: "recordatorio_15", 5: "recordatorio_5"}

# Perú = UTC-5 todo el año. El servidor corre en UTC, así que "hoy" se
# calcula siempre en hora de Lima para no desfasar los 15/5 días.
LIMA = dt.timezone(dt.timedelta(hours=-5))


def _hoy_lima() -> dt.date:
    return dt.datetime.now(LIMA).date()


def _ya_enviado_hoy(usuario_id: str, tipo: str) -> bool:
    inicio_dia_lima = dt.datetime.combine(_hoy_lima(), dt.time.min, tzinfo=LIMA)
    resp = (
        get_client()
        .table("recordatorios_enviados")
        .select("id")
        .eq("usuario_id", usuario_id)
        .eq("tipo", tipo)
        .gte("fecha_envio", inicio_dia_lima.isoformat())
        .execute()
    )
    return bool(resp.data)


def _nombres(ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    filas = get_client().table("usuarios").select("id,nombre").in_("id", ids).execute().data
    return {f["id"]: f["nombre"] for f in filas}


def _avisar_resumen_telegram(enviados: list[str], fallidos: list[dict]) -> None:
    """Resumen a Isamu/JC solo si hubo envíos o fallos (best-effort)."""
    if not enviados and not fallidos:
        return
    try:
        nombres = _nombres(enviados + [f["usuario_id"] for f in fallidos])
        lineas = ["📅 Recordatorios de membresía"]
        if enviados:
            lineas.append("Enviados: " + ", ".join(nombres.get(i, i) for i in enviados))
        for f in fallidos:
            lineas.append(
                f"❌ Falló {nombres.get(f['usuario_id'], f['usuario_id'])}: {f['error'][:150]}"
            )
        notificar_equipo("\n".join(lineas))
    except Exception as e:  # noqa: BLE001
        print(f"[recordatorios] Aviso a Telegram falló: {type(e).__name__}")


def revisar_vencimientos() -> dict:
    """
    Busca usuarios cuya membresía vence en exactamente 15 o 5 días
    y envía el recordatorio de WhatsApp correspondiente, evitando
    duplicados el mismo día.
    """
    hoy = _hoy_lima()
    enviados, omitidos, fallidos = [], [], []
    fechas_buscadas = {}

    for dias, tipo in VENTANAS.items():
        fecha_objetivo = (hoy + dt.timedelta(days=dias)).isoformat()
        fechas_buscadas[tipo] = fecha_objetivo
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

    print(
        f"[recordatorios] hoy(Lima)={hoy} buscadas={fechas_buscadas} "
        f"enviados={len(enviados)} omitidos={len(omitidos)} fallidos={fallidos}"
    )
    _avisar_resumen_telegram(enviados, fallidos)
    return {
        "hoy_lima": hoy.isoformat(),
        "fechas_buscadas": fechas_buscadas,
        "enviados": enviados,
        "omitidos": omitidos,
        "fallidos": fallidos,
    }
