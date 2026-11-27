"""Pruebas de extremo a extremo de la API con un LLM y un almacén vectorial falsos (sin red)."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nexora.config import Settings
from nexora.core_ia.llm.base import AdaptadorLLM
from nexora.core_ia.vector_store import Vecino
from nexora.main import create_app
from nexora.servicios import construir_servicios


class LLMFalso(AdaptadorLLM):
    proveedor = "falso"
    modelo = "prueba"

    def __init__(self) -> None:
        self.respuestas: list[dict[str, Any]] = []
        self.ultimo_prompt = ""

    async def generar_json(self, sistema: str, prompt: str, esquema: dict[str, Any]) -> dict[str, Any]:
        self.ultimo_prompt = prompt
        return self.respuestas.pop(0)

    async def generar_texto(self, sistema: str, prompt: str, *, max_tokens: int = 4000, esfuerzo: str = "medio") -> str:
        self.ultimo_prompt = prompt
        return "Es una factura de luz de noviembre por 50 euros."


def _palabras(texto: str) -> set[str]:
    return set(re.findall(r"\w+", texto.lower()))


class VectoresFalsos:
    """Similitud = coeficiente de Jaccard entre palabras; suficiente para probar el flujo."""

    def __init__(self) -> None:
        self.archivos: dict[str, tuple[str, dict]] = {}
        self.ejemplos: dict[str, tuple[str, dict]] = {}

    def indexar_archivo(self, archivo_id: str, documento: str, metadatos: dict) -> None:
        self.archivos[archivo_id] = (documento, metadatos)

    def _similitud(self, a: str, b: str) -> float:
        pa, pb = _palabras(a), _palabras(b)
        return len(pa & pb) / len(pa | pb) if pa | pb else 0.0

    def buscar_archivos(self, consulta: str, n: int) -> list[tuple[str, float]]:
        res = [(i, self._similitud(consulta, doc)) for i, (doc, _) in self.archivos.items()]
        return sorted(res, key=lambda r: r[1], reverse=True)[:n]

    def vecinos_aprendizaje(self, consulta: str, n: int) -> list[Vecino]:
        res = [
            Vecino(i, meta["categoria"], self._similitud(consulta, doc), meta["nombre"], meta["fuente"], meta["motivo"])
            for i, (doc, meta) in self.ejemplos.items()
        ]
        return sorted(res, key=lambda v: v.similitud, reverse=True)[:n]

    def olvidar_ejemplos(self, archivo_id):
        self.ejemplos = {k: v for k, v in self.ejemplos.items() if v[1].get("archivo_id") != archivo_id}

    def registrar_ejemplo(self, ejemplo_id, archivo_id, categoria, fue_correccion, fuente="usuario", motivo="") -> bool:
        if archivo_id not in self.archivos:
            return False
        doc, meta = self.archivos[archivo_id]
        self.olvidar_ejemplos(archivo_id)
        self.ejemplos[ejemplo_id] = (doc, {**meta, "categoria": categoria, "fuente": fuente, "motivo": motivo, "archivo_id": archivo_id})
        return True


def _respuesta_llm(carpeta: str, confianza: float) -> dict[str, Any]:
    return {
        "carpeta_destino": carpeta,
        "confianza": confianza,
        "resumen_ejecutivo": "Factura del servicio de electricidad del mes de noviembre.",
        "palabras_clave": ["factura", "electricidad", "noviembre"],
        "alternativas": ["Finanzas/Hogar"],
        "razonamiento": "Es una factura doméstica.",
    }


@pytest.fixture
def entorno(tmp_path: Path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    settings = Settings(
        dir_datos=tmp_path / "datos",
        carpetas_vigiladas=[entrada],
        raiz_organizada=tmp_path / "organizado",
        zonas_busqueda=[tmp_path / "Documentos"],
        habilitar_watcher=False,
        _env_file=None,
    )
    llm, vectores = LLMFalso(), VectoresFalsos()
    servicios_creados = {}

    def fabrica(s: Settings):
        servicios_creados["s"] = construir_servicios(s, vectores=vectores, llm=llm)
        return servicios_creados["s"]

    with TestClient(create_app(settings, fabrica)) as cliente:
        token = settings.ruta_token_escritorio.read_text(encoding="utf-8")
        cliente.headers["Authorization"] = f"Bearer {token}"
        yield cliente, servicios_creados["s"], llm, entrada, tmp_path


def _indexar(servicios, ruta: Path) -> str:
    return asyncio.run(servicios.indexador.procesar(ruta))


def test_rutas_protegidas_exigen_token(entorno):
    cliente, *_ = entorno
    assert cliente.get("/api/health").status_code == 200
    respuesta = cliente.get("/api/files/search", params={"q": "x"}, headers={"Authorization": ""})
    assert respuesta.status_code == 401
    respuesta = cliente.get("/api/files/search", params={"q": "x"}, headers={"Authorization": "Bearer nx_falso"})
    assert respuesta.status_code == 401


def test_flujo_completo_busqueda_explicacion_y_feedback(entorno):
    cliente, servicios, llm, entrada, tmp_path = entorno
    archivo = entrada / "Factura_Noviembre.txt"
    archivo.write_text("Factura de luz de noviembre. Importe total 50 euros.", encoding="utf-8")

    # Confianza baja: va a la bandeja de dudas y no se mueve.
    llm.respuestas.append(_respuesta_llm("Finanzas/Hogar", 0.62))
    archivo_id = _indexar(servicios, archivo)
    assert archivo.exists()
    pendientes = cliente.get("/api/pending").json()
    assert [p["archivo"]["id"] for p in pendientes] == [archivo_id]
    assert pendientes[0]["carpeta_sugerida"] == "Finanzas/Hogar"

    # Búsqueda por nombre (FTS sin tildes ni mayúsculas) y por contenido.
    resultado = cliente.get("/api/files/search", params={"q": "factura noviembre"}).json()
    assert resultado["resultados"][0]["archivo"]["id"] == archivo_id
    assert resultado["resultados"][0]["coincidencia"] in {"nombre", "ambas"}
    assert resultado["resultados"][0]["archivo"]["ruta_actual"] == str(archivo)
    assert cliente.get("/api/files/search", params={"q": "electricidad"}).json()["total"] == 1

    # Explicación: lee el archivo del disco y lo pasa al LLM.
    explicacion = cliente.post(f"/api/files/{archivo_id}/explain", json={"pregunta": "¿Cuánto hay que pagar?"}).json()
    assert explicacion["respuesta"].startswith("Es una factura")
    assert "Importe total 50 euros" in llm.ultimo_prompt
    assert explicacion["texto_truncado"] is False

    # Feedback: el usuario elige otra carpeta.
    respuesta = cliente.post("/api/feedback", json={"archivo_id": archivo_id, "carpeta_elegida": "Finanzas\\Servicios"})
    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()
    nueva_ruta = tmp_path / "organizado" / "Finanzas" / "Servicios" / "Factura_Noviembre.txt"
    assert datos["fue_correccion"] is True
    assert datos["carpeta_elegida"] == "Finanzas/Servicios"
    assert datos["aprendizaje_registrado"] is True
    assert datos["archivo"]["ruta_actual"] == str(nueva_ruta)
    assert nueva_ruta.exists() and not archivo.exists()
    assert cliente.get("/api/pending").json() == []

    # Aprendizaje: un archivo casi idéntico que el LLM manda a otra carpeta pasa a revisión humana,
    # y el prompt incluye la decisión previa del usuario.
    similar = entrada / "Factura_Diciembre.txt"
    similar.write_text("Factura de luz de diciembre. Importe total 50 euros.", encoding="utf-8")
    llm.respuestas.append(_respuesta_llm("Finanzas/Hogar", 0.95))
    servicios.vectores.vecinos_aprendizaje = lambda consulta, n: [Vecino("x", "Finanzas/Servicios", 0.97, "Factura_Noviembre.txt")]
    similar_id = _indexar(servicios, similar)
    assert "Finanzas/Servicios" in llm.ultimo_prompt
    pendiente = cliente.get("/api/pending").json()[0]
    assert pendiente["archivo"]["id"] == similar_id
    assert pendiente["confianza"] <= 0.6
    assert pendiente["alternativas"][0] == "Finanzas/Servicios"


def test_confianza_alta_mueve_automaticamente(entorno):
    cliente, servicios, llm, entrada, tmp_path = entorno
    archivo = entrada / "nomina.txt"
    archivo.write_text("Nómina de octubre", encoding="utf-8")
    (tmp_path / "organizado" / "Finanzas" / "Trabajo").mkdir(parents=True)
    llm.respuestas.append(_respuesta_llm("Finanzas/Trabajo", 0.93))
    archivo_id = _indexar(servicios, archivo)
    destino = tmp_path / "organizado" / "Finanzas" / "Trabajo" / "nomina.txt"
    assert destino.exists()
    assert cliente.get("/api/pending").json() == []
    resultado = cliente.get("/api/files/search", params={"q": "nomina"}).json()
    assert resultado["resultados"][0]["archivo"]["id"] == archivo_id
    assert resultado["resultados"][0]["archivo"]["ruta_actual"] == str(destino)


def test_feedback_rechaza_rutas_peligrosas(entorno):
    cliente, servicios, llm, entrada, _ = entorno
    archivo = entrada / "a.txt"
    archivo.write_text("hola", encoding="utf-8")
    llm.respuestas.append(_respuesta_llm("Varios", 0.1))
    archivo_id = _indexar(servicios, archivo)
    for carpeta in ["../fuera", "C:/Windows", "C:/Windows/Temp", "C:/", "C:/no/existe", "CON", "a/./b"]:
        respuesta = cliente.post("/api/feedback", json={"archivo_id": archivo_id, "carpeta_elegida": carpeta})
        assert respuesta.status_code == 422, carpeta
    assert archivo.exists()


def test_explicar_archivo_inexistente(entorno):
    cliente, servicios, llm, entrada, _ = entorno
    assert cliente.post("/api/files/no-existe/explain", json={}).status_code == 404
    archivo = entrada / "borrado.txt"
    archivo.write_text("contenido", encoding="utf-8")
    llm.respuestas.append(_respuesta_llm("Varios", 0.1))
    archivo_id = _indexar(servicios, archivo)
    archivo.unlink()
    assert cliente.post(f"/api/files/{archivo_id}/explain", json={}).status_code == 410


def test_emparejamiento_movil_con_permisos_limitados(entorno):
    cliente, *_ = entorno
    codigo = cliente.post("/api/devices/pairing-code").json()
    import json

    contenido = json.loads(codigo["contenido_qr"])
    emparejado = cliente.post(
        "/api/devices/pair", json={"codigo": contenido["codigo"], "nombre": "Mi teléfono", "plataforma": "android"},
        headers={"Authorization": ""},
    )
    assert emparejado.status_code == 201
    token_movil = emparejado.json()["token"]
    # El código es de un solo uso.
    repetido = cliente.post("/api/devices/pair", json={"codigo": contenido["codigo"], "nombre": "Otro"})
    assert repetido.status_code == 400

    cabecera = {"Authorization": f"Bearer {token_movil}"}
    assert cliente.get("/api/files/search", params={"q": "x"}, headers=cabecera).status_code == 200
    assert cliente.get("/api/pending", headers=cabecera).status_code == 403
    assert cliente.post("/api/devices/pairing-code", headers=cabecera).status_code == 403

    movil_id = emparejado.json()["dispositivo_id"]
    assert cliente.delete(f"/api/devices/{movil_id}").status_code == 204
    assert cliente.get("/api/files/search", params={"q": "x"}, headers=cabecera).status_code == 401


def test_tema(entorno):
    cliente, *_ = entorno
    tema = cliente.get("/api/settings/theme").json()
    assert tema["modo"] == "auto" and tema["detectado_del_so"] is True
    oscuro = cliente.put("/api/settings/theme", json={"modo": "oscuro"}).json()
    assert oscuro["modo_efectivo"] == "oscuro"
    assert oscuro["tokens"] == {
        "fondo": "#120324", "superficie": "#1C0B35", "acento_primario": "#00F0FF", "acento_secundario": "#FF007F",
    }


def test_reintenta_archivos_interrumpidos_o_fallidos(entorno):
    cliente, servicios, llm, entrada, tmp_path = entorno
    archivo = entrada / "factura.txt"
    archivo.write_text("Factura", encoding="utf-8")
    archivo_id = _indexar(servicios, archivo)  # el LLM falso no tiene respuestas: falla

    async def reanudar():
        await servicios.vigilante.iniciar()
        await servicios.vigilante._cola.join()
        await servicios.vigilante.detener()

    (tmp_path / "organizado" / "Finanzas").mkdir(parents=True)
    llm.respuestas.append(_respuesta_llm("Finanzas", 0.95))
    asyncio.run(reanudar())
    assert (tmp_path / "organizado" / "Finanzas" / "factura.txt").exists()
    assert cliente.get("/api/files/search", params={"q": "factura"}).json()["resultados"][0]["archivo"]["id"] == archivo_id


def test_panel_subida_resumen_ajustes_y_carpetas(entorno):
    cliente, servicios, llm, entrada, tmp_path = entorno
    (tmp_path / "organizado" / "Finanzas").mkdir(parents=True)
    original = tmp_path / "suelto" / "recibo.txt"
    original.parent.mkdir()
    original.write_text("Recibo de agua", encoding="utf-8")
    r = cliente.post("/api/files/import", json={"rutas": [str(original), str(tmp_path), "relativo.txt"]}).json()
    assert r["aceptados"] == ["recibo.txt"] and len(r["omitidos"]) == 2
    llm.respuestas.append(_respuesta_llm("Finanzas", 0.95))
    _indexar(servicios, servicios.vigilante._cola.get_nowait())
    resumen = cliente.get("/api/stats").json()
    assert resumen["clasificados_hoy"] == 1 and resumen["precision"] == 100
    assert cliente.get("/api/activity").json()[0]["nombre"] == "recibo.txt"
    assert not original.exists()  # se movió, no se copió
    movido = tmp_path / "organizado" / "Finanzas" / "recibo.txt"
    assert "ya está organizado" in cliente.post("/api/files/import", json={"rutas": [str(movido)]}).json()["omitidos"][0]
    assert {"valor": "Finanzas", "etiqueta": "Finanzas"} in cliente.get("/api/destinations").json()

    # Un archivo borrado a mano deja libre su ruta para otro con el mismo nombre.
    (tmp_path / "organizado" / "Finanzas" / "recibo.txt").unlink()
    otro = entrada / "recibo.txt"
    otro.write_text("Otro recibo", encoding="utf-8")
    llm.respuestas.append(_respuesta_llm("Finanzas", 0.95))
    _indexar(servicios, otro)
    assert cliente.get("/api/stats").json()["total"] == 1

    assert cliente.put("/api/settings", json={"umbral_confianza": 0.7}).json()["umbral_confianza"] == 0.7
    assert servicios.indexador.umbral == 0.7
    nueva = tmp_path / "nueva"
    nueva.mkdir()
    carpeta = cliente.post("/api/folders", json={"ruta": str(nueva)})
    assert carpeta.status_code == 201
    assert cliente.post("/api/folders", json={"ruta": "C:/"}).status_code == 422
    assert cliente.delete(f"/api/folders/{carpeta.json()['id']}").status_code == 204
    assert len(cliente.get("/api/folders").json()) == 1

    llm.respuestas.append(_respuesta_llm("Finanzas", 0.95))
    assert cliente.post("/api/ai/test").json()["carpeta"] == "Finanzas"


def test_usa_carpetas_que_el_usuario_ya_tiene(entorno):
    cliente, servicios, llm, entrada, tmp_path = entorno
    calculo = tmp_path / "Documentos" / "Universidad" / "Cálculo"
    calculo.mkdir(parents=True)
    (tmp_path / "Documentos" / "proyecto" / ".git").mkdir(parents=True)
    archivo = entrada / "parcial.txt"
    archivo.write_text("Parcial de integrales", encoding="utf-8")
    llm.respuestas.append(_respuesta_llm("documentos/universidad/cálculo", 0.95))
    _indexar(servicios, archivo)
    assert (calculo / "parcial.txt").exists() and not archivo.exists()
    assert "Documentos/Universidad/Cálculo" in llm.ultimo_prompt
    assert str(tmp_path) not in llm.ultimo_prompt  # no se envían rutas absolutas al proveedor
    assert "proyecto" not in [d["etiqueta"].split("/")[-1] for d in cliente.get("/api/destinations").json()]


def test_pregunta_si_no_hay_carpeta_y_acepta_una_externa(entorno):
    cliente, servicios, llm, entrada, tmp_path = entorno
    archivo = entrada / "apuntes.txt"
    archivo.write_text("Apuntes de historia", encoding="utf-8")
    llm.respuestas.append(_respuesta_llm("Estudio/Historia", 0.99))
    archivo_id = _indexar(servicios, archivo)
    assert archivo.exists() and not (tmp_path / "organizado" / "Estudio").exists()
    duda = cliente.get("/api/pending").json()[0]
    assert duda["carpeta_existe"] is False and duda["carpeta_sugerida"] == "Estudio/Historia"

    elegida = tmp_path / "Mis cosas" / "Historia"
    elegida.mkdir(parents=True)
    r = cliente.post("/api/feedback", json={"archivo_id": archivo_id, "carpeta_elegida": str(elegida)})
    assert r.status_code == 200, r.text
    assert (elegida / "apuntes.txt").exists() and not archivo.exists()
    assert r.json()["aprendizaje_registrado"] is True

    # Con el siguiente archivo parecido, Nexora ofrece primero la carpeta que eligió el usuario.
    otro = entrada / "apuntes2.txt"
    otro.write_text("Apuntes de historia, segunda parte", encoding="utf-8")
    llm.respuestas.append(_respuesta_llm("Historia", 0.99))
    servicios.vectores.vecinos_aprendizaje = lambda c, n: [Vecino("x", elegida.as_posix(), 0.97, "apuntes.txt")]
    _indexar(servicios, otro)
    assert cliente.get("/api/pending").json()[0]["alternativas"][0] == elegida.as_posix()


def test_carpeta_nueva_dentro_de_una_del_usuario(entorno):
    cliente, servicios, llm, entrada, tmp_path = entorno
    documentos = tmp_path / "Documentos"
    documentos.mkdir()
    archivo = entrada / "soat.txt"
    archivo.write_text("SOAT", encoding="utf-8")
    llm.respuestas.append(_respuesta_llm("Documentos/Moto", 0.99))
    archivo_id = _indexar(servicios, archivo)
    duda = cliente.get("/api/pending").json()[0]
    assert duda["carpeta_etiqueta"] == "Documentos/Moto" and not duda["carpeta_existe"]
    r = cliente.post("/api/feedback", json={"archivo_id": archivo_id, "carpeta_elegida": duda["carpeta_sugerida"]})
    assert r.status_code == 200, r.text
    assert (documentos / "Moto" / "soat.txt").exists() and not archivo.exists()


def test_modelo_pequeno_preselecciona_carpetas_y_no_mueve_solo(entorno):
    cliente, servicios, llm, entrada, tmp_path = entorno
    for i in range(30):
        (tmp_path / "Documentos" / f"Otra{i}").mkdir(parents=True)
    (tmp_path / "Documentos" / "Cálculo Integral").mkdir()
    llm.max_carpetas, llm.confianza_maxima, llm.max_caracteres_clasificacion = 5, 0.8, 50
    servicios.vectores.similitudes = lambda consulta, textos: [0.0] * len(textos)
    archivo = entrada / "parcial.txt"
    archivo.write_text("Parcial de integrales " + "x" * 500, encoding="utf-8")
    llm.respuestas.append(_respuesta_llm("Documentos/Cálculo Integral", 0.99))
    _indexar(servicios, archivo)
    assert "- Documentos/Cálculo Integral" in llm.ultimo_prompt
    assert llm.ultimo_prompt.count("- Documentos/") == 5 and "x" * 60 not in llm.ultimo_prompt
    assert archivo.exists()  # lo decide el usuario
    assert cliente.get("/api/pending").json()[0]["carpeta_existe"] is True

    # Con un ejemplo previo del usuario que lo respalda, sí se mueve.
    destino = (tmp_path / "Documentos" / "Cálculo Integral").resolve()
    servicios.vectores.vecinos_aprendizaje = lambda c, n: [Vecino("x", destino.as_posix(), 0.9, "parcial.txt")]
    otro = entrada / "parcial2.txt"
    otro.write_text("Parcial de integrales dos", encoding="utf-8")
    llm.respuestas.append(_respuesta_llm("Documentos/Cálculo Integral", 0.99))
    _indexar(servicios, otro)
    assert (destino / "parcial2.txt").exists()


def test_cambia_de_motor_al_agotarse_la_cuota_y_vuelve(entorno):
    from nexora.core_ia.llm import AdaptadorConRespaldo, ErrorLLM

    cliente, servicios, principal, entrada, tmp_path = entorno
    respaldo = LLMFalso()
    respaldo.modelo, respaldo.confianza_maxima = "local", 0.8
    mixto = AdaptadorConRespaldo(principal, respaldo)
    ahora = [1000.0]
    mixto.reloj = lambda: ahora[0]
    servicios.llm = servicios.clasificador.llm = servicios.explicador.llm = mixto
    (tmp_path / "organizado" / "Finanzas").mkdir(parents=True)
    normal = principal.generar_json

    async def sin_cuota(*_):
        raise ErrorLLM("cuota", cuota=True, espera=3600)

    def nuevo(nombre):
        ruta = entrada / nombre
        ruta.write_text(nombre, encoding="utf-8")
        return ruta

    # Se agota: el mismo archivo se procesa con el respaldo, con los límites del respaldo (no se mueve solo).
    principal.generar_json = sin_cuota
    respaldo.respuestas.append(_respuesta_llm("Finanzas", 0.99))
    uno = nuevo("uno.txt")
    archivo_id = _indexar(servicios, uno)
    assert uno.exists() and cliente.get("/api/pending").json()[0]["confianza"] == 0.8
    estado = cliente.get("/api/stats").json()
    assert estado["usando_respaldo"] and estado["motor_ia"] == "falso:local" and estado["vuelve_en"] == 900
    assert cliente.post(f"/api/files/{archivo_id}/explain", json={}).json()["modelo"] == "falso:local"
    ajustes = cliente.get("/api/settings").json()
    assert (ajustes["llm"], ajustes["respaldo"]) == ("falso:prueba", "falso:local")

    # Se renueva: vuelve solo al principal.
    ahora[0] += 901
    principal.generar_json = normal
    principal.respuestas.append(_respuesta_llm("Finanzas", 0.99))
    _indexar(servicios, nuevo("dos.txt"))
    assert (tmp_path / "organizado" / "Finanzas" / "dos.txt").exists()
    assert not cliente.get("/api/stats").json()["usando_respaldo"]

    # Un error que no es de cuota no cambia de motor.
    async def caido(*_):
        raise ErrorLLM("sin red")

    principal.generar_json = caido
    _indexar(servicios, nuevo("tres.txt"))
    assert cliente.get("/api/stats").json()["errores"] == 1
    assert not mixto.vuelve_en


def test_espera_indicada_por_gemini():
    from nexora.core_ia.llm.gemini_adapter import _segundos_de_espera

    assert _segundos_de_espera(Exception("{'@type': 'x.RetryInfo', 'retryDelay': '29389s'}")) == 29389
    assert _segundos_de_espera(Exception("429")) is None


def test_el_respaldo_aprende_del_principal_sin_mover_solo(entorno):
    cliente, servicios, llm, entrada, tmp_path = entorno
    (tmp_path / "organizado" / "Finanzas").mkdir(parents=True)
    uno = entrada / "uno.txt"
    uno.write_text("Factura", encoding="utf-8")
    llm.respuestas.append(_respuesta_llm("Finanzas", 0.95))
    archivo_id = _indexar(servicios, uno)
    doc, meta = servicios.vectores.ejemplos[f"ia:{archivo_id}"]
    assert meta["fuente"] == "ia" and meta["categoria"] == "Finanzas"

    ejemplo = Vecino("e", "Finanzas", 0.95, "uno.txt", "ia")
    servicios.vectores.vecinos_aprendizaje = lambda c, n: [ejemplo]
    dos = entrada / "dos.txt"
    dos.write_text("Factura dos", encoding="utf-8")
    llm.respuestas.append(_respuesta_llm("Finanzas", 0.5))
    _indexar(servicios, dos)
    assert "uno.txt" not in llm.ultimo_prompt  # el principal no recibe sus propios ejemplos

    llm.ensena, llm.confianza_maxima = False, 0.8
    for nombre, carpeta in (("tres.txt", "Finanzas"), ("cuatro.txt", "Otra")):
        ruta = entrada / nombre
        ruta.write_text(nombre, encoding="utf-8")
        llm.respuestas.append(_respuesta_llm(carpeta, 0.99))
        _indexar(servicios, ruta)
        assert "uno.txt" in llm.ultimo_prompt and ruta.exists()  # aprende, pero pregunta
    dudas = {d["archivo"]["nombre"]: d for d in cliente.get("/api/pending").json()}
    assert dudas["cuatro.txt"]["alternativas"][0] == "Finanzas"
    assert not any(k.startswith("ia:") and k != f"ia:{archivo_id}" for k in servicios.vectores.ejemplos)


def test_correccion_por_texto_propone_y_guarda_el_motivo(entorno):
    cliente, servicios, llm, entrada, tmp_path = entorno
    (tmp_path / "organizado" / "Finanzas").mkdir(parents=True)
    (tmp_path / "Documentos" / "Hogar").mkdir(parents=True)
    ids = {}
    for nombre in ("factura.txt", "recibo.txt"):
        (entrada / nombre).write_text(nombre, encoding="utf-8")
        llm.respuestas.append(_respuesta_llm("Finanzas", 0.95))
        ids[nombre] = _indexar(servicios, entrada / nombre)
    actual = tmp_path / "organizado" / "Finanzas" / "factura.txt"

    llm.respuestas.append({"archivo": 1, "carpeta": "Documentos/Hogar"})
    texto = "La factura quedó mal, va en Hogar"
    p = cliente.post("/api/corrections", json={"texto": texto}).json()
    assert p["archivo"]["id"] == ids["factura.txt"] and p["carpeta_etiqueta"] == "Documentos/Hogar" and p["carpeta_existe"]
    assert actual.exists()  # solo propone
    r = cliente.post("/api/feedback", json={"archivo_id": p["archivo"]["id"], "carpeta_elegida": p["carpeta"], "motivo": texto})
    assert r.status_code == 200 and r.json()["fue_correccion"]
    assert (tmp_path / "Documentos" / "Hogar" / "factura.txt").exists() and not actual.exists()
    assert any(m["motivo"] == texto for _, m in servicios.vectores.ejemplos.values())

    # El motivo llega a la IA con el siguiente archivo parecido.
    servicios.vectores.vecinos_aprendizaje = lambda c, n: [Vecino("x", p["carpeta"], 0.9, "factura.txt", "usuario", texto)]
    (entrada / "otra.txt").write_text("otra", encoding="utf-8")
    llm.respuestas.append(_respuesta_llm("Documentos/Hogar", 0.9))
    _indexar(servicios, entrada / "otra.txt")
    assert texto in llm.ultimo_prompt

    # Sin destino claro, o un archivo que no existe: no inventa.
    llm.respuestas.append({"archivo": 1, "carpeta": "Finanzas"})
    p = cliente.post("/api/corrections", json={"texto": "esto está mal", "archivo_id": ids["recibo.txt"]}).json()
    assert p["archivo"]["nombre"] == "recibo.txt" and p["carpeta"] is None
    p = cliente.post("/api/corrections", json={"texto": "zzzz qqqq"}).json()
    assert p["archivo"] is None and p["candidatos"] == []


def test_migra_indice_y_la_decision_del_usuario_reemplaza_la_de_la_ia(tmp_path):
    from nexora.core_ia.vector_store import AlmacenChroma

    def embebedor(textos):
        return [[float(len(t)), 1.0, 0.0] for t in textos]

    import chromadb

    viejo = chromadb.PersistentClient(path=str(tmp_path / "c"), settings=chromadb.Settings(anonymized_telemetry=False))
    viejo.get_or_create_collection("archivos", metadata={"hnsw:space": "cosine"}).upsert(
        ids=["a"], documents=["Archivo: a.txt"], embeddings=[[0.1, 0.2]], metadatas=[{"nombre": "a.txt"}]
    )
    almacen = AlmacenChroma(tmp_path / "c", embebedor)
    assert almacen.buscar_archivos("x", 5)[0][0] == "a"
    assert almacen.registrar_ejemplo("ia:a", "a", "Uno", False, "ia")
    assert almacen.registrar_ejemplo("f1", "a", "Dos", True, "usuario", "va en Dos")
    assert almacen.registrar_ejemplo("ia:a", "a", "Uno", False, "ia")  # no pisa al usuario
    vecinos = almacen.vecinos_aprendizaje("x", 5)
    assert [(v.categoria, v.fuente, v.motivo) for v in vecinos] == [("Dos", "usuario", "va en Dos")]


def test_indicaciones_del_usuario(entorno):
    cliente, servicios, llm, entrada, tmp_path = entorno
    hogar = tmp_path / "Documentos" / "Hogar"
    hogar.mkdir(parents=True)
    (tmp_path / "organizado" / "Finanzas").mkdir(parents=True)

    def indexar(nombre, carpeta="Finanzas", confianza=0.95, indicacion=0):
        ruta = entrada / nombre
        ruta.write_text(nombre, encoding="utf-8")
        llm.respuestas.append({**_respuesta_llm(carpeta, confianza), "indicacion": indicacion})
        _indexar(servicios, ruta)
        return ruta

    # Interpretar no guarda nada.
    llm.respuestas.append({"entendida": True, "resumen": "Los recibos van en Hogar.", "accion": "mover", "carpeta": "Documentos/Hogar"})
    leida = cliente.post("/api/rules/interpret", json={"texto": "los recibos van en Hogar"}).json()
    assert leida["carpeta_etiqueta"] == "Documentos/Hogar" and leida["carpeta_existe"]
    assert cliente.get("/api/rules").json() == []

    regla = cliente.post("/api/rules", json={
        "texto": "los recibos van en Hogar", "resumen": leida["resumen"], "accion": "mover", "carpeta": leida["carpeta"],
    }).json()
    indexar("recibo.txt", indicacion=1)
    assert "1. los recibos van en Hogar (Los recibos van en Hogar.) -> carpeta Documentos/Hogar" in llm.ultimo_prompt
    assert (hogar / "recibo.txt").exists()
    assert cliente.get("/api/rules").json()[0]["usos"] == 1

    # No encaja, o número inventado: se clasifica normal.
    indexar("a.txt", indicacion=0)
    indexar("b.txt", indicacion=9)
    assert (tmp_path / "organizado/Finanzas/a.txt").exists() and (tmp_path / "organizado/Finanzas/b.txt").exists()

    # La indicación gana a un ejemplo previo contrario.
    servicios.vectores.vecinos_aprendizaje = lambda c, n: [Vecino("x", "Finanzas", 0.97, "z.txt")]
    indexar("recibo2.txt", indicacion=1)
    assert (hogar / "recibo2.txt").exists()
    servicios.vectores.vecinos_aprendizaje = lambda c, n: []

    # En pausa no se envía.
    cliente.patch(f"/api/rules/{regla['id']}", json={"activa": False})
    indexar("recibo3.txt", indicacion=1)
    assert "Indicaciones del usuario" not in llm.ultimo_prompt and not (hogar / "recibo3.txt").exists()

    # no_mover: se queda; con un modelo pequeño, pregunta.
    cliente.post("/api/rules", json={"texto": "no muevas instaladores", "accion": "no_mover"})
    assert indexar("setup.txt", indicacion=1).exists()
    fila = next(e for e in cliente.get("/api/activity").json() if e["nombre"] == "setup.txt")
    assert fila["estado"] == "clasificado" and fila["carpeta"] is None
    llm.ensena = False
    llm.respuestas.append({"tipo": "instalador", "indicacion": 1})  # pregunta aparte para modelos pequeños
    assert indexar("setup2.txt").exists()
    llm.respuestas.append({"tipo": "otro", "indicacion": 0})
    indexar("normal.txt", indicacion=1)
    assert "Indicaciones del usuario" not in llm.ultimo_prompt and (tmp_path / "organizado/Finanzas/normal.txt").exists()
    llm.ensena = True
    cliente.post("/api/rules", json={"texto": "contratos: pregúntame", "accion": "preguntar"})
    assert indexar("contrato.txt", indicacion=1).exists()
    dudas = {d["archivo"]["nombre"]: d for d in cliente.get("/api/pending").json()}
    assert set(dudas) == {"setup2.txt", "contrato.txt"} and "contratos: pregúntame" in dudas["contrato.txt"]["razonamiento"]

    # Carpetas: nueva solo con permiso; nunca del sistema.
    nueva = {"texto": "fotos a Album", "accion": "mover", "carpeta": "Album"}
    assert cliente.post("/api/rules", json=nueva).status_code == 422
    assert cliente.post("/api/rules", json={**nueva, "crear_carpeta": True}).status_code == 201
    assert (tmp_path / "organizado" / "Album").is_dir()
    assert cliente.post("/api/rules", json={**nueva, "carpeta": "C:/Windows", "crear_carpeta": True}).status_code == 422
    assert cliente.post("/api/rules", json={"texto": "sin carpeta", "accion": "mover"}).status_code == 422
    assert cliente.delete(f"/api/rules/{regla['id']}").status_code == 204
    assert cliente.delete(f"/api/rules/{regla['id']}").status_code == 404


def test_deshacer_movimientos(entorno):
    cliente, servicios, llm, entrada, tmp_path = entorno
    (tmp_path / "organizado" / "Finanzas").mkdir(parents=True)
    archivo = entrada / "f.txt"
    archivo.write_text("Factura", encoding="utf-8")
    llm.respuestas.append(_respuesta_llm("Finanzas", 0.95))
    archivo_id = _indexar(servicios, archivo)
    movido = tmp_path / "organizado" / "Finanzas" / "f.txt"
    assert movido.exists() and f"ia:{archivo_id}" in servicios.vectores.ejemplos
    assert next(e for e in cliente.get("/api/activity").json() if e["id"] == archivo_id)["se_puede_deshacer"]

    # Deshacer lo que hizo la IA: vuelve a su sitio, a la Bandeja, y se olvida el ejemplo.
    r = cliente.post(f"/api/files/{archivo_id}/undo")
    assert r.status_code == 200, r.text
    assert archivo.exists() and not movido.exists()
    duda = cliente.get("/api/pending").json()[0]
    assert duda["archivo"]["id"] == archivo_id and "Deshiciste" in duda["razonamiento"]
    assert not any(m.get("archivo_id") == archivo_id for _, m in servicios.vectores.ejemplos.values())
    assert cliente.post(f"/api/files/{archivo_id}/undo").status_code == 409

    # Deshacer una decisión del usuario; si el sitio original está ocupado, no se sobrescribe.
    destino = tmp_path / "Otra"
    destino.mkdir()
    cliente.post("/api/feedback", json={"archivo_id": archivo_id, "carpeta_elegida": str(destino)})
    archivo.write_text("ocupado", encoding="utf-8")
    assert cliente.post(f"/api/files/{archivo_id}/undo").status_code == 200
    assert archivo.read_text(encoding="utf-8") == "ocupado" and (entrada / "f (1).txt").exists()

    # Si el archivo ya no está donde lo dejó Nexora, no se puede deshacer.
    cliente.post("/api/feedback", json={"archivo_id": archivo_id, "carpeta_elegida": str(destino)})
    (destino / "f (1).txt").unlink()
    assert cliente.post(f"/api/files/{archivo_id}/undo").status_code == 410


def test_duplicados(entorno, monkeypatch):
    import send2trash

    cliente, servicios, llm, entrada, tmp_path = entorno
    (tmp_path / "organizado" / "Finanzas").mkdir(parents=True)
    original = entrada / "factura.txt"
    original.write_text("Factura 123", encoding="utf-8")
    llm.respuestas.append(_respuesta_llm("Finanzas", 0.95))
    original_id = _indexar(servicios, original)
    copia = entrada / "factura (copia).txt"
    copia.write_text("Factura 123", encoding="utf-8")
    copia_id = _indexar(servicios, copia)  # sin respuesta preparada: no se consulta a la IA
    duda = cliente.get("/api/pending").json()[0]
    assert duda["duplicado_de"]["id"] == original_id and duda["carpeta_etiqueta"] == "Finanzas"
    assert cliente.post(f"/api/files/{original_id}/trash").status_code == 409  # solo copias

    enviados = []
    monkeypatch.setattr(send2trash, "send2trash", lambda ruta: (enviados.append(ruta), Path(ruta).unlink()))
    assert cliente.post(f"/api/files/{copia_id}/trash").status_code == 204
    assert enviados == [str(copia)] and not copia.exists() and cliente.get("/api/pending").json() == []

    # Una copia de un archivo que ya no existe se clasifica normal; los vacíos no son duplicados.
    (tmp_path / "organizado" / "Finanzas" / "factura.txt").unlink()
    otra = entrada / "otra.txt"
    otra.write_text("Factura 123", encoding="utf-8")
    llm.respuestas.append(_respuesta_llm("Finanzas", 0.95))
    _indexar(servicios, otra)
    assert (tmp_path / "organizado" / "Finanzas" / "otra.txt").exists()
    for n in ("v1.txt", "v2.txt"):
        (entrada / n).write_text("", encoding="utf-8")
        llm.respuestas.append(_respuesta_llm("Finanzas", 0.95))
        _indexar(servicios, entrada / n)
    assert all(d["duplicado_de"] is None for d in cliente.get("/api/pending").json())


def test_organizar_carpeta_existente_por_lotes(entorno, monkeypatch):
    from nexora.file_watcher import organizador as org

    cliente, servicios, llm, entrada, tmp_path = entorno
    (tmp_path / "organizado" / "Finanzas").mkdir(parents=True)
    vieja = tmp_path / "Vieja"
    (vieja / "sub").mkdir(parents=True)
    (vieja / "proyecto" / ".git").mkdir(parents=True)
    for i in range(3):
        (vieja / f"a{i}.txt").write_text(f"archivo {i}", encoding="utf-8")
    (vieja / "sub" / "b.txt").write_text("sub", encoding="utf-8")
    (vieja / "proyecto" / "c.txt").write_text("codigo", encoding="utf-8")
    (vieja / "~$temp.docx").write_text("x", encoding="utf-8")
    monkeypatch.setattr(org, "TAMANO_LOTE", 2)

    assert cliente.post("/api/organize", json={"ruta": "C:/"}).status_code == 422
    e = cliente.post("/api/organize", json={"ruta": str(vieja), "recursiva": True}).json()
    assert (e["total"], e["estado"], e["hechos"]) == (4, "lista", 0)  # sin proyectos ni temporales; nada analizado

    def lote(aprobar):
        # El análisis corre en el bucle del servidor; se espera consultando el estado.
        assert cliente.post("/api/organize/next").status_code == 200
        for _ in range(200):
            estado = cliente.get("/api/organize").json()
            if estado["estado"] != "analizando":
                break
        return estado, cliente.post("/api/organize/approve", json={"aprobados": aprobar(estado)}).json()

    llm.respuestas += [_respuesta_llm("Finanzas", 0.99), _respuesta_llm("Nueva/Carpeta", 0.99)]
    estado, r = lote(lambda e: {d["archivo"]["id"]: d["carpeta_sugerida"] for d in e["lote"] if d["carpeta_existe"]})
    assert len(estado["lote"]) == 2
    assert (tmp_path / "organizado" / "Finanzas" / "a0.txt").exists() and (vieja / "a1.txt").exists()  # la nueva se pregunta
    assert r["estado"] == "lista" and r["restantes"] == 2
    assert [d["archivo"]["nombre"] for d in cliente.get("/api/pending").json()] == ["a1.txt"]

    llm.respuestas += [_respuesta_llm("Finanzas", 0.99), _respuesta_llm("Finanzas", 0.99)]
    estado, r = lote(lambda e: {})  # no aprueba nada: queda en la Bandeja
    assert r["estado"] == "terminada" and len(cliente.get("/api/pending").json()) == 3
    assert cliente.delete("/api/organize").status_code == 204 and not cliente.get("/api/organize").json()["activa"]


def test_respaldo_tambien_si_el_principal_se_cae(entorno):
    from nexora.core_ia.llm import AdaptadorConRespaldo, ErrorLLM

    cliente, servicios, principal, entrada, tmp_path = entorno
    respaldo = LLMFalso()
    respaldo.modelo = "local"
    mixto = AdaptadorConRespaldo(principal, respaldo)
    servicios.llm = servicios.clasificador.llm = mixto

    async def caido(*_):
        raise ErrorLLM("503 UNAVAILABLE", reintentable=True)

    async def mal_configurado(*_):
        raise ErrorLLM("400 API key not valid")

    def nuevo(nombre):
        ruta = entrada / nombre
        ruta.write_text(nombre, encoding="utf-8")
        return ruta

    principal.generar_json = mal_configurado
    _indexar(servicios, nuevo("a.txt"))
    assert not mixto.vuelve_en and cliente.get("/api/stats").json()["errores"] == 1  # no oculta el error de clave

    principal.generar_json = caido
    respaldo.respuestas.append(_respuesta_llm("Finanzas", 0.5))
    _indexar(servicios, nuevo("b.txt"))
    estado = cliente.get("/api/stats").json()
    assert estado["usando_respaldo"] and estado["motivo_respaldo"] == "caida" and 0 < estado["vuelve_en"] <= 120
