"""
Script pensado para correr como Cron Job diario en Railway/Render
(comando: python cron_revisar_vencimientos.py), separado del servicio
web principal.
"""
from tools.recordatorios import revisar_vencimientos

if __name__ == "__main__":
    resultado = revisar_vencimientos()
    print(resultado)
