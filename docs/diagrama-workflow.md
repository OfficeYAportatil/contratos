# Diagrama del flujo de contratos de arrendamiento

```mermaid
flowchart TD
    GS["Google Sheets<br/>BaseDatosContratos<br/>(Presets • DatosRenda • PLANTILLAS)"]

    subgraph INICIO["1. Gerencia da de alta el proceso"]
        PANEL["Panel gerencia<br/>/api/gerencia"]
        NUEVO["Nuevo proceso<br/>(inmueble + email inquilino)"]
    end

    subgraph CAPTURA["2. Captura del inquilino"]
        URL["URL del formulario<br/>/api/f/{process_id}"]
        FORM["Formulario con autoguardado<br/>(cada 15 s, multi-sesión)"]
        YA["Botón 'Ya complete'<br/>(sin envío formal)"]
    end

    subgraph REVISION["3. Revisión de gerencia"]
        DOC["Recopilar correos del inquilino<br/>(Gmail: cuerpos + adjuntos)"]
        LLM["Extraer campos con LLM<br/>(valor + evidencia)"]
        INF["Informe de comparación<br/>capturado ↔ documentos"]
        MAILG["Correo a gerencia<br/>dynamisdenarii@gmail.com"]
        REV["Gerencia corrige y decide<br/>aprobado si / aprobado no"]
    end

    subgraph GENERACION["4. Generación del contrato"]
        PLANT["Elegir plantilla<br/>Habitacional / Comercial"]
        DOCX["Descargar plantilla docx<br/>y llenar {{tokens}}"]
        DRIVE["Guardar contratos en Drive<br/>carpeta de contratos"]
        PDF["Exportar PDF"]
        SEND["Correo a gerencia<br/>con PDF + enlace"]
    end

    CORRECCION["Correo al inquilino con notas<br/>y formulario reabierto (máx. 3 ciclos)"]
    PAUSA["Proceso pausado"]

    GS --> NUEVO
    PANEL --> NUEVO --> URL --> FORM --> YA
    YA --> DOC --> LLM --> INF --> MAILG --> REV
    REV -- "aprobado" --> PLANT --> DOCX --> DRIVE --> PDF --> SEND
    REV -- "con correcciones" --> CORRECCION --> FORM
    CORRECCION -- "3 ciclos agotados" --> PAUSA
```

## Campos calculados

Se calculan en `calcula_derivados` (`apps/worker/src/worker/workflows/arrendamiento_utilidades.py`)
a partir de los datos capturados y de `DatosRenda`, justo antes de llenar la plantilla:

| Campo calculado | Fórmula / fuente |
| --- | --- |
| `Texto renta mensual bruta` | `Renta mensual bruta` → número a letras en pesos con centavos `XX/100 M.N.` |
| `Valor del periodo` | `Renta mensual bruta × 12` |
| `Total con dcto pp` | `Renta mensual bruta − Descuento PP` (solo si hay renta) |
| `Texto total con dcto pp` | el total anterior a letras |
| `Importe DEG` / `Texto DEG` | depósito en garantía → formato y letras |
| `Fecha Firma` | `Fecha Firma` (varios formatos) → `15 de marzo de 2025` |
| `Día de pago mensual` / `Texto día de pago mensual` | día de `Fecha Firma` → `1` / `UNO` en letras |
| `Año firma texto` | año de `Fecha Firma` → `DOS MIL VEINTICINCO` |

La mezcla final es `valores = arrendador (DatosRenda) + formulario del inquilino + derivados`;
los `{{tokens}}` de la plantilla sin valor se reportan en el informe como **pendientes**.
Para agregar un campo calculado nuevo basta añadirlo en `calcula_derivados` con su prueba.
