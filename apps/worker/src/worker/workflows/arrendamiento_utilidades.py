"""Funciones puras del flujo de contratos de arrendamiento.

Nada aqui toca la red ni un conector: la hoja de calculo llega como texto, la plantilla como
``docx`` en base 64 y todo lo demas son diccionarios de cadenas. Asi el llenado del contrato, el
parseo de la base de datos y los numeros a letras se prueban sin workflow ni credenciales.
"""

import base64
import csv
import datetime
import io
import re
import unicodedata
import zipfile
from decimal import Decimal, InvalidOperation


CAMPOS_INQUILINO: tuple[str, ...] = (
    "Nombre arrendatario",
    "Teléfono arrendatario",
    "Domicilio particular arrendatario",
    "Email arrendatario",
    "INE arrendatario",
    "RFC arrendatario",
    "CURP arrendatario",
    "Conyuge arrendaario",
    "Conyuge Telefono",
    "Conyuge eMail",
    "Conyuge IFE",
    "Conyuge RFC",
    "Conyuge Curp",
    "Aval nombre",
    "Aval teléfono",
    "Aval domicilio particular",
    "Aval ciuidad estado domicilio particular",
    "Email aval",
    "INE aval",
    "RFC aval",
    "CURP aval",
    "Inmueble en garantía",
    "Inmueble en garantía cuenta predial",
    "Inmueble en garantía cuenta catastarl",
    "Inmueble en garantía R20",
    "Destino del inmueble",
)

CAMPOS_ARRENDADOR: tuple[str, ...] = (
    "Arrendador",
    "RFC Arrendador",
    "Página Web",
    "Arco eMail",
    "Arrendador Teléfono",
    "Apoderado Arrendador",
    "Domicilio legal arrendador",
    "Banco",
    "Cuenta Bancaria",
    "Cuenta SPEI",
    "WhatsApp Pagos",
    "eMail recepción de pagos",
    "Inmueble en arrendamiento",
    "Tipo de inmueble",
    "Uso de suelo",
    "GeneraIVA",
    "Fecha Firma",
    "Importe DEG",
    "Renta mensual bruta",
    "Tasa Interes",
)

CAMPOS_TODOS = CAMPOS_INQUILINO + CAMPOS_ARRENDADOR

GRUPOS_INQUILINO: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Arrendatario",
        (
            "Nombre arrendatario",
            "Teléfono arrendatario",
            "Domicilio particular arrendatario",
            "Email arrendatario",
            "INE arrendatario",
            "RFC arrendatario",
            "CURP arrendatario",
        ),
    ),
    (
        "Cónyuge o codeudor del arrendatario (opcional)",
        ("Conyuge arrendaario", "Conyuge Telefono", "Conyuge eMail", "Conyuge IFE", "Conyuge RFC", "Conyuge Curp"),
    ),
    (
        "Obligado solidario (aval)",
        (
            "Aval nombre",
            "Aval teléfono",
            "Aval domicilio particular",
            "Aval ciuidad estado domicilio particular",
            "Email aval",
            "INE aval",
            "RFC aval",
            "CURP aval",
        ),
    ),
    (
        "Inmueble en garantía",
        (
            "Inmueble en garantía",
            "Inmueble en garantía cuenta predial",
            "Inmueble en garantía cuenta catastarl",
            "Inmueble en garantía R20",
        ),
    ),
    ("Uso", ("Destino del inmueble",)),
)

def normaliza(texto: str) -> str:
    """Minusculas sin acentos ni espacios sobrantes, para comparar inmuebles por nombre."""
    sin_acentos = unicodedata.normalize("NFKD", texto or "")
    return "".join(caracter for caracter in sin_acentos if not unicodedata.combining(caracter)).strip().lower()


VACIAS = {"", "nan", "nat", "n/d", "none", "null", "na"}


def celda(texto: str) -> str:
    """Una celda de la representacion textual de la hoja: vacia cuando la hoja dice ``nan`` o similar."""
    limpio = (texto or "").strip()
    return "" if limpio.lower() in VACIAS else limpio


def separa_hojas(texto_hoja: str) -> dict[str, list[list[str]]]:
    """Parte el texto de ``read_file_content`` del spreadsheet en hojas, y cada hoja en filas CSV.

    El texto trae bloques ``Sheet: <nombre>`` seguidos de renglones CSV; se entiende cada bloque
    como un CSV independiente porque los propios renglones citan comillas.
    """
    hojas: dict[str, list[list[str]]] = {}
    nombre = ""
    lineas: list[str] = []
    for linea in texto_hoja.splitlines():
        coincidencia = re.match(r"^Sheet:\s*(.+?)\s*$", linea)
        if coincidencia:
            if nombre:
                hojas[nombre] = _filas_csv(lineas)
            nombre = coincidencia.group(1)
            lineas = []
            continue
        if nombre:
            lineas.append(linea)
    if nombre:
        hojas[nombre] = _filas_csv(lineas)
    return hojas


