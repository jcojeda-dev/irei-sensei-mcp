"""
Crea en Meta las plantillas de WhatsApp de recordatorio con clases restantes.

Uso (desde la carpeta del proyecto, con el .env lleno):
    python crear_plantillas_meta.py            # crea las que falten
    python crear_plantillas_meta.py --estado   # solo muestra si están aprobadas

Necesita en el .env:
    WHATSAPP_TOKEN    token con permiso whatsapp_business_management
    WHATSAPP_WABA_ID  ID de la cuenta de WhatsApp Business (WhatsApp Manager →
                      Configuración de la cuenta; no es el Phone Number ID)
Opcional:
    WHATSAPP_TEMPLATE_LANG  idioma de tus plantillas actuales (por defecto "es")

Cuando Meta las apruebe, agrega WHATSAPP_PLANTILLAS_SESIONES=1 en Render.
"""
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

API = "https://graph.facebook.com/v20.0"

# {{1}} nombre, {{2}} clases restantes, {{3}} fecha de vencimiento (mismo orden que envía tools/whatsapp.py).
PLANTILLAS = [
    {
        "name": "membresia_por_vencer_15_sesiones",
        "texto": (
            "Hola {{1}}, te quedan {{2}} clases de tu membresía en Irei Sensei y puedes "
            "usarlas hasta el {{3}}, fecha en que vence. ¡Agenda tus sesiones para "
            "aprovecharlas! Si quieres renovar, responde a este mensaje."
        ),
    },
    {
        "name": "membresia_por_vencer_5_sesiones",
        "texto": (
            "Hola {{1}}, solo quedan 5 días: tienes {{2}} clases disponibles hasta el {{3}}, "
            "cuando vence tu membresía en Irei Sensei. ¡No las dejes pasar! Para renovar, "
            "responde a este mensaje."
        ),
    },
]
EJEMPLO = [["Luis", "3", "25/11/2026"]]


def _var(nombre: str) -> str:
    v = (os.environ.get(nombre) or "").strip().strip('"').strip("'")
    if not v:
        sys.exit(f"Falta {nombre} en el .env (ver instrucciones al inicio de este archivo).")
    return v


def main() -> None:
    token, waba = _var("WHATSAPP_TOKEN"), _var("WHATSAPP_WABA_ID")
    idioma = (os.environ.get("WHATSAPP_TEMPLATE_LANG") or "es").strip()
    h = {"Authorization": f"Bearer {token}"}
    url = f"{API}/{waba}/message_templates"

    r = httpx.get(url, headers=h, params={"fields": "name,status,language", "limit": 200}, timeout=20)
    if r.status_code >= 400:
        sys.exit(f"Meta respondió {r.status_code}: {r.text[:400]}")
    existentes = {(t["name"], t["language"]): t["status"] for t in r.json().get("data", [])}

    for p in PLANTILLAS:
        clave = (p["name"], idioma)
        if clave in existentes:
            print(f"• {p['name']} ({idioma}): ya existe, estado {existentes[clave]}")
            continue
        if "--estado" in sys.argv:
            print(f"• {p['name']} ({idioma}): todavía no creada")
            continue
        cuerpo = {
            "name": p["name"], "language": idioma, "category": "UTILITY",
            "components": [{"type": "BODY", "text": p["texto"], "example": {"body_text": EJEMPLO}}],
        }
        r = httpx.post(url, headers=h, json=cuerpo, timeout=20)
        if r.status_code >= 400:
            print(f"✗ {p['name']}: Meta respondió {r.status_code}: {r.text[:400]}")
        else:
            print(f"✓ {p['name']} ({idioma}): enviada a revisión, estado {r.json().get('status')}")

    print("\nCuando ambas digan APPROVED, agrega WHATSAPP_PLANTILLAS_SESIONES=1 en Render.")


if __name__ == "__main__":
    main()
