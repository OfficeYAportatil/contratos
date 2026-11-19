"""El workflow ``arrendamiento_contrato``: de la hoja de calculo al contrato firmado.

Un proceso por contrato. Gerencia lo crea desde el panel con el inmueble; el workflow carga el
inventario de la hoja, abre el formulario durable para el inquilino, compara lo capturado contra la
documentacion que ese inquilino envio por correo, reporta a gerencia por correo y, con la revision
aprobada, llena la plantilla y la deja en la carpeta de contratos.

El estado (formulario, revision, notas) vive en la instancia del workflow: los updates desde las
paginas web lo cambian, las ``wait_condition`` lo esperan y la query ``ver_proceso`` lo lee.
"""

import json
import re
from typing import Any
from urllib.parse import quote_plus

import mistralai.workflows as workflows
from mistralai.workflows import Depends
from mistralai.workflows.plugins.mistralai import activities as ia
from mistralai.workflows.plugins.mistralai.connectors import (
    ConnectorToolCallError,
    ToolCallClient,
    connector,
    uses_connectors,
)
from pydantic import BaseModel

from env.contratos import env as env_contratos
from worker.workflows import arrendamiento_utilidades as util

INSTANCIAS_MAXIMAS = 3
MODELO = "mistral-medium-latest"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MIME = "application/pdf"
HOJA_INMUEBLES = ("presets", "inmuebles")
HOJA_CONTRATOS = ("datosrenda",)
HOJA_PLANTILLAS = ("plantillas",)

google_drive = connector("google_drive_mcp")
gmail = connector("gmail")


class ConfigModel(BaseModel):
    """Los servicios fijos del proceso; la API los llena desde el entorno."""

    spreadsheet_id: str
    carpeta_contratos_id: str
    gerencia_email: str
    app_url: str


class ProcesoInput(BaseModel):
    process_id: str
    inmueble: str
    email_inquilino: str | None = None
    config: ConfigModel | None = None


def config_resuelta(config: ConfigModel | None) -> ConfigModel:
    if config is not None:
        return config
    valores = {
        "CONTRATOS_SPREADSHEET_ID": env_contratos.spreadsheet_id,
        "CONTRATOS_CARPETA_ID": env_contratos.carpeta_contratos_id,
        "CONTRATOS_GERENCIA_EMAIL": env_contratos.gerencia_email,
        "CONTRATOS_APP_URL": env_contratos.app_url,
    }
    faltantes = [nombre for nombre, valor in valores.items() if not valor]
    if faltantes:
        raise ValueError(f"Configuracion faltante del despliegue: {', '.join(faltantes)}")
    return ConfigModel(
        spreadsheet_id=valores["CONTRATOS_SPREADSHEET_ID"] or "",
        carpeta_contratos_id=valores["CONTRATOS_CARPETA_ID"] or "",
        gerencia_email=valores["CONTRATOS_GERENCIA_EMAIL"] or "",
        app_url=valores["CONTRATOS_APP_URL"] or "",
    )


def texto_de_resultado(resultado: Any) -> str:
    """El texto de los bloques ``content`` que regresa un ``call_tool`` del conector."""
    contenido = resultado.get("content") if isinstance(resultado, dict) else getattr(resultado, "content", None)
    pedazos = []
    if isinstance(contenido, list):
        for item in contenido:
            texto = item.get("text") if isinstance(item, dict) else getattr(item, "text", None)
            if isinstance(texto, str):
                pedazos.append(texto)
    return "\n".join(pedazos)


def contenido_json(resultado: Any) -> Any:
    texto = texto_de_resultado(resultado).strip()
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        inicio = texto.find("{")
        fin = texto.rfind("}")
        if inicio >= 0 and fin > inicio:
            try:
                return json.loads(texto[inicio : fin + 1])
            except json.JSONDecodeError:
                return None
        return None


CAMPOS_INQUILINO = util.CAMPOS_INQUILINO
CAMPOS_ARRENDADOR = util.CAMPOS_ARRENDADOR
CAMPOS_TODOS = util.CAMPOS_TODOS
GRUPOS_INQUILINO = util.GRUPOS_INQUILINO


