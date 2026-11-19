"""Las piezas puras del flujo de contratos: parseo de la hoja, numeros a letras y llenado docx."""

import base64
import io
import zipfile

from worker.workflows import arrendamiento_utilidades as util
from worker.workflows.arrendamiento import elegir_plantilla, texto_de_resultado
from worker.workflows.arrendamiento_utilidades import informe_revision


class TestNumerosALetras:
    def test_cifras_basicas(self) -> None:
        assert util.numero_a_letras(0) == "cero"
        assert util.numero_a_letras(1) == "un"
        assert util.numero_a_letras(11) == "once"
        assert util.numero_a_letras(21) == "veintiun"
        assert util.numero_a_letras(30) == "treinta"
        assert util.numero_a_letras(46) == "cuarenta y seis"
        assert util.numero_a_letras(100) == "cien"
        assert util.numero_a_letras(101) == "ciento un"
        assert util.numero_a_letras(1000) == "mil"
        assert util.numero_a_letras(1100) == "mil cien"
        assert util.numero_a_letras(152345) == "ciento cincuenta y dos mil trescientos cuarenta y cinco"

    def test_millones(self) -> None:
        assert util.numero_a_letras(1_000_000) == "un millon"
        assert util.numero_a_letras(2_500_000) == "dos millones quinientos mil"
        assert util.numero_a_letras(1_860_000) == "un millon ochocientos sesenta mil"

    def test_pesos(self) -> None:
        texto = util.dinero_a_letras("152345.6")
        assert texto == "CIENTO CINCUENTA Y DOS MIL TRESCIENTOS CUARENTA Y CINCO PESOS 60/100 M.N."
        assert util.dinero_a_letras("155,000.00") == "CIENTO CINCUENTA Y CINCO MIL PESOS 00/100 M.N."
        assert util.dinero_a_letras("") == ""
        assert util.dinero_a_letras("abc") == ""


class TestHojas:
    def test_separa_hojas_con_comillas(self) -> None:
        texto = (
            "Sheet: Presets\nTipo de inmueble,Direcci\u00f3n\nCasa,\"Av. Siempre Viva 742, Springfield\"\n"
            "Sheet: DatosRenda\nInmueble en arrendamiento,Arrendador,Importe DEG\n"
            "\"Av. Siempre Viva 742, Springfield\",Homero,155000\nnan,NaN,\n"
        )
        hojas = util.separa_hojas(texto)
        assert set(hojas) == {"Presets", "DatosRenda"}
        inmuebles = util.hoja_a_registros(hojas, "Presets")
        assert inmuebles == [{"Tipo de inmueble": "Casa", "Direcci\u00f3n": "Av. Siempre Viva 742, Springfield"}]
        contratos = util.hoja_a_registros(hojas, "DatosRenda")
        assert contratos[0]["Importe DEG"] == "155000"
        # El renglon todo-nan de la hoja se descarta: es un renglon vacio.
        assert len(contratos) == 1

    def test_busca_inmueble_tolerante(self) -> None:
        registros = [{"Tipo de inmueble": "Local", "Direcci\u00f3n": "Av. Ju\u00e1rez 100, Le\u00f3n"}]
        encontrada = util.busca_inmueble(registros, "ju\u00e1REZ 100")
        assert encontrada["Direcci\u00f3n"] == "Av. Ju\u00e1rez 100, Le\u00f3n"
        assert util.busca_inmueble(registros, "x") == {}
        assert util.busca_inmueble(registros, "") == {}

    def test_busca_arrendador_ultima_fila(self) -> None:
        contratos = [
            {"Inmueble en arrendamiento": "Av. Ju\u00e1rez 100", "Arrendador": "Viejo", "Importe DEG": "1"},
            {"Inmueble en arrendamiento": "Av. Ju\u00e1rez 100", "Arrendador": "Nuevo", "Importe DEG": "2"},
        ]
        fila = util.busca_arrendador(contratos, "Av. Ju\u00e1rez 100")
        assert fila["Arrendador"] == "Nuevo"
        assert util.busca_arrendador(contratos, "Otro") == {}


class TestDerivados:
    def test_calcula_todos(self) -> None:
        derivative = util.calcula_derivados(
            {"Renta mensual bruta": "155,000.00", "Importe DEG": "155000", "Fecha Firma": "2025-03-15", "Descuento PP": ""}
        )
        assert derivative["Texto renta mensual bruta"].endswith("PESOS 00/100 M.N.")
        assert derivative["Valor del periodo"] == "1,860,000.00"
        assert derivative["Descuento PP"] == "0.00"
        assert derivative["Total con dcto pp"] == "155,000.00"
        assert derivative["D\u00eda de pago mensual"] == "15"
        assert derivative["Texto d\u00eda de pago mensual"] == "QUINCE"
        assert derivative["A\u00f1o firma texto"] == "DOS MIL VEINTICINCO"
        assert derivative["Fecha Firma"] == "15 de marzo de 2025"

    def test_resta_descuento(self) -> None:
        derivative = util.calcula_derivados({"Renta mensual bruta": "20000", "Descuento PP": "500"})
        assert derivative["Total con dcto pp"] == "19,500.00"
        assert derivative["Texto total con dcto pp"].startswith("DIECINUEVE MIL QUINIENTOS")

    def test_sin_datos_no_explota(self) -> None:
        assert util.calcula_derivados({}) == {}


