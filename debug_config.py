"""Ejecutar con: python debug_config.py
Muestra qué está leyendo config.py de tu .env, sin imprimir secretos completos.
Bórralo cuando termines de depurar."""
from config import cargar_settings

s = cargar_settings()
print("SUPABASE_URL leída:", repr(s.supabase_url))
print("Longitud de SUPABASE_SERVICE_ROLE_KEY:", len(s.supabase_service_role_key))

print("\nANTHROPIC_API_KEY leída (primeros/últimos caracteres):",
      s.anthropic_api_key[:12], "...", s.anthropic_api_key[-4:],
      f"(longitud {len(s.anthropic_api_key)})")

print("\nProbando llamada real a Anthropic...")
import anthropic
try:
    c = anthropic.Anthropic(api_key=s.anthropic_api_key)
    r = c.messages.create(
        model="claude-sonnet-4-6", max_tokens=10,
        messages=[{"role": "user", "content": "di 'ok'"}],
    )
    print("Anthropic OK:", r.content[0].text)
except Exception as e:
    print("Anthropic FALLÓ:", repr(e))
import socket
host = s.supabase_url.replace("https://", "").replace("http://", "").rstrip("/")
try:
    ip = socket.gethostbyname(host)
    print(f"DNS OK: {host} -> {ip}")
except Exception as e:
    print(f"DNS FALLÓ: {e}")

from supabase_client import get_client
try:
    resp = get_client().table("usuarios").select("id").limit(1).execute()
    print("Conexión a Supabase OK. Respuesta:", resp.data)
except Exception as e:
    print("Conexión a Supabase FALLÓ:", repr(e))