def busca_hoja(hojas: dict[str, list[list[str]]], candidatos: tuple[str, ...]) -> str | None:
    for nombre in hojas:
        if util.normaliza(nombre) in candidatos:
            return nombre
    return None


def elegir_plantilla(plantillas: list[dict[str, str]], tipo_inmueble: str) -> dict[str, str] | None:
    """La plantilla por tipo de inmueble: Habitacional para casa/departamento, Comercial para lo demas."""
    habitacional = util.normaliza(tipo_inmueble) in (
        "casa",
        "departamento",
        "apartamento",
        "cuarto",
    )
    deseada = "habitacional" if habitacional else "comercial"
    respaldo: dict[str, str] | None = None
    for registro in plantillas:
        nombre_plantilla = next(
            (valor for clave, valor in registro.items() if "arrendamiento" in util.normaliza(clave)),
            "",
        )
        llave = nombre_plantilla if nombre_plantilla else " ".join(str(valor) for valor in registro.values())
        documento = next((str(valor) for valor in registro.values() if re.fullmatch(r"[\w-]{20,}", str(valor))), "")
        if not documento:
            continue
        candidata = {"nombre": nombre_plantilla or deseada, "documento_id": documento}
        if deseada in util.normaliza(llave):
            return candidata
        if respaldo is None:
            respaldo = candidata
    return respaldo


def _desenvuelve_lista(datos: Any) -> list[dict[str, Any]]:
    if isinstance(datos, list):
        return [item for item in datos if isinstance(item, dict)]
    if isinstance(datos, dict):
        for valor in datos.values():
            if isinstance(valor, list) and valor and all(isinstance(item, dict) for item in valor):
                return valor
    return []


def ejecuta_json(texto: str) -> list[dict[str, Any]]:
    """Lo que resulto ser una lista JSON dentro del texto de un call_tool; vacia si es otra cosa."""
    try:
        datos = json.loads(texto)
    except json.JSONDecodeError:
        datos = None
    if datos is None:
        inicio = texto.find("[")
        fin = texto.rfind("]")
        if inicio >= 0 and fin > inicio:
            try:
                datos = json.loads(texto[inicio : fin + 1])
            except json.JSONDecodeError:
                return []
    return _desenvuelve_lista(datos)


@workflows.activity(name="cargar_inventario")
async def cargar_inventario(spreadsheet_id: str, client: ToolCallClient = Depends(google_drive)) -> dict[str, Any]:
    """La base de datos completa como registros: inmuebles, contratos anteriores y plantillas."""
    resultado = await client.call_tool("read_file_content", {"file_id": spreadsheet_id})
    hojas = util.separa_hojas(texto_de_resultado(resultado))
    nombre_inmuebles = busca_hoja(hojas, HOJA_INMUEBLES)
    nombre_contratos = busca_hoja(hojas, HOJA_CONTRATOS)
    nombre_plantillas = busca_hoja(hojas, HOJA_PLANTILLAS)
    return {
        "inmuebles": util.hoja_a_registros(hojas, nombre_inmuebles) if nombre_inmuebles else [],
        "contratos": util.hoja_a_registros(hojas, nombre_contratos) if nombre_contratos else [],
        "plantillas": util.hoja_a_registros(hojas, nombre_plantillas) if nombre_plantillas else [],
    }