def _docx_con(xml: str) -> str:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archivo:
        archivo.writestr("word/document.xml", xml)
    return base64.b64encode(buffer.getvalue()).decode()


def _xml_de(docx: str) -> str:
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(docx))) as archivo:
        return archivo.read("word/document.xml").decode()


class TestPlantilla:
    def test_llena_token_partido_entre_runs(self) -> None:
        plantilla = _docx_con(
            "<w:document><w:p><w:r><w:t>Arrendatario {{Nombre</w:t></w:r>"
            '<w:r><w:t xml:space="preserve"> arrendatario}} y m\u00e1s</w:t></w:r></w:p></w:document>'
        )
        lleno, pendientes = util.llena_plantilla_docx(plantilla, {"Nombre arrendatario": "Ana L\u00f3pez y C\u00eda."})
        xml = _xml_de(lleno)
        assert "Ana L\u00f3pez y C\u00eda." in xml
        assert "{{" not in xml and "}}" not in xml
        assert pendientes == []

    def test_token_sin_valor_queda_y_se_reporta(self) -> None:
        plantilla = _docx_con("<w:p><w:r><w:t>{{Nombre arrendatario}} / {{RFC arrendatario}}</w:t></w:r></w:p>")
        lleno, pendientes = util.llena_plantilla_docx(plantilla, {"Nombre arrendatario": "Ana"})
        xml = _xml_de(lleno)
        assert "Ana" in xml and "{{RFC arrendatario}}" in xml
        assert pendientes == ["RFC arrendatario"]

    def test_tokens_de_plantilla_lee_partidos(self) -> None:
        plantilla = _docx_con(
            "<w:p><w:r><w:t>{{Conyuge</w:t></w:r><w:r><w:t> Dummy}}</w:t></w:r></w:p>"
        )
        assert util.tokens_de_plantilla(plantilla) == ["Conyuge Dummy"]


def test_texto_de_resultado_lee_bloques() -> None:
    resultado = {"content": [{"type": "text", "text": "Sheet: A\nx\n"}]}
    assert texto_de_resultado(resultado) == "Sheet: A\nx\n"
    assert texto_de_resultado(None) == ""
    assert texto_de_resultado({"content": []}) == ""


class TestInforme:
    def test_informe_marca_faltantes_diferencias_y_extra(self) -> None:
        formulario = {"Nombre arrendatario": "Ana", "RFC arrendatario": "XAXX010101000", "CURP arrendatario": ""}
        extraido = [
            {"campo": "RFC arrendatario", "valor": "AABC840101H45", "evidencia": "correo 1"},
            {"campo": "Teléfono arrendatario", "valor": "477 123 4567", "evidencia": "correo 2"},
        ]
        informe = informe_revision(formulario, extraido, [{"de": "a@x.mx", "asunto": "docs", "fecha": "hoy", "adjuntos": ["INE.pdf"]}])
        assert "CURP arrendatario" in informe["faltantes"]
        assert "Nombre arrendatario" not in informe["faltantes"]
        assert len(informe["faltantes"]) == 24
        assert informe["diferencias"]["RFC arrendatario"]["documento"] == "AABC840101H45"
        assert informe["extra_del_documento"]["Teléfono arrendatario"]["valor"] == "477 123 4567"
        assert informe["documentos_recibidos"][0]["adjuntos"] == ["INE.pdf"]


class TestPlantillas:
    def test_eleccion_por_tipo(self) -> None:
        plantillas = [
            {"Plantilla": "ArrendamientoComercial", "Documento ID": "1a" * 15},
            {"Plantilla": "ArrendamientoHabitacional", "Documento ID": "1b" * 15},
        ]
        elegida = elegir_plantilla(plantillas, "Departamento")
        assert "habitacional" in elegida["nombre"].lower()
        elegida = elegir_plantilla(plantillas, "Local Comercial")
        assert "comercial" in elegida["nombre"].lower()

    def test_sin_coincidencia_toma_primera(self) -> None:
        plantillas = [{"Plantilla": "ArrendamientoComercial", "Documento ID": "1a" * 15}]
        elegida = elegir_plantilla(plantillas, "Nave")
        assert elegida is not None
        assert elegir_plantilla([], "Casa") is None