def _filas_csv(lineas: list[str]) -> list[list[str]]:
    return [[celda(campo) for campo in fila] for fila in csv.reader(lineas) if fila]


def hoja_a_registros(hojas: dict[str, list[list[str]]], nombre: str) -> list[dict[str, str]]:
    """Una hoja como renglones nombrados por su encabezado; encabezados sin nombre se descartan."""
    filas = hojas.get(nombre) or []
    if not filas:
        return []
    encabezados = filas[0]
    registros = []
    for fila in filas[1:]:
        registro = {
            encabezados[i]: fila[i] if i < len(fila) else ""
            for i in range(len(encabezados))
            if encabezados[i]
        }
        if any(registro.values()):
            registros.append(registro)
    return registros


def busca_inmueble(registros_inmuebles: list[dict[str, str]], referencia: str) -> dict[str, str]:
    """La fila de inmuebles que menciona la referencia, por cualquiera de sus columnas.

    Un campo cuyo valor contiene la referencia, o viceversa, cuenta: gerencia nombra al inmueble a
    mano y la hoja escribe direcciones completas.
    """
    referencia_normalizada = normaliza(referencia)
    if not referencia_normalizada:
        return {}
    for registro in registros_inmuebles:
        for valor in registro.values():
            if normaliza(valor) == referencia_normalizada:
                return registro
    for registro in registros_inmuebles:
        for valor in registro.values():
            valor_normalizado = normaliza(valor)
            if valor_normalizado and (
                referencia_normalizada in valor_normalizado or valor_normalizado in referencia_normalizada
            ):
                return registro
    return {}


def busca_arrendador(registros_contratos: list[dict[str, str]], referencia: str) -> dict[str, str]:
    """La ultima fila de contratos anteriores del mismo inmueble, para prellenar al arrendador."""
    if not registros_contratos or not normaliza(referencia):
        return {}
    con_inmueble = [
        registro
        for registro in registros_contratos
        if normaliza(registro.get("Inmueble en arrendamiento") or "").find(normaliza(referencia)) >= 0
    ]
    if not con_inmueble:
        return {}
    return con_inmueble[-1]


_UNIDADES = [
    "", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve",
    "diez", "once", "doce", "trece", "catorce", "quince", "dieciseis", "diecisiete",
    "dieciocho", "diecinueve", "veinte", "veintiuno", "veintidos", "veintitres",
    "veinticuatro", "veinticinco", "veintiseis", "veintisiete", "veintiocho", "veintinueve",
]
_DECENAS = ["", "", "veinte", "treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta", "noventa"]
_CENTENAS = [
    "", "ciento", "doscientos", "trescientos", "cuatrocientos", "quinientos",
    "seiscientos", "setecientos", "ochocientos", "novecientos",
]


def _menor_mil(numero: int) -> str:
    if numero < 0 or numero >= 1000:
        raise ValueError(f"numero fuera de rango: {numero}")
    if numero == 0:
        return "cero"
    if numero == 100:
        return "cien"
    partes: list[str] = []
    centena, resto = divmod(numero, 100)
    if centena:
        partes.append(_CENTENAS[centena])
    if resto:
        if resto <= 29:
            partes.append(_UNIDADES[resto])
        else:
            decena, unidad = divmod(resto, 10)
            partes.append(_DECENAS[decena] if not unidad else f"{_DECENAS[decena]} y {_UNIDADES[unidad]}")
    return " ".join(partes)


def _entero_a_letras(numero: int) -> str:
    if numero > 999_999_999_999:
        raise ValueError(f"numero demasiado grande: {numero}")
    if numero < 1000:
        return _menor_mil(numero)
    partes: list[str] = []
    millones, resto = divmod(numero, 1_000_000)
    if millones == 1:
        partes.append("un millon")
    elif millones:
        partes.append(f"{_entero_a_letras(millones)} millones")
    miles, unidades = divmod(resto, 1000)
    if miles:
        partes.append("mil" if miles == 1 else f"{_menor_mil(miles)} mil".replace("uno mil", "un mil"))
    if unidades:
        partes.append(_menor_mil(unidades))
    return " ".join(partes)