@workflows.activity(name="recopilar_documentos")
async def recopilar_documentos(
    email_inquilino: str, total_maximo: int = 15, client: ToolCallClient = Depends(gmail)
) -> list[dict[str, Any]]:
    """Lo que el inquilino ha enviado por correo: cuerpos, asuntos, remitentes y adjuntos.

    Un fallo del conector deja la lista vacia: gerencia revisa sin contraste documental y el proceso
    no se cae por un problema de lectura de Gmail.
    """
    if not email_inquilino:
        return []
    try:
        respuesta_busqueda = await client.call_tool(
            "search_threads",
            {"query": f"from:{email_inquilino}", "pageSize": max(total_maximo, 1)},
        )
    except ConnectorToolCallError:
        return []
    hilos = ejecuta_json(texto_de_resultado(respuesta_busqueda))
    documentos: list[dict[str, Any]] = []
    for hilo in hilos[:total_maximo]:
        hilo_dict = hilo if isinstance(hilo, dict) else {}
        hilo_id = hilo_dict.get("id")
        if not hilo_id:
            continue
        try:
            respuesta_hilo = await client.call_tool(
                "get_thread", {"threadId": hilo_id, "messageFormat": "FULL_CONTENT"}
            )
        except ConnectorToolCallError:
            continue
        datos = contenido_json(respuesta_hilo)
        mensajes = datos.get("messages") if isinstance(datos, dict) else None
        if not isinstance(mensajes, list):
            mensajes = hilo_dict.get("messages")
        if not isinstance(mensajes, list):
            continue
        for mensaje in mensajes:
            if not isinstance(mensaje, dict):
                continue
            remitente = mensaje.get("from", "")
            if isinstance(remitente, dict):
                remitente = remitente.get("email") or remitente.get("name") or ""
            adjuntos = mensaje.get("attachments") or []
            documentos.append(
                {
                    "hilo_id": hilo_id,
                    "de": str(remitente),
                    "asunto": str(mensaje.get("subject", "")),
                    "fecha": str(mensaje.get("date", "")),
                    "cuerpo": str(mensaje.get("plaintext_body") or mensaje.get("body") or "")[:12000],
                    "adjuntos": [
                        str(adjunto.get("filename", "")) if isinstance(adjunto, dict) else str(adjunto)
                        for adjunto in adjuntos
                    ],
                }
            )
    return documentos


