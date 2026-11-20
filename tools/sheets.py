"""
Herramienta MCP: sincroniza el Google Sheet de evaluaciones/alumnos
hacia Supabase (usuarios, evaluaciones, avance_entrenamiento).

Autenticación: Service Account de Google (JSON), con acceso de
"Editor" compartido explícitamente sobre el Sheet (el bot de pagos agrega filas). El MCP nunca usa
credenciales de una cuenta personal de Gmail.

GOOGLE_SERVICE_ACCOUNT_JSON puede ser:
  - una ruta a un archivo .json (uso local, como en desarrollo), o
  - el contenido JSON completo pegado directo en la variable de
    entorno (uso en producción, ej. Railway, donde no hay archivo).
"""
import calendar
import json
import re
import threading
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

from google.oauth2 import service_account
from googleapiclient.discovery import build

from config import cargar_settings
from supabase_client import get_client

# Lectura y escritura: el Sheet debe estar compartido con la cuenta de servicio como "Editor".
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Nombre de la hoja y rango a leer. Ajustar según la plantilla real.
HOJA = "Alumnos"
RANGO = "Alumnos!A1:Z"

# Se leen por NOMBRE de encabezado (el orden de las columnas no importa).
COLUMNAS_ESPERADAS = [
    "nombre", "whatsapp", "email", "plan",
    "peso_kg", "porcentaje_grasa", "objetivo", "notas",
    "fecha_medicion", "fecha_inicio", "fecha_renovacion", "meses", "fecha_fin",
    # "sesiones" y "precio" son FÓRMULAS del Sheet: el servidor no las lee ni las escribe.
]

# Columnas que son FÓRMULAS en "Alumnos": nunca se escriben, se copian de la fila de arriba.
COLUMNAS_FORMULA = (
    "sesiones", "precio", "clases_semana",
    "sesiones_total", "sesiones_realizadas", "sesiones_restantes",
)

# Bitácora de sesiones: una fila por sesión confirmada desde Telegram.
# Columnas: fecha | nombre | whatsapp | nota. El Sheet cuenta las filas por WhatsApp.
HOJA_SESIONES = "sesiones_log"

DIAS_SEMANA = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


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
    # Inicio del periodo vigente: la renovación (si existe) manda sobre el inicio original.
    inicio = _a_fecha(fila.get("fecha_renovacion", "")) or _a_fecha(fila.get("fecha_inicio", ""))
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


def _texto_seguro(valor) -> str:
    """Evita que un texto de un tercero se interprete como fórmula en el Sheet."""
    t = "" if valor is None else str(valor)
    t = "".join(c for c in t if c >= " " or c in "\n")
    return t.lstrip("=+-@ ").strip()[:500]


