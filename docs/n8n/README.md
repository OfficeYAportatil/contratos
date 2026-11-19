# Exportacion a n8n

`contratos-arrendamiento.json` es un equivalentede importación directa en n8n (Workflows →
Import from file). Mantiene el mismo orden del flujo Mistral y usa tus IDs reales:

- Spreadsheet `1jda5Z9-Uu07KXj-ZUwiARUV5xmXGnbt0D8E57EJVzLI` (Presets / DatosRenda)
- Plantillas Habitacional `1bN7cFZg…` / Comercial `1CIi0Nse…`
- Carpeta de contratos `187lKT8g582TR20MLvGtG0jodpIcBg3kp`
- Destino de gerencia `dynamisdenarii@gmail.com`

## Que hay que ajustar despues de importar

1. **Credenciales**: conecta tus cuentas de Google Sheets / Gmail / Google Drive en cada nodo.
2. **Nodo "LLM extrae campos"**: es un placeholder (Code node); cambialo por un nodo
   OpenAI/Mistral/Gemini que pida el JSON `[{"campo","valor","evidencia"}]` contra el catalogo
   de campos de `apps/worker/src/worker/workflows/arrendamiento_utilidades.py`.
3. **Nodo "Llenar {{tokens}}"**: n8n no trae manipulacion de docx incluida; la via natural es
   el nodo Google Docs ("create document from template") o un Code node con libreria de zip
   (requires addons). El Code node incluido solo documenta el contrato de datos.
4. **Espera durable**: n8n sustituye el `wait_condition` del workflow Mistral por webhooks
   externos ("contratos/aprobado") o polling; el autoguardado multi-sesion del formulario es
   parte del portal Mistral (`/api/f/{process_id}`), no de este JSON.

Es un puerto aproximado para evaluar n8n como alternativa: la implementacion completa
(campos catalogados, informe con evidencia, ciclos de correccion, PDF) vive en
`apps/worker/src/worker/workflows/`.