@workflows.activity(name="extraer_campos_documentos")
async def extraer_campos_documentos(
    formulario: dict[str, str], documentos: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Los campos que los correos del inquilino sustentan, con el pasaje que los respalda."""
    if not documentos:
        return []
    resumen: list[str] = []
    for indice, documento in enumerate(documentos, start=1):
        adjuntos = ", ".join(documento.get("adjuntos") or []) or "sin adjuntos"
        resumen.append(
            f"[{indice}] De: {documento.get('de')} | Asunto: {documento.get('asunto')} | "
            f"Fecha: {documento.get('fecha')} | Adjuntos: {adjuntos}\n{documento.get('cuerpo', '')[:4000]}"
        )
    from mistralai.client import models as mistralai_models

    campos_deseados = ", ".join(f'"{campo}"' for campo in CAMPOS_INQUILINO)
    prompt = (
        "Estos son correos de un futuro inquilino con su documentacion (identificacion, comprobantes, "
        "datos del cónyuge y del aval, etc.). Extrae en formato JSON una lista con objetos {\"campo\", "
        "\"valor\", \"evidencia\"} por cada campo de esta lista sobre el que los correos den un dato real: "
        f"{campos_deseados}. Copia los valores tal cual aparecen, sin inventar; en evidencia cita de que "
        "correo o adjunto sale el dato. Si un campo no aparece, omitelo.\n\nCORREOS:\n" + "\n\n".join(resumen)
    )
    respuesta = await ia.mistralai_chat_complete(
        mistralai_models.ChatCompletionRequest(
            model=MODELO,
            messages=[mistralai_models.UserMessage(content=prompt)],
            temperature=0,
        )
    )
    contenido = ""
    if respuesta.choices and respuesta.choices[0].message and respuesta.choices[0].message.content:
        contenido = str(respuesta.choices[0].message.content)
    items = ejecuta_json(contenido)
    validos: list[dict[str, Any]] = []
    ya_en_formulario = {campo for campo, valor in formulario.items() if valor}
    for item in items:
        if isinstance(item, dict) and item.get("campo") in CAMPOS_INQUILINO and item.get("valor"):
            validos.append(
                {
                    "campo": str(item["campo"]),
                    "valor": str(item["valor"]),
                    "evidencia": str(item.get("evidencia", "")),
                    "capturado": str(item["campo"]) in ya_en_formulario,
                }
            )
    return validos


@workflows.activity(name="notificar_gmail")
async def enviar_correo(
    para: list[str],
    asunto: str,
    cuerpo: str,
    html: str = "",
    adjuntos: list[dict[str, str]] | None = None,
    client: ToolCallClient = Depends(gmail),
) -> bool:
    """Un correo por el conector de Gmail; falso cuando el conector fallo, para no tumbar al proceso."""
    arguments: dict[str, Any] = {"to": para, "subject": asunto, "body": cuerpo}
    if html:
        arguments["htmlBody"] = html
    if adjuntos:
        arguments["attachments"] = adjuntos
    try:
        await client.call_tool("send_email", arguments)
    except ConnectorToolCallError:
        return False
    return True


@workflows.activity(name="generar_contrato")
async def generar_contrato(
    plantilla_id: str,
    valores: dict[str, str],
    titulo: str,
    carpeta_id: str,
    client: ToolCallClient = Depends(google_drive),
) -> dict[str, Any]:
    """Descarga la plantilla, sustituye ``{{campos}}`` y la deja como ``docx`` en la carpeta de contratos."""
    descarga = await client.call_tool(
        "download_file_content", {"file_id": plantilla_id, "export_mime_type": DOCX_MIME}
    )
    archivo_plantilla = contenido_json(descarga)
    plantilla_base64 = ""
    if isinstance(archivo_plantilla, dict):
        plantilla_base64 = str(archivo_plantilla.get("content", "") or "")
    if not plantilla_base64:
        plantilla_base64 = texto_de_resultado(descarga).strip()
    lleno, pendientes = util.llena_plantilla_docx(plantilla_base64, valores)
    respuesta = await client.call_tool(
        "create_file",
        {
            "title": titulo,
            "parent_id": carpeta_id,
            "base64_content": lleno,
            "content_mime_type": DOCX_MIME,
        },
    )
    datos = contenido_json(respuesta)
    archivo_id = datos.get("id", "") if isinstance(datos, dict) else ""
    enlace = f"https://drive.google.com/file/d/{archivo_id}/view" if archivo_id else ""
    return {
        "id": archivo_id,
        "titulo": titulo,
        "enlace": enlace,
        "plantilla": plantilla_id,
        "pendientes": pendientes,
    }


@workflows.activity(name="exportar_pdf")
async def exportar_pdf(archivo_id: str, client: ToolCallClient = Depends(google_drive)) -> str:
    """El contrato ya creado exportado a PDF (base 64) para adjuntarlo; cadena vacia si falla."""
    try:
        descarga = await client.call_tool(
            "download_file_content", {"file_id": archivo_id, "export_mime_type": PDF_MIME}
        )
        archivo = contenido_json(descarga)
        if isinstance(archivo, dict) and archivo.get("content"):
            return str(archivo["content"])
    except ConnectorToolCallError:
        return ""
    return ""


def cuerpo_informe(informe: dict[str, Any], url_revision: str) -> str:
    lineas = [
        "Revision pendiente de un contrato de arrendamiento.",
        "",
        f"ABRIR PANEL: {url_revision}",
        "",
        f"Campos faltantes ({len(informe['faltantes'])}):",
    ]
    lineas += [f"  - {campo}" for campo in informe["faltantes"]] or ["  (ninguno)"]
    if informe["diferencias"]:
        lineas += ["", f"Campos que difieren de la documentacion ({len(informe['diferencias'])}):"]
        for campo, detalle in informe["diferencias"].items():
            lineas.append(f"  - {campo}: capturado='{detalle['capturado']}' | documento='{detalle['documento']}'")
    if informe["extra_del_documento"]:
        lineas += ["", "Datos que solo estan en la documentacion:"]
        for campo, detalle in informe["extra_del_documento"].items():
            lineas.append(f"  - {campo}: {detalle['valor']}")
    lineas += ["", f"Documentacion recibida: {len(informe['documentos_recibidos'])} mensajes comprobados."]
    return "\n".join(lineas)


@workflows.workflow.define(name="arrendamiento_contrato", search_keys=["process_id", "inmueble"])
@uses_connectors(google_drive, gmail)
class ArrendamientoContrato:
    """El proceso durable de un contrato: captura del inquilino, revision de gerencia, entrega."""

    def __init__(self) -> None:
        self.estado = "iniciando"
        self.inmueble_referencia = ""
        self.inmueble_datos: dict[str, str] = {}
        self.formulario: dict[str, str] = {}
        self.revision_datos: dict[str, str] = {}
        self.notas = ""
        self.aprobado = False
        self.solicitud_complete = False
        self.informe: dict[str, Any] = {}
        self.entrega: dict[str, Any] = {}

    @workflows.workflow.query(name="ver_proceso")
    def ver_proceso(self) -> dict[str, Any]:
        """La foto completa del proceso, para el formulario y el panel de gerencia."""
        return {
            "estado": self.estado,
            "inmueble": self.inmueble_referencia,
            "inmueble_datos": self.inmueble_datos,
            "formulario": self.formulario,
            "informe": self.informe,
            "revision_datos": self.revision_datos,
            "notas": self.notas,
            "aprobado": self.aprobado,
            "entrega": self.entrega,
        }

    @workflows.workflow.update(name="guardar_borrador")
    async def guardar_borrador(self, data: dict[str, str]) -> dict[str, Any]:
        """El autoguardado del formulario del inquilino; los campos desconocidos se ignoran."""
        self.formulario.update({campo: str(valor) for campo, valor in data.items() if campo in CAMPOS_TODOS})
        return {"guardado": True}

    @workflows.workflow.update(name="marcar_completo")
    async def marcar_completo(self, email_inquilino: str = "") -> dict[str, Any]:
        """El inquilino avisa (sin enviar nada de una vez) que ya esta completo hasta donde pueda."""
        if email_inquilino and not self.formulario.get("Email arrendatario"):
            self.formulario["Email arrendatario"] = email_inquilino.strip()
        self.solicitud_complete = True
        self.estado = "revision"
        return {"recibido": True}

    @workflows.workflow.update(name="registrar_revision")
    async def registrar_revision(
        self, data: dict[str, str], notas: str = "", aprobado: bool = False
    ) -> dict[str, Any]:
        """Gerencia corrige, completa y deja notas; con ``aprobado`` el contrato se genera y manda."""
        limpio = {campo: str(valor) for campo, valor in data.items() if campo in CAMPOS_TODOS}
        self.formulario.update(limpio)
        self.revision_datos = limpio
        self.notas = notas.strip()
        self.aprobado = aprobado
        return {"recibido": True, "aprobado": aprobado}

    @workflows.workflow.entrypoint
    async def run(self, params: ProcesoInput) -> dict[str, Any]:
        config = config_resuelta(params.config)
        self.inmueble_referencia = params.inmueble
        base_url = config.app_url.rstrip("/")
        formulario_url = f"{base_url}/api/f/{quote_plus(params.process_id)}"
        url_revision = f"{base_url}/api/gerencia/{quote_plus(params.process_id)}"
        await workflows.workflow.add_search_keys(
            {"process_id": params.process_id, "inmueble": params.inmueble, "estado": "cargando"}
        )

        inventario = await cargar_inventario(config.spreadsheet_id)
        self.inmueble_datos = util.busca_inmueble(inventario["inmuebles"], params.inmueble)
        referencia_inmueble = self.inmueble_datos.get("Inmueble en arrendamiento") or params.inmueble
        arrendador_anterior = util.busca_arrendador(inventario["contratos"], referencia_inmueble)
        base: dict[str, str] = {}
        for registro in (arrendador_anterior, self.inmueble_datos):
            for campo, valor in registro.items():
                if campo in CAMPOS_TODOS and valor and not base.get(campo):
                    base[campo] = valor
        base.setdefault("Inmueble en arrendamiento", referencia_inmueble)
        base.setdefault("Uso de suelo", self.inmueble_datos.get("Uso de suelo", ""))
        base.setdefault("Destino del inmueble", self.inmueble_datos.get("Uso de suelo", ""))
        if params.email_inquilino:
            base.setdefault("Email arrendatario", params.email_inquilino)
        self.formulario.update({campo: valor for campo, valor in base.items() if campo in CAMPOS_TODOS})
        self.estado = "captura"
        await workflows.workflow.add_search_keys({"estado": "captura"})

        ciclos = 0
        while True:
            await workflows.workflow.wait_condition(lambda: self.solicitud_complete)
            await workflows.workflow.add_search_keys({"estado": "revision"})

            correo_inquilino = self.formulario.get("Email arrendatario") or params.email_inquilino or ""
            documentos = await recopilar_documentos(correo_inquilino) if correo_inquilino else []
            extraido = await extraer_campos_documentos(self.formulario, documentos)
            self.informe = util.informe_revision(self.formulario, extraido, documentos)
            await enviar_correo(
                para=[config.gerencia_email],
                asunto=f"Revision de contrato: {params.inmueble} ({params.process_id[:8]})",
                cuerpo=cuerpo_informe(self.informe, url_revision),
            )

            self.aprobado = False
            self.notas = ""
            await workflows.workflow.wait_condition(lambda: self.aprobado or bool(self.notas))

            if self.aprobado:
                break
            ciclos += 1
            if ciclos >= INSTANCIAS_MAXIMAS:
                self.estado = "pausado"
                await workflows.workflow.add_search_keys({"estado": "pausado"})
                await enviar_correo(
                    para=[config.gerencia_email],
                    asunto=f"Contrato pausado: {params.inmueble} ({params.process_id[:8]})",
                    cuerpo=(
                        f"El formulario sigue pendiente tras {INSTANCIAS_MAXIMAS} devoluciones.\n"
                        f"Ultimas notas: {self.notas}\nPanel: {url_revision}"
                    ),
                )
                return {"estado": "pausado", "notas": self.notas, "url_revision": url_revision}
            if correo_inquilino:
                await enviar_correo(
                    para=[correo_inquilino],
                    asunto=f"Datos pendientes: {params.inmueble}",
                    cuerpo=(
                        "Revisamos tu formulario y tu documentacion. Ajusta lo siguiente en el formulario, "
                        "despues pulsa 'Ya complete' de nuevo:\n\n"
                        f"{self.notas or 'Faltan datos en el formulario.'}\n\nFORMULARIO: {formulario_url}"
                    ),
                )
            self.solicitud_complete = False
            self.estado = "captura"
            await workflows.workflow.add_search_keys({"estado": "captura"})

        self.estado = "generando"
        await workflows.workflow.add_search_keys({"estado": "generando"})

        valores = dict(self.formulario)
        valores.update(util.calcula_derivados(valores))
        plantilla = elegir_plantilla(
            inventario["plantillas"], valores.get("Tipo de inmueble", "") or self.inmueble_datos.get("Tipo de inmueble", "")
        )
        if plantilla is None:
            raise ValueError("La hoja PLANTILLAS no tiene plantillas con documento id")
        tipo_plantilla = "Habitacional" if "habitacional" in util.normaliza(plantilla["nombre"]) else "Comercial"
        titulo = f"{valores.get('Nombre arrendatario') or 'Sin nombre'} - Arrendamiento{tipo_plantilla}"
        archivo = await generar_contrato(
            plantilla["documento_id"],
            valores,
            titulo,
            config.carpeta_contratos_id,
        )
        pdf_base64 = await exportar_pdf(archivo["id"]) if archivo["id"] else ""
        adjuntos = (
            [{"filename": f"{titulo}.pdf", "content": pdf_base64, "mimeType": PDF_MIME}]
            if pdf_base64
            else None
        )
        self.entrega = {**archivo, "pdf": bool(pdf_base64)}
        mensaje_gerencia = (
            f"Contrato generado desde la plantilla {plantilla['nombre']}.\n"
            f"Documento: {archivo['enlace'] or '(ver carpeta de contratos)'}\n"
            f"Campos sin valor: {', '.join(archivo['pendientes']) or 'ninguno'}"
        )
        if self.notas:
            mensaje_gerencia += f"\nNotas de la revision: {self.notas}"
        await enviar_correo(
            para=[config.gerencia_email],
            asunto=f"Contrato generado: {titulo}",
            cuerpo=mensaje_gerencia,
            adjuntos=adjuntos,
        )
        await workflows.workflow.add_search_keys({"estado": "entregado"})
        return self.entrega