def _letra(indice: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    n, letras = indice + 1, ""
    while n:
        n, r = divmod(n - 1, 26)
        letras = chr(65 + r) + letras
    return letras


def _leer_hoja() -> tuple[list[str], list[list[str]]]:
    """(encabezados en minúscula, filas de datos). Fila de datos i está en la fila i+2 del Sheet."""
    valores = (
        _servicio_sheets().spreadsheets().values()
        .get(spreadsheetId=cargar_settings().google_sheet_id, range=RANGO)
        .execute().get("values", [])
    )
    if not valores:
        raise RuntimeError("La hoja Alumnos está vacía")
    cab = [h.strip().lower() for h in valores[0]]
    if "nombre" not in cab or "whatsapp" not in cab:
        raise RuntimeError("La hoja Alumnos no tiene encabezados nombre/whatsapp")
    return cab, valores[1:]


def buscar_fila_alumno(whatsapp: str) -> int | None:
    """
    Número de fila (1 = encabezados) donde está la MEMBRESÍA del alumno: la última
    fila de ese número que tenga plan; si ninguna tiene, la primera de ese número.
    Las filas que solo traen mediciones no se tocan.
    """
    cab, filas = _leer_hoja()
    return _buscar_en(cab, filas, whatsapp)


def _buscar_en(cab: list[str], filas: list[list[str]], whatsapp: str) -> int | None:
    i_wa, i_plan = cab.index("whatsapp"), cab.index("plan") if "plan" in cab else None
    con_plan, cualquiera = None, None
    for n, f in enumerate(filas, start=2):
        if _normalizar_whatsapp(f[i_wa] if i_wa < len(f) else "") != whatsapp:
            continue
        cualquiera = cualquiera or n
        if i_plan is not None and i_plan < len(f) and f[i_plan].strip():
            con_plan = n
    return con_plan or cualquiera


def actualizar_fila(numero: int, valores: dict) -> None:
    """Cambia SOLO las celdas indicadas (por encabezado) de una fila existente."""
    cab, _ = _leer_hoja()
    datos = [
        {"range": f"{HOJA}!{_letra(cab.index(k))}{numero}", "values": [[_texto_seguro(v)]]}
        for k, v in valores.items() if k in cab
    ]
    if not datos:
        return
    _servicio_sheets().spreadsheets().values().batchUpdate(
        spreadsheetId=cargar_settings().google_sheet_id,
        body={"valueInputOption": "USER_ENTERED", "data": datos},
    ).execute()


def _copiar_formulas(numero: int, cab: list[str]) -> None:
    """Copia las fórmulas (sesiones, precio, contador de sesiones...) de la fila de arriba a la fila nueva (mejor esfuerzo)."""
    if numero < 3:
        return
    servicio, sid = _servicio_sheets(), cargar_settings().google_sheet_id
    columnas = [cab.index(c) for c in COLUMNAS_FORMULA if c in cab]
    if not columnas:
        return
    hojas = servicio.spreadsheets().get(
        spreadsheetId=sid, fields="sheets(properties(sheetId,title))"
    ).execute()["sheets"]
    hoja_id = next(h["properties"]["sheetId"] for h in hojas if h["properties"]["title"] == HOJA)
    peticiones = []
    for col in columnas:
        celda = f"{HOJA}!{_letra(col)}{numero - 1}"
        v = (
            servicio.spreadsheets().values()
            .get(spreadsheetId=sid, range=celda, valueRenderOption="FORMULA")
            .execute().get("values", [[""]])
        )
        if not (v and v[0] and str(v[0][0]).startswith("=")):
            continue  # no es fórmula: no se copia un valor ajeno
        peticiones.append({"copyPaste": {
            "source": {"sheetId": hoja_id, "startRowIndex": numero - 2, "endRowIndex": numero - 1,
                       "startColumnIndex": col, "endColumnIndex": col + 1},
            "destination": {"sheetId": hoja_id, "startRowIndex": numero - 1, "endRowIndex": numero,
                            "startColumnIndex": col, "endColumnIndex": col + 1},
            "pasteType": "PASTE_FORMULA",
        }})
    if peticiones:
        servicio.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": peticiones}).execute()


_candado_hoja = threading.Lock()


def _siguiente_fila_libre(cab: list[str], filas: list[list[str]]) -> int:
    """Fila (1 = encabezados) justo debajo de la última fila con nombre o WhatsApp.
    No depende de filas pre-formateadas ni de desplegables vacíos más abajo."""
    claves = [cab.index("nombre"), cab.index("whatsapp")]
    ultimo = 1
    for n, f in enumerate(filas, start=2):
        if any(i < len(f) and f[i].strip() for i in claves):
            ultimo = n
    return ultimo + 1


def agregar_fila(valores: dict) -> int:
    """
    Escribe UNA fila nueva justo debajo de la última fila con datos de "Alumnos",
    celda por celda según los encabezados. Solo toca las celdas con valor: no borra
    desplegables ni fórmulas. sesiones y precio no se escriben (son fórmulas del
    Sheet): se copian desde la fila de arriba. Devuelve el número de fila.
    """
    with _candado_hoja:  # dos altas simultáneas no pisan la misma fila
        cab, filas = _leer_hoja()
        numero = _siguiente_fila_libre(cab, filas)
        datos = []
        for h in cab:
            if h in COLUMNAS_FORMULA:
                continue
            v = _texto_seguro(valores.get(h))
            if v:
                datos.append({"range": f"{HOJA}!{_letra(cab.index(h))}{numero}", "values": [[v]]})
        _servicio_sheets().spreadsheets().values().batchUpdate(
            spreadsheetId=cargar_settings().google_sheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": datos},
        ).execute()
        try:
            _copiar_formulas(numero, cab)
        except Exception as e:  # noqa: BLE001
            print(f"[sheets] no se pudieron copiar las fórmulas: {type(e).__name__}")
        return numero


def registrar_membresia(
    whatsapp: str, nombre: str, email: str | None, plan: str,
    inicio: date, meses: int, fin: date, renovacion: bool,
) -> str:
    """
    Escribe la membresía de un pago aprobado, sin perder el historial:
      - alta: fecha_inicio = inicio del periodo;
      - renovación: fecha_inicio NO se toca; fecha_renovacion = inicio del nuevo periodo.
    Siempre actualiza plan, meses y fecha_fin. Devuelve "actualizada" o "agregada".
    """
    whatsapp = _normalizar_whatsapp(whatsapp) or whatsapp
    numero = buscar_fila_alumno(whatsapp)
    if numero:
        cab, filas = _leer_hoja()
        actual = dict(zip(cab, filas[numero - 2] + [""] * len(cab)))
        cambios = {"plan": plan, "meses": meses, "fecha_fin": fin.isoformat()}
        if renovacion and (actual.get("fecha_inicio") or "").strip():
            cambios["fecha_renovacion"] = inicio.isoformat()
        else:
            cambios["fecha_inicio"] = inicio.isoformat()
        actualizar_fila(numero, cambios)
        return "actualizada"
    agregar_fila({
        "nombre": nombre, "whatsapp": whatsapp, "email": email, "plan": plan,
        "fecha_inicio": inicio.isoformat(), "meses": meses, "fecha_fin": fin.isoformat(),
    })
    return "agregada"


# ---------------------------------------------------------------------------
# Sesiones: contador y días de entrenamiento
# ---------------------------------------------------------------------------
def _a_int_o_none(valor) -> int | None:
    v = _a_float_o_none(str(valor or ""))
    return int(v) if v is not None else None


def _sin_tildes(texto: str) -> str:
    return (texto or "").strip().lower().translate(str.maketrans("áéíóú", "aeiou"))


def _resumen_de_fila(cab: list[str], fila: list[str]) -> dict:
    d = dict(zip(cab, fila + [""] * (len(cab) - len(fila))))
    dias = [d.get(f"dia_{i}", "").strip() for i in range(1, 8)]
    return {
        "nombre": d.get("nombre", "").strip(),
        "whatsapp": _normalizar_whatsapp(d.get("whatsapp", "")),
        "plan": d.get("plan", "").strip(),
        "clases_semana": _a_int_o_none(d.get("clases_semana")),
        "dias": [x for x in dias if x],
        "sesiones_total": _a_int_o_none(d.get("sesiones_total")),
        "sesiones_realizadas": _a_int_o_none(d.get("sesiones_realizadas")),
        "sesiones_restantes": _a_int_o_none(d.get("sesiones_restantes")),
    }


def resumen_sesiones(whatsapp: str) -> dict | None:
    """Contador de sesiones y días de entrenamiento del alumno (según el Sheet), o None si no está."""
    whatsapp = _normalizar_whatsapp(whatsapp) or whatsapp
    cab, filas = _leer_hoja()
    numero = _buscar_en(cab, filas, whatsapp)
    if not numero:
        return None
    return _resumen_de_fila(cab, filas[numero - 2])


def sesiones_en_fecha(whatsapp: str, fecha: date) -> int:
    """Cuántas sesiones tiene ya registradas el alumno en esa fecha (para avisar duplicados)."""
    whatsapp = _normalizar_whatsapp(whatsapp) or whatsapp
    valores = (
        _servicio_sheets().spreadsheets().values()
        .get(spreadsheetId=cargar_settings().google_sheet_id, range=f"{HOJA_SESIONES}!A2:C")
        .execute().get("values", [])
    )
    n = 0
    for f in valores:
        if len(f) < 3 or _normalizar_whatsapp(f[2]) != whatsapp:
            continue
        try:
            if _a_fecha(f[0]) == fecha:
                n += 1
        except ValueError:
            continue
    return n


def registrar_sesion(whatsapp: str, nombre: str, fecha: date, nota: str = "") -> None:
    """
    Agrega UNA fila a la bitácora de sesiones. El Sheet descuenta solo:
    sesiones_restantes = sesiones_total - filas de ese WhatsApp desde el inicio del periodo.
    """
    whatsapp = _normalizar_whatsapp(whatsapp)
    if not whatsapp:
        raise ValueError("WhatsApp inválido")
    with _candado_hoja:
        _servicio_sheets().spreadsheets().values().append(
            spreadsheetId=cargar_settings().google_sheet_id,
            range=f"{HOJA_SESIONES}!A:D",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [[fecha.isoformat(), _texto_seguro(nombre), whatsapp, _texto_seguro(nota)]]},
        ).execute()


