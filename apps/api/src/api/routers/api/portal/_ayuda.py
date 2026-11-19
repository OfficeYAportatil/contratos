"""Helpers privados del portal: shell HTML, sesion de gerencia y llamadas al workflow.

Los modulos que empiezan con ``_`` no son rutas: solo comparten lo que ``f.py`` y ``gerencia.py``
necesitan. Las llamadas al API de workflows usan el cliente de la app (``utils.mistral``).
"""

import hashlib
import html
from typing import Any

from env.contratos import env as env_contratos
from env.workflows import env as env_workflows
from fastapi import Request
from fastapi.responses import HTMLResponse
from utils.mistral import mistral_client

COOKIE_GERENCIA = "gerencia_sesion"
WORKFLOW_NAME = "arrendamiento_contrato"


def url_panel() -> str:
    base = (env_contratos.app_url or "").rstrip("/")
    return f"{base}/api/gerencia" if base else "/api/gerencia"


def url_formulario(process_id: str) -> str:
    base = (env_contratos.app_url or "").rstrip("/")
    return f"{base}/api/f/{process_id}" if base else f"/api/f/{process_id}"


def clave_de_sesion() -> str:
    return hashlib.sha256(("gerencia:" + (env_contratos.review_passcode or "")).encode()).hexdigest()[:32]


def sesion_valida(request: Request) -> bool:
    """Solo con clave configurada y cookie correcta; sin clave configurada, cerrado."""
    if not env_contratos.review_passcode:
        return False
    return request.cookies.get(COOKIE_GERENCIA) == clave_de_sesion()


ESTILO = """
:root { color-scheme: light; }
body { font-family: system-ui, sans-serif; margin: 0; background: #f4f4f2; color: #222; }
main { max-width: 960px; margin: 2rem auto; padding: 0 1rem; }
h1 { font-size: 1.4rem; } h2 { font-size: 1.1rem; margin-top: 2rem; }
.campo { margin: 0.6rem 0; }
.campo label { display: block; font-size: 0.85rem; color: #444; margin-bottom: 0.2rem; }
.campo input[type=text], .campo input[type=email], .campo textarea {
  width: 100%; box-sizing: border-box; padding: 0.45rem 0.6rem; border: 1px solid #bbb;
  border-radius: 6px; font: inherit; background: #fff;
}
.grupo { background: #fff; border: 1px solid #ddd; border-radius: 10px; padding: 1rem 1.2rem; margin: 1rem 0; }
button { padding: 0.55rem 1.1rem; border: 0; border-radius: 6px; font: inherit; cursor: pointer; }
.primario { background: #1a5fb4; color: #fff; }
.secundario { background: #e5e5e2; }
.aviso { background: #fdf3d7; border: 1px solid #e0c36a; border-radius: 8px; padding: 0.8rem 1rem; margin: 1rem 0; }
.ok { background: #e4f0e0; border: 1px solid #86b569; border-radius: 8px; padding: 0.8rem 1rem; }
.estado { display: inline-block; padding: 0.15rem 0.6rem; border-radius: 999px; background: #e5e5e2; font-size: 0.8rem; }
.tabla { width: 100%; border-collapse: collapse; background: #fff; }
.tabla th, .tabla td { text-align: left; padding: 0.5rem 0.7rem; border-bottom: 1px solid #e5e5e2; font-size: 0.9rem; }
.mensaje { color: #a33; margin: 0.5rem 0; }
.dif { background: #fdf3d7; }
.grilla { display: grid; grid-template-columns: 1fr 1fr; gap: 1.2rem; }
@media (max-width: 720px) { .grilla { grid-template-columns: 1fr; } }
"""


def pagina(titulo: str, cuerpo: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(titulo)}</title><style>{ESTILO}</style></head>
<body><main>{cuerpo}</main></body></html>"""
    )


def pagina_error(titulo: str, mensaje: str) -> HTMLResponse:
    return pagina(
        titulo,
        f"""<h1>{html.escape(titulo)}</h1><div class="aviso">{html.escape(mensaje)}</div>
<p><a href="/">Inicio</a></p>""",
    )


async def ver_proceso(process_id: str) -> dict[str, Any] | None:
    """El snapshot ``ver_proceso`` de la ejecucion, o None si no existe (o el API no contesta)."""
    try:
        respuesta = await mistral_client().workflows.executions.query_workflow_execution_async(
            execution_id=process_id, name="ver_proceso"
        )
    except Exception:
        return None
    resultado = respuesta.result
    return resultado if isinstance(resultado, dict) else None


async def aplicar_update(process_id: str, name: str, entrada: dict[str, Any]) -> tuple[bool, str]:
    """Un update del workflow; (False, mensaje) cuando la ejecucion no existe o rechaza el cambio."""
    try:
        await mistral_client().workflows.executions.update_workflow_execution_async(
            execution_id=process_id, name=name, input=entrada
        )
    except Exception as error:
        return False, f"No se pudo aplicar el cambio ({error.__class__.__name__})."
    return True, ""


async def iniciar_proceso(process_id: str, inmueble: str, email_inquilino: str) -> tuple[bool, str]:
    """Lanza la ejecución del workflow con ese process_id como execution_id; False si el API rechaza."""
    entrada: dict[str, Any] = {"process_id": process_id, "inmueble": inmueble}
    if email_inquilino:
        entrada["email_inquilino"] = email_inquilino
    try:
        await mistral_client().workflows.execute_workflow_async(
            workflow_identifier=WORKFLOW_NAME,
            execution_id=process_id,
            input=entrada,
            deployment_name=env_workflows.deployment_name,
        )
    except Exception as error:
        return False, f"No se pudo iniciar el proceso: {error.__class__.__name__}."
    return True, ""


ESTADOS_ABERTOS = ("captura", "revision")


def campo_formulario(campo: str, valor: str) -> str:
    """Un campo del formulario como textarea, nombrado ``campos[<campo>]`` para el parseo del POST."""
    ruta = f' name="campos[{html.escape(campo)}]"'
    return (
        f'<div class="campo"><label>{html.escape(campo)}</label>'
        f'<textarea rows="2"{ruta}>{html.escape(valor or "")}</textarea></div>'
    )


def campo_lectura(campo: str, valor: str) -> str:
    return f'<div class="campo"><label>{html.escape(campo)}</label><div>{html.escape(valor or "—")}</div></div>'