def numero_a_letras(numero: int) -> str:
    """Un entero en espanol mexicano, para los campos ``Texto ...`` del contrato."""
    texto = _entero_a_letras(numero)
    texto = re.sub(r"\bveintiuno\b", "veintiun", texto)
    texto = re.sub(r"\buno\b", "un", texto)
    return texto


def _a_decimal(monto_texto: str) -> Decimal | None:
    limpio = (monto_texto or "").strip().replace("$", "").replace(",", "")
    if not limpio:
        return None
    try:
        return Decimal(limpio)
    except InvalidOperation:
        return None


def _monto_2024(monto: Decimal) -> str:
    return f"{monto:,.2f}"


def dinero_a_letras(monto_texto: str) -> str:
    """``152345.6`` -> ``CIENTO CINCUENTA Y DOS MIL TRESCIENTOS CUARENTA Y CINCO PESOS 60/100 M.N.``"""
    monto = _a_decimal(monto_texto)
    if monto is None:
        return ""
    entero = int(monto)
    centavos = int((monto - entero).quantize(Decimal("0.01")) * 100)
    letras = numero_a_letras(entero).upper() if entero else "CERO"
    return f"{letras} PESOS {centavos:02d}/100 M.N."


def _fecha_legible(fecha: datetime.date) -> str:
    MESES = [
        "", "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
    ]
    return f"{fecha.day} de {MESES[fecha.month]} de {fecha.year}"


def _parsea_fecha(fecha_texto: str) -> datetime.date | None:
    limpio = (fecha_texto or "").strip()
    if not limpio:
        return None
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.datetime.strptime(limpio[:10], formato).date()
        except ValueError:
            continue
    try:
        return datetime.datetime.fromisoformat(limpio).date()
    except ValueError:
        return None


def calcula_derivados(datos: dict[str, str]) -> dict[str, str]:
    """Los campos del contrato que no se capturan: montos a letras, vacaciones y fechas.

    Entrada: los datos ya reunidos (arrendador + inmueble + revision). Salida solo con los campos
    calculados; el que llama los mezcla con los capturados.
    """
    derivados: dict[str, str] = {}
    renta = _a_decimal(datos.get("Renta mensual bruta", ""))
    if renta is not None:
        derivados["Renta mensual bruta"] = _monto_2024(renta)
        derivados["Texto renta mensual bruta"] = dinero_a_letras(str(renta))
        valor_periodo = renta * 12
        derivados["Valor del periodo"] = _monto_2024(valor_periodo)
        derivados["Texto valor del periodo"] = dinero_a_letras(str(valor_periodo))
    if renta is not None:
        descuento = _a_decimal(datos.get("Descuento PP", "")) or Decimal("0")
        derivados["Descuento PP"] = _monto_2024(descuento)
        jue_total = renta - descuento
        derivados["Total con dcto pp"] = _monto_2024(jue_total)
        derivados["Texto total con dcto pp"] = dinero_a_letras(str(jue_total))
    importe_deg = _a_decimal(datos.get("Importe DEG", ""))
    if importe_deg is not None:
        derivados["Importe DEG"] = _monto_2024(importe_deg)
        derivados["Texto DEG"] = dinero_a_letras(str(importe_deg))
    fecha = _parsea_fecha(datos.get("Fecha Firma", ""))
    if fecha is not None:
        derivados["Fecha Firma"] = _fecha_legible(fecha)
        derivados["Día de pago mensual"] = str(fecha.day)
        derivados["Texto día de pago mensual"] = numero_a_letras(fecha.day).upper()
        derivados["Año firma texto"] = numero_a_letras(fecha.year).upper()
    return derivados


