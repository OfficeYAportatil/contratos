"""El panel de gerencia: ``/api/gerencia``.

Entra con la clave del despliegue (cookie de sesi\u00f3n); ah\u00ed crea procesos por inmueble, revisa
cada uno contra su informe documental, corrige o completa cualquier campo y aprueba, lo que dispara
el llenado autom\u00e1tico del contrato.
"""

import html
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from api.routers.api.portal import _ayuda as ayuda
from worker.workflows.arrendamiento import (
    CAMPOS_ARRENDADOR,
    CAMPOS_INQUILINO,
)

router = APIRouter()


def _login_page(error: str = "") -> HTMLResponse:
    aviso = f'<div class="mensaje">{html.escape(error)}</div>' if error else ""
    return ayuda.pagina(
        "Gerencia",
        f"""<h1>Panel de gerencia</h1>
<form method="post" action="/api/gerencia">
  <div class="campo"><label>Clave de acceso</label>
  <input type="password" name="clave"></div>
  {aviso}
  <p><button class="primario" type="submit">Entrar</button></p>
</form>""",
    )


def _lista_procesos(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<p>Todav\u00eda no hay procesos. Crea el primero con el inmueble de la hoja.</p>"
    filas = []
    for fila in rows:
        enlace = f'/api/gerencia/{html.escape(str(fila["process_id"]))}'
        inmueble = html.escape(str(fila.get("inmueble") or fila["process_id"]))
        estado = html.escape(str(fila.get("estado") or "?"))
        filas.append(
            f'<tr><td><a href="{enlace}">{inmueble}</a></td>'
            f'<td><span class="estado">{estado}</span></td>'
            f"<td>{html.escape(str(fila.get('codigo') or ''))}</td></tr>"
        )
    return (
        '<table class="tabla"><tr><th>Inmueble</th><th>Estado</th><th>Proceso</th></tr>'
        + "".join(filas)
        + "</table>"
    )


@router.get("", include_in_schema=False)
async def panel(request: Request) -> HTMLResponse:
    if not ayuda.sesion_valida(request):
        return _login_page()
    rows = []
    try:
        respuesta = await ayuda.mistral_client().workflows.runs.list_runs_async(
            workflow_identifier="arrendamiento_contrato", page_size=30
        )
        resultado = getattr(respuesta, "result", None) if respuesta else None
        for ejecucion in getattr(resultado, "executions", []) or []:
            proceso = await ayuda.ver_proceso(str(ejecucion.execution_id))
            if proceso is not None:
                rows.append(
                    {
                        "process_id": str(ejecucion.execution_id),
                        "inmueble": proceso.get("inmueble", ""),
                        "estado": proceso.get("estado", ""),
                        "codigo": str(ejecucion.execution_id)[:8],
                    }
                )
    except Exception:
        pass
    cuerpo = f"""
<h1>Panel de gerencia</h1>
<div class="grupo"><h2>Nuevo proceso</h2>
<form method="post" action="/api/gerencia/nuevo">
  <div class="campo"><label>Inmueble (como aparece en la hoja: direcci\u00f3n o tipo)</label>
  <input type="text" name="inmueble" style="width:100%"></div>
  <div class="campo"><label>Correo del futuro inquilino (opcional, para buscar su documentaci\u00f3n)</label>
  <input type="email" name="email" style="width:100%"></div>
  <p><button class="primario" type="submit">Crear proceso</button></p>
</form></div>
<h2>Procesos</h2>
{_lista_procesos(rows)}
"""
    return ayuda.pagina("Gerencia \u00b7 Contratos", cuerpo)


@router.post("", include_in_schema=False)
async def login(request: Request) -> HTMLResponse:
    formulario = await request.form()
    clave = str(formulario.get("clave") or "")
    if not ayuda.env_contratos.review_passcode:
        return _login_page("No hay clave configurada en el despliegue (CONTRATOS_REVIEW_PASSCODE).")
    if clave != ayuda.env_contratos.review_passcode:
        return _login_page("Clave incorrecta.")
    respuesta = RedirectResponse(url="/api/gerencia", status_code=303)
    respuesta.set_cookie(
        ayuda.COOKIE_GERENCIA,
        ayuda.clave_de_sesion(),
        max_age=7 * 24 * 3600,
        httponly=True,
        samesite="lax",
    )
    return respuesta


@router.post("/nuevo", include_in_schema=False)
async def nuevo(request: Request) -> Response:
    if not ayuda.sesion_valida(request):
        return _login_page()
    formulario = await request.form()
    inmueble = str(formulario.get("inmueble") or "").strip()
    if not inmueble:
        return _login_page()
    process_id = str(uuid.uuid4())
    email = str(formulario.get("email") or "").strip()
    logrado, error = await ayuda.iniciar_proceso(process_id, inmueble, email)
    if not logrado:
        return _login_page(error)
    return RedirectResponse(url=f"/api/gerencia/{process_id}", status_code=303)


@router.get("/{process_id}", include_in_schema=False)
async def revision(process_id: str, request: Request) -> HTMLResponse:
    if not ayuda.sesion_valida(request):
        return _login_page()
    proceso = await ayuda.ver_proceso(process_id)
    if proceso is None:
        return ayuda.pagina_error("Proceso no encontrado", f"No hay proceso con id {process_id}.")

    estado = str(proceso.get("estado", ""))
    valores = dict(proceso.get("formulario") or {})
    informe = dict(proceso.get("informe") or {})
    documento = dict(informe.get("documento") or {})
    diferencias = set(informe.get("diferencias") or {})
    faltantes = list(informe.get("faltantes") or [])
    documentos = list(informe.get("documentos_recibidos") or [])
    entrega = dict(proceso.get("entrega") or {})

    fila_capturada = []
    for campo in CAMPOS_INQUILINO:
        sugerencia = (documento.get(campo) or {}).get("valor", "")
        clase = ' class="dif"' if campo in diferencias else ""
        fila_capturada.append(
            f'<div class="campo"{clase}><label>{html.escape(campo)}</label>'
            f'<textarea rows="2" name="campos[{html.escape(campo)}]">{html.escape(valores.get(campo) or "")}</textarea>'
            + (
                f'<div class="aviso">Del documento: {html.escape(sugerencia)}'
                f" <em>({html.escape((documento.get(campo) or {}).get('evidencia', ''))})</em></div>"
                if sugerencia and sugerencia != (valores.get(campo) or "")
                else ""
            )
            + "</div>"
        )
    fila_arrendador = [
        ayuda.campo_formulario(campo, valores.get(campo, "")) for campo in CAMPOS_ARRENDADOR
    ]

    tabla_documentos = (
        "<table class=\"tabla\"><tr><th>De</th><th>Asunto</th><th>Fecha</th><th>Adjuntos</th></tr>"
        + "".join(
            "<tr><td>"
            + html.escape(str(item.get("de") or ""))
            + "</td><td>"
            + html.escape(str(item.get("asunto") or ""))
            + "</td><td>"
            + html.escape(str(item.get("fecha") or ""))
            + "</td><td>"
            + html.escape(", ".join(str(adjunto) for adjunto in item.get("adjuntos") or []))
            + "</td></tr>"
            for item in documentos
        )
        + "</table>"
        if documentos
        else "<p>Sin correos de documentaci\u00f3n para el correo del inquilino.</p>"
    )

    enlace_formulario = f"{ayuda.url_formulario(process_id)}"
    if entrega.get("enlace"):
        bandera_entrega = (
            f'<div class="ok">Contrato generado: <a href="{html.escape(str(entrega["enlace"]))}">'
            f"{html.escape(str(entrega.get('titulo') or 'documento'))}</a></div>"
        )
    else:
        bandera_entrega = ""
    cuerpo = f"""
<h1>Revisi\u00f3n \u00b7 {html.escape(str(proceso.get('inmueble') or ''))}</h1>
<p><span class="estado">{html.escape(estado)}</span> &nbsp; proceso {html.escape(process_id[:8])}</p>
{bandera_entrega}
<p>Formulario del inquilino: <a href="{html.escape(enlace_formulario)}">{html.escape(enlace_formulario)}</a></p>
<h2>Comparación con la documentación</h2>
{tabla_documentos}
<form method="post" action="/api/gerencia/{html.escape(process_id)}">
<h2>Datos del arrendatario, aval y garant\u00eda</h2>
{''.join(fila_capturada)}
<h2>Arrendador y datos económicos</h2>
{''.join(fila_arrendador)}
<div class="campo"><label>Notas para el inquilino (lo que debe corregir)</label>
<textarea rows="4" name="notas">{html.escape(str(proceso.get('notas') or ''))}</textarea></div>
<p><button class="secundario" type="submit" name="aprobado" value="no">Guardar correcciones y devolver al inquilino</button>
<button class="primario" type="submit" name="aprobado" value="si">Todo bien: generar contrato</button></p>
</form>
"""
    return ayuda.pagina("Gerencia \u00b7 revisi\u00f3n", cuerpo)


@router.post("/{process_id}", include_in_schema=False)
async def registrar(process_id: str, request: Request) -> Response:
    if not ayuda.sesion_valida(request):
        return _login_page()
    formulario = await request.form()
    campos: dict[str, str] = {}
    for llave in formulario.keys():
        if llave.startswith("campos[") and llave.endswith("]"):
            campo = llave[len("campos[") : -1]
            if campo in CAMPOS_INQUILINO or campo in CAMPOS_ARRENDADOR:
                campos[campo] = str(formulario.get(llave) or "")
    notas = str(formulario.get("notas") or "")
    aprobado = str(formulario.get("aprobado") or "no") == "si"
    logrado, error = await ayuda.aplicar_update(
        process_id,
        "registrar_revision",
        {"data": campos, "notas": notas, "aprobado": aprobado},
    )
    if not logrado:
        return ayuda.pagina_error("No se pudo registrar la revisi\u00f3n", error)
    if aprobado:
        return ayuda.pagina(
            "Contrato en generaci\u00f3n",
            f'<h1>Revisi\u00f3n aprobada</h1><div class="ok">El workflow est\u00e1 llenando el contrato. '
            f'Cuando termine llegar\u00e1 un correo con el documento.</div>'
            f"<p><a href=\"/api/gerencia\">Volver al panel</a></p>",
        )
    return RedirectResponse(url=f"/api/gerencia/{process_id}", status_code=303)
