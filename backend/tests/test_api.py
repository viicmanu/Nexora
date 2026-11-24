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
            Vecino(i, meta["categoria"], self._similitud(consulta, doc), meta["nombre"])
            for i, (doc, meta) in self.ejemplos.items()
        ]
        return sorted(res, key=lambda v: v.similitud, reverse=True)[:n]

    def registrar_ejemplo(self, ejemplo_id: str, archivo_id: str, categoria: str, fue_correccion: bool) -> bool:
        if archivo_id not in self.archivos:
            return False
        doc, meta = self.archivos[archivo_id]
        self.ejemplos[ejemplo_id] = (doc, {**meta, "categoria": categoria})
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
    for carpeta in ["../fuera", "C:/Windows", "CON", "a/./b"]:
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