def alumnos_por_dia(dia: str) -> list[dict]:
    """Alumnos que tienen ese día (lunes, martes...) en dia_1..dia_N, con su contador."""
    buscado = _sin_tildes(dia)
    cab, filas = _leer_hoja()
    vistos, salida = set(), []
    for fila in filas:
        r = _resumen_de_fila(cab, fila)
        if not r["nombre"] or r["whatsapp"] in vistos:
            continue
        if any(_sin_tildes(d) == buscado for d in r["dias"]):
            vistos.add(r["whatsapp"])
            salida.append(r)
    return salida


# ---------------------------------------------------------------------------
# Alta de alumnos desde Telegram
# ---------------------------------------------------------------------------
# Nombres exactos de los desplegables de dia_1..dia_6 (pestaña "datos", columna F).
DIAS_ENTRENAMIENTO = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado")
MAX_DIAS = 6


def planes_disponibles() -> dict[str, dict]:
    """Planes de la pestaña "datos": {"hajimete": {"nombre": "hajimete", "sesiones": 8, "clases_semana": 2}, ...}."""
    valores = (
        _servicio_sheets().spreadsheets().values()
        .get(spreadsheetId=cargar_settings().google_sheet_id, range="datos!A2:D")
        .execute().get("values", [])
    )
    planes = {}
    for f in valores:
        f = f + [""] * (4 - len(f))
        nombre = f[0].strip()
        if nombre:
            planes[nombre.lower()] = {
                "nombre": nombre,
                "sesiones": _a_int_o_none(f[1]),
                "clases_semana": _a_int_o_none(f[3]),
            }
    return planes