TOKEN = re.compile(r"\{\{\s*(.+?)\s*\}\}")
_PARAFO = re.compile(r"<w:p\b.*?</w:p>", re.DOTALL)
_TEXTO = re.compile(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", re.DOTALL)


def _escapa_xml(texto: str) -> str:
    return texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _apertura_vacia(pieza: str) -> str:
    """La etiqueta ``<w:t ...>`` original cerrada vacia: ``<w:t></w:t>``."""
    apertura = pieza[: pieza.index(">") + 1]
    return f"{apertura}</w:t>"


def _rellena_parte_xml(xml: bytes, valores: dict[str, str]) -> tuple[bytes, set[str]]:
    """Una parte OOXML: reemplaza ``{{token}}`` respetando la division en parrafos.

    Un token puede quedar partido por varios ``runs``. En cada parrafo se concadena el texto de sus
    ``w:t``, se reemplaza ahi, y el resultado entra al primer ``w:t`` del parrafo, dejando vacios los
    demas. Regresa los tokens que quedaron sin valor.
    """
    texto = xml.decode("utf-8")
    pendientes = {
        token
        for parrafo in _PARAFO.findall(texto)
        for token in TOKEN.findall("".join(_TEXTO.findall(parrafo)))
    } - set(valores)

    def reemplaza_parrafo(coincidencia: re.Match) -> str:
        parrafo = coincidencia.group(0)
        extraido = "".join(_TEXTO.findall(parrafo))
        if not extraido:
            return parrafo

        def sustituye(coincidencia_token: re.Match) -> str:
            return str(valores.get(coincidencia_token.group(1), coincidencia_token.group(0)))

        reemplazo = TOKEN.sub(sustituye, extraido)
        if reemplazo == extraido:
            return parrafo
        piezas = list(_TEXTO.finditer(parrafo))
        resultado: list[str] = []
        ultimo = 0
        for indice, pieza in enumerate(piezas):
            resultado.append(parrafo[ultimo : pieza.start()])
            if indice == 0:
                apertura = pieza.group(0)[: pieza.group(0).index(">") + 1]
                resultado.append(f"{apertura}{_escapa_xml(reemplazo)}</w:t>")
            else:
                resultado.append(_apertura_vacia(pieza.group(0)))
            ultimo = pieza.end()
        resultado.append(parrafo[ultimo:])
        return "".join(resultado)

    return _PARAFO.sub(reemplaza_parrafo, texto).encode("utf-8"), pendientes


PARTES_CON_TEXTO = re.compile(r"^word/(document|header\d*|footer\d*)\.xml$")


def llena_plantilla_docx(docx_base64: str, valores: dict[str, str]) -> tuple[str, list[str]]:
    """Llena ``{{campos}}`` de una plantilla ``docx`` (base 64) y regresa el nuevo ``docx`` en base 64.

    Tambien regresa la lista de tokens que quedaron sin valor, para el informe de gerencia.
    """
    fuente = io.BytesIO(base64.b64decode(docx_base64))
    destino = io.BytesIO()
    pendientes: set[str] = set()
    with zipfile.ZipFile(fuente) as origen, zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as salida:
        for info in origen.infolist():
            contenido = origen.read(info.filename)
            if PARTES_CON_TEXTO.match(info.filename):
                contenido, pendientes_parte = _rellena_parte_xml(contenido, valores)
                pendientes.update(pendientes_parte)
            salida.writestr(info, contenido)
    return base64.b64encode(destino.getvalue()).decode("ascii"), sorted(pendientes)


def tokens_de_plantilla(docx_base64: str) -> list[str]:
    """Todos los ``{{tokens}}`` que la plantilla pide, sin repetir y en orden de aparicion.

    Se leen del texto concadenado por parrafo, porque un token puede quedar partido entre ``runs``.
    """
    fuente = io.BytesIO(base64.b64decode(docx_base64))
    vistos: list[str] = []
    with zipfile.ZipFile(fuente) as origen:
        for info in origen.infolist():
            if not PARTES_CON_TEXTO.match(info.filename):
                continue
            texto = origen.read(info.filename).decode("utf-8")
            for parrafo in _PARAFO.findall(texto):
                for token in TOKEN.findall("".join(_TEXTO.findall(parrafo))):
                    if token not in vistos:
                        vistos.append(token)
    return vistos


def informe_revision(
    formulario: dict[str, str],
    extraido: list[dict[str, str]],
    documentos: list[dict[str, str]],
) -> dict[str, object]:
    """La comparacion capturado contra documento: que falta, que difiere, que correos llegaron."""
    capturado = {campo: formulario.get(campo, "") for campo in CAMPOS_INQUILINO}
    faltantes = [campo for campo, valor in capturado.items() if not valor]
    documento = {item["campo"]: item for item in extraido}
    diferencias: dict[str, dict[str, str]] = {}
    extra: dict[str, dict[str, str]] = {}
    for campo, item in documento.items():
        valor = item["valor"]
        if not valor:
            continue
        capturado_valor = capturado.get(campo, "")
        if not capturado_valor:
            extra[campo] = {"valor": valor, "evidencia": item.get("evidencia", "")}
        elif normaliza(valor) != normaliza(capturado_valor):
            diferencias[campo] = {
                "capturado": capturado_valor,
                "documento": valor,
                "evidencia": item.get("evidencia", ""),
            }
    return {
        "capturado": capturado,
        "documento": documento,
        "faltantes": faltantes,
        "diferencias": diferencias,
        "extra_del_documento": extra,
        "documentos_recibidos": [
            {
                "de": item.get("de", ""),
                "asunto": item.get("asunto", ""),
                "fecha": item.get("fecha", ""),
                "adjuntos": item.get("adjuntos", []),
            }
            for item in documentos
        ],
    }

