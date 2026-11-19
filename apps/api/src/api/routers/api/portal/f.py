"""El formulario del futuro inquilino: ``/api/f/{process_id}``.

El process_id es el token de acceso (uuid generado por gerencia); quien tenga el URL captura. El
autoguardado manda los campos sucios por ``guardar_borrador`` cada tanto, y el boton "Ya complete"
manda ``marcar_completo``: nada se envia formalmente, la revision la hace gerencia.
"""

import html
import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from api.routers.api.portal import _ayuda as ayuda
from worker.workflows.arrendamiento import CAMPOS_INQUILINO, GRUPOS_INQUILINO

router = APIRouter()

# Mientras gerencia revisa o el contrato se genera, el inquilino ve la pagina de espera; si gerencia
# le pide correcciones, el proceso vuelve a `captura` y el formulario se reabre con las notas.
MODOS_CAPTURA = ("captura", "iniciando", "cargando")


def _campos_de_formulario(valores: dict[str, Any]) -> str:
    pedazos = []
    for titulo, campos in GRUPOS_INQUILINO:
        filas = "".join(ayuda.campo_formulario(campo, valores.get(campo, "")) for campo in campos)
        pedazos.append(f'<div class="grupo"><h2>{html.escape(titulo)}</h2>{filas}</div>')
    return "".join(pedazos)



@router.get("/{process_id}", include_in_schema=False)
async def formulario(process_id: str) -> Response:
    proceso = await ayuda.ver_proceso(process_id)
    if proceso is None:
        return ayuda.pagina_error(
            "Formulario no disponible",
            "Este proceso no existe o ya no esta activo. Notifica a gerencia para recibir un URL nuevo.",
        )
    estado = str(proceso.get("estado", ""))
    if estado == "entregado":
        return ayuda.pagina(
            "Contrato en marcha",
            '<h1>Tu informacion ya fue procesada</h1><div class="ok">Gerencia gener\u00f3 el contrato. '
            "Si algo faltara, te contactar\u00e1n por correo.</div>",
        )
    if estado not in MODOS_CAPTURA:
        if estado == "pausado":
            mensaje = (
                "El proceso est\u00e1 pausado en gerencia. Escr\u00edbeles para retomarlo."
            )
        else:
            mensaje = (
                "Gerencia est\u00e1 comparando tus datos con la documentaci\u00f3n. "
                "Te escribir\u00e1n por correo si falta algo."
            )
        return ayuda.pagina(
            "En revisión",
            f"<h1>Gracias, tu captura est\u00e1 en revisi\u00f3n</h1>"
            f"<div class='ok'>{html.escape(mensaje)}</div>",
        )

    valores = proceso.get("formulario") or {}
    notas = str(proceso.get("notas") or "")
    aviso = (
        f'<div class="aviso"><strong>Pendiente que gerencia pidi\u00f3 corregir:</strong><br>{html.escape(notas)}</div>'
        if notas
        else ""
    )
    cuerpo = f"""
<h1>Datos para tu contrato de arrendamiento</h1>
<p>Inmueble: <strong>{html.escape(str(valores.get("Inmueble en arrendamiento") or proceso.get("inmueble") or ""))}</strong></p>
{aviso}
<div class="aviso">Guardado automático activo: puedes cerrar y regresar otra vez. Cuando ya
completaste todo lo que puedes, pulsa <strong>Ya complete</strong> al final; no se env\u00eda nada de una vez,
solamente avisas que tu parte est\u00e1 lista para la revisi\u00f3n.</div>
<form id="datos">{_campos_de_formulario(valores)}
<p><button type="button" id="completar" class="primario">Ya complete</button>
<button type="button" id="guardar" class="secundario">Guardar ahora</button>
<span id="mensaje" class="mensaje"></span></p>
</form>
<script>
(() => {{
  const sucios = new Set();
  const mensaje = document.getElementById("mensaje");
  const textoEstado = (texto) => {{ mensaje.textContent = texto; setTimeout(() => mensaje.textContent = "", 4000); }};
  document.querySelectorAll("#datos [name]").forEach((elemento) => {{
    const campo = elemento.name.slice(7, -1);
    elemento.addEventListener("input", () => sucios.add(campo));
  }});
  const enviar = async () => {{
    if (sucios.size === 0) return true;
    const campos = {{}};
    for (const campo of sucios) {{
      const elemento = document.querySelector(`#datos [name="campos[${{campo}}]"]`);
      if (elemento) campos[campo] = elemento.value;
    }}
    const ok = await guardar(campos);
    if (ok) sucios.clear();
    return ok;
  }};
  document.getElementById("guardar").addEventListener("click", async () => {{
    textoEstado((await enviar()) ? "Guardado." : "No se pudo guardar, intenta de nuevo.");
  }});
  setInterval(() => enviar().then((ok) => ok && textoEstado("Autoguardado.")), 15000);
  document.getElementById("completar").addEventListener("click", async () => {{
    if (!(await enviar())) {{
      textoEstado("No se pudieron guardar los cambios, intenta de nuevo.");
      return;
    }}
    const campoEmail = document.querySelector('#datos [name="campos[Email arrendatario]"]');
    const respuesta = await fetch(window.location.pathname + "/complete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: campoEmail ? campoEmail.value : "" }),
    });
    if (respuesta.ok) {{
      document.getElementById("datos").innerHTML =
        '<div class="ok"><h2>Gracias</h2>Gerencia ya qued\u00f3 notificada y revisar\u00e1 tu informaci\u00f3n contra tu documentaci\u00f3n. Te escribir\u00e1n si falta algo.</div>';
    }} else {{
      textoEstado("No se pudo marcar como completo, intenta de nuevo.");
    }}
  }});
}})();
</script>
"""
    return ayuda.pagina("Formulario de arrendamiento", cuerpo)


@router.post("/{process_id}", include_in_schema=False)
async def autoguardado(process_id: str, request: Request) -> JSONResponse:
    try:
        cuerpo = json.loads((await request.body()) or b"{}")
    except json.JSONDecodeError:
        return JSONResponse({"ok": False}, status_code=400)
    campos = cuerpo.get("campos") if isinstance(cuerpo, dict) else None
    if not isinstance(campos, dict):
        return JSONResponse({"ok": False}, status_code=400)
    solo_inquilino = {campo: str(valor) for campo, valor in campos.items() if campo in CAMPOS_INQUILINO}
    if not solo_inquilino:
        return JSONResponse({"ok": True})
    logrado, error = await ayuda.aplicar_update(
        process_id, "guardar_borrador", {"data": solo_inquilino}
    )
    return JSONResponse({"ok": logrado}, status_code=200 if logrado else 409)


@router.post("/{process_id}/complete", include_in_schema=False)
async def marcar_completo(process_id: str, request: Request) -> JSONResponse:
    try:
        cuerpo = json.loads((await request.body()) or b"{}")
    except json.JSONDecodeError:
        cuerpo = {}
    email = str(cuerpo.get("email") or "") if isinstance(cuerpo, dict) else ""
    logrado, _ = await ayuda.aplicar_update(
        process_id,
        "marcar_completo",
        {"email_inquilino": email} if email else {},
    )
    return JSONResponse({"ok": logrado}, status_code=200 if logrado else 409)