def normalizar_dia(dia: str) -> str | None:
    """'miercoles' / 'MIÉRCOLES' -> 'Miércoles'. None si no es un día de entrenamiento."""
    buscado = _sin_tildes(dia)
    return next((d for d in DIAS_ENTRENAMIENTO if _sin_tildes(d) == buscado), None)


def registrar_alumno_nuevo(datos: dict) -> int:
    """
    Agrega al alumno en "Alumnos" (una fila nueva, con fórmulas copiadas).
    'datos' usa los encabezados del Sheet; 'dias' es una lista de días ya normalizados.
    Falla si el WhatsApp ya existe. Devuelve el número de fila.
    """
    whatsapp = _normalizar_whatsapp(datos.get("whatsapp", ""))
    if not whatsapp:
        raise ValueError("WhatsApp inválido")
    if buscar_fila_alumno(whatsapp):
        raise ValueError("Ese WhatsApp ya está registrado en el Sheet")
    fila = {k: v for k, v in datos.items() if k != "dias" and v not in (None, "")}
    fila["whatsapp"] = whatsapp
    for k in ("peso_kg", "porcentaje_grasa"):
        if isinstance(fila.get(k), float):
            # El Sheet está en español (coma decimal): 72.5 se escribe "72,5".
            v = fila[k]
            fila[k] = str(int(v)) if v.is_integer() else f"{v:.1f}".replace(".", ",")
    for i, dia in enumerate((datos.get("dias") or [])[:MAX_DIAS], start=1):
        fila[f"dia_{i}"] = dia
    return agregar_fila(fila)
