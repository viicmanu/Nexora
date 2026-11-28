"""Teléfono vinculado por la red local: regla 1 a 1, latidos, subidas reanudables, descargas y bandeja remota."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from nexora.security import emparejamiento, red
from nexora.servidor import app_red_local
from test_api import LLMFalso, _indexar, _respuesta_llm, entorno  # noqa: F401  (el fixture se reutiliza)


@pytest.fixture
def lan(entorno, monkeypatch):  # noqa: F811
    """Cliente que entra como si viniera de la Wi-Fi (servidor de la red local), con red privada."""
    cliente, servicios, llm, entrada, tmp_path = entorno
    privada = {"valor": True}
    monkeypatch.setattr(red, "comprobar_privada", lambda ip: privada["valor"])
    monkeypatch.setattr(red, "ips_lan", lambda: ["192.168.1.5"])
    movil = TestClient(app_red_local(cliente.app), base_url="https://192.168.1.5:8767")
    movil.__enter__()
    yield cliente, movil, servicios, llm, entrada, tmp_path, privada
    movil.__exit__(None, None, None)


def _codigo(cliente, **params):
    r = cliente.post("/api/devices/pairing-code", params=params)
    assert r.status_code == 200, r.text
    return json.loads(r.json()["contenido_qr"])


def _vincular(movil, qr, nombre="Mi teléfono"):
    return movil.post("/api/devices/pair", json={"codigo": qr["codigo"], "nombre": nombre, "plataforma": "android"})


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_qr_vinculacion_1_a_1_y_reemplazo(lan):
    cliente, movil, servicios, *_ = lan
    qr = _codigo(cliente)
    assert qr["v"] == 2 and qr["hosts"] == ["192.168.1.5"] and qr["puerto"] == 8767 and len(qr["huella"]) == 64
    qr2 = _codigo(cliente)

    a = _vincular(movil, qr, "Teléfono A")
    assert a.status_code == 201, a.text
    token_a = a.json()["token"]
    assert a.json()["pc_id"] and "files:upload" in a.json()["permisos"]

    # Un segundo teléfono con un QR anterior: 409, y el código no se gasta.
    b = _vincular(movil, qr2, "Teléfono B")
    assert b.status_code == 409 and "ya tiene un teléfono vinculado" in b.json()["detail"]
    assert cliente.post("/api/devices/pairing-code").status_code == 409

    # Reemplazar: el anterior queda fuera y el nuevo entra.
    qr3 = _codigo(cliente, reemplazar=True)
    assert movil.post("/api/devices/heartbeat", headers=_auth(token_a)).status_code == 401
    c = _vincular(movil, qr3, "Teléfono C")
    assert c.status_code == 201
    assert movil.post("/api/devices/heartbeat", headers=_auth(c.json()["token"])).status_code == 200
    assert cliente.get("/api/devices/phone").json()["telefono"]["nombre"] == "Teléfono C"


def test_regla_1_a_1_en_la_base_y_sin_carreras(lan):
    cliente, movil, servicios, *_ = lan
    codigos = [_codigo(cliente)["codigo"] for _ in range(6)]
    resultados = []

    def canjear(c):
        try:
            resultados.append(emparejamiento.canjear_codigo(servicios.db, c, "x", "android", None) is not None)
        except emparejamiento.TelefonoYaVinculado:
            resultados.append("409")

    hilos = [threading.Thread(target=canjear, args=(c,)) for c in codigos]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert resultados.count(True) == 1 and resultados.count("409") == 5

    with servicios.db.conectar() as con, pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO dispositivos_vinculados (id, nombre, tipo, token_hash, token_prefijo) VALUES ('x2', 'b', 'movil', 'h2', 'p')"
        )


def test_red_local_solo_telefono_vinculado_y_red_privada(lan):
    cliente, movil, servicios, llm, entrada, tmp_path, privada = lan
    token = _vincular(movil, _codigo(cliente)).json()["token"]
    escritorio = cliente.headers["Authorization"]

    assert movil.get("/api/files/search", params={"q": "x"}, headers=_auth(token)).status_code == 200
    assert movil.get("/api/files/search", params={"q": "x"}, headers={"Authorization": escritorio}).status_code == 401
    assert movil.get("/api/files/search", params={"q": "x"}, headers=_auth("nx_inventado")).status_code == 401
    assert movil.get("/docs").status_code == 404
    salud = movil.get("/api/health").json()
    assert salud["app"] == "nexora" and "llm" not in salud
    # El teléfono no gestiona carpetas, ajustes ni dispositivos.
    for metodo, ruta in [("get", "/api/folders"), ("put", "/api/settings"), ("post", "/api/devices/pairing-code"),
                         ("get", "/api/rules"), ("post", "/api/organize")]:
        assert getattr(movil, metodo)(ruta, headers=_auth(token)).status_code in (403, 422), ruta
    # En la app de escritorio todo sigue igual.
    assert cliente.get("/api/folders").status_code == 200

    privada["valor"] = False
    r = movil.get("/api/files/search", params={"q": "x"}, headers=_auth(token))
    assert r.status_code == 403 and "pública" in r.json()["detail"]
    assert _vincular(movil, {"codigo": "y" * 32}).status_code == 403


def test_latido_estado_en_linea_y_eventos(lan):
    cliente, movil, servicios, *_ = lan
    vinculado = _vincular(movil, _codigo(cliente)).json()
    token = vinculado["token"]
    assert cliente.get("/api/devices/phone").json()["telefono"]["en_linea"] is False

    eventos: list[str] = []

    def escuchar():
        with cliente.stream("GET", "/api/devices/events", params={"max_eventos": 1, "espera": 5}) as r:
            eventos.extend(l for l in r.iter_lines() if l.startswith("data:"))

    hilo = threading.Thread(target=escuchar)
    hilo.start()
    time.sleep(0.5)
    info = movil.post("/api/devices/heartbeat", headers=_auth(token)).json()
    hilo.join(10)
    assert info["pc_id"] == vinculado["pc_id"] and info["ips"] == ["192.168.1.5"]
    datos = [json.loads(e[5:]) for e in eventos]
    assert datos[0]["tipo"] == "estado" and datos[1] == {"tipo": "telefono", "dispositivo_id": vinculado["dispositivo_id"], "en_linea": True}
    telefono = cliente.get("/api/devices/phone").json()["telefono"]
    assert telefono["en_linea"] and telefono["ultima_ip"]

    # Sin latidos en 90 s: desconectado.
    hace = (datetime.now(timezone.utc) - timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with servicios.db.conectar() as con:
        con.execute("UPDATE dispositivos_vinculados SET ultimo_heartbeat = ? WHERE id = ?", (hace, vinculado["dispositivo_id"]))
    assert cliente.get("/api/devices/phone").json()["telefono"]["en_linea"] is False

    # Renombrar y desvincular; la sesión del teléfono se corta.
    assert cliente.patch(f"/api/devices/{vinculado['dispositivo_id']}", json={"nombre": "Pixel"}).json()["nombre"] == "Pixel"
    assert cliente.delete(f"/api/devices/{vinculado['dispositivo_id']}").status_code == 204
    assert movil.get("/api/devices/pc", headers=_auth(token)).status_code == 401


def _subir_en_fragmentos(movil, token, datos, cliente_id="c1", nombre="factura.txt", cortar_en=None):
    sha = hashlib.sha256(datos).hexdigest()
    s = movil.post("/api/uploads", headers=_auth(token), json={"cliente_id": cliente_id, "nombre": nombre, "tamano": len(datos), "sha256": sha})
    assert s.status_code == 200, s.text
    return s.json()


def test_subida_por_fragmentos_reanudable_y_sin_duplicados(lan):
    cliente, movil, servicios, llm, entrada, tmp_path, _ = lan
    (tmp_path / "organizado" / "Finanzas").mkdir(parents=True)
    servicios.settings.carpeta_recibidos = tmp_path / "recibidos"
    token = _vincular(movil, _codigo(cliente), "Pixel").json()["token"]
    datos = b"Factura de agua de marzo. Total 82.300 pesos. " * 4000  # ~180 KB
    subida = _subir_en_fragmentos(movil, token, datos)
    assert subida["estado"] == "recibiendo" and subida["bytes_recibidos"] == 0

    # Primer fragmento, y la Wi-Fi se corta: el teléfono pregunta y sigue desde donde quedó.
    r = movil.put(f"/api/uploads/{subida['id']}", params={"offset": 0}, content=datos[:100_000], headers=_auth(token))
    assert r.json()["bytes_recibidos"] == 100_000
    r = movil.put(f"/api/uploads/{subida['id']}", params={"offset": 0}, content=datos[:100_000], headers=_auth(token))
    assert r.status_code == 409 and r.json()["bytes_recibidos"] == 100_000
    reanudada = _subir_en_fragmentos(movil, token, datos)  # misma cola: misma subida
    assert reanudada["id"] == subida["id"] and reanudada["bytes_recibidos"] == 100_000
    assert movil.post(f"/api/uploads/{subida['id']}/complete", headers=_auth(token)).status_code == 409  # incompleta
    movil.put(f"/api/uploads/{subida['id']}", params={"offset": 100_000}, content=datos[100_000:], headers=_auth(token))

    llm.respuestas.append(_respuesta_llm("Finanzas", 0.95))
    fin = movil.post(f"/api/uploads/{subida['id']}/complete", headers=_auth(token))
    assert fin.status_code == 200 and fin.json()["estado"] in ("recibida", "organizada")
    for _ in range(100):
        estado = movil.get(f"/api/uploads/{subida['id']}", headers=_auth(token)).json()
        if estado["estado"] == "organizada":
            break
        time.sleep(0.05)
    assert estado["estado"] == "organizada" and estado["archivo"]["estado"] == "clasificado"
    final = tmp_path / "organizado" / "Finanzas" / "factura.txt"
    assert final.read_bytes() == datos and estado["archivo"]["origen_dispositivo"] == "móvil: Pixel"

    # Reenviar el mismo elemento de la cola no duplica nada.
    otra_vez = _subir_en_fragmentos(movil, token, datos)
    assert otra_vez["id"] == subida["id"] and otra_vez["estado"] == "organizada"
    assert movil.post(f"/api/uploads/{subida['id']}/complete", headers=_auth(token)).json()["estado"] == "organizada"
    assert len(list((tmp_path / "recibidos").glob("*"))) == 0 and len(list(final.parent.glob("*"))) == 1
    assert "Pixel" in next(e for e in cliente.get("/api/activity").json() if e["nombre"] == "factura.txt")["origen_dispositivo"]


def test_subida_validaciones(lan):
    cliente, movil, servicios, llm, entrada, tmp_path, _ = lan
    token = _vincular(movil, _codigo(cliente)).json()["token"]
    sha = "a" * 64
    pedir = lambda **c: movil.post("/api/uploads", headers=_auth(token), json={"cliente_id": "z", "nombre": "a.txt", "tamano": 10, "sha256": sha, **c})
    assert pedir(nombre="../../evil.txt").status_code == 422
    assert pedir(nombre="C:\\Windows\\a.txt").status_code == 422
    assert pedir(nombre="setup.exe").status_code == 415
    cliente.put("/api/settings", json={"max_subida_mb": 1})
    assert pedir(tamano=2 * 1024 * 1024).status_code == 413
    assert pedir(sha256="no-es-hash").status_code == 422

    # Hash distinto: se descarta y se pide de nuevo desde cero.
    s = pedir(cliente_id="h", tamano=3).json()
    movil.put(f"/api/uploads/{s['id']}", params={"offset": 0}, content=b"abc", headers=_auth(token))
    assert movil.put(f"/api/uploads/{s['id']}", params={"offset": 3}, content=b"x", headers=_auth(token)).status_code == 413
    r = movil.post(f"/api/uploads/{s['id']}/complete", headers=_auth(token))
    assert r.status_code == 422 and movil.get(f"/api/uploads/{s['id']}", headers=_auth(token)).json()["bytes_recibidos"] == 0
    assert movil.delete(f"/api/uploads/{s['id']}", headers=_auth(token)).status_code == 204
    assert movil.get(f"/api/uploads/{s['id']}", headers=_auth(token)).json()["estado"] == "cancelada"


def test_organizados_descarga_y_bandeja_remota(lan):
    cliente, movil, servicios, llm, entrada, tmp_path, _ = lan
    (tmp_path / "organizado" / "Finanzas").mkdir(parents=True)
    token = _vincular(movil, _codigo(cliente)).json()["token"]
    for nombre, conf in (("a.txt", 0.95), ("b.pdf", 0.95), ("duda.txt", 0.4)):
        (entrada / nombre).write_bytes(nombre.encode())
        llm.respuestas.append(_respuesta_llm("Finanzas", conf))
        _indexar(servicios, entrada / nombre)

    r = movil.get("/api/files/organized", headers=_auth(token)).json()
    assert sorted(i["nombre"] for i in r["items"]) == ["a.txt", "b.pdf"] and len(r["ids"]) == 2
    item = next(i for i in r["items"] if i["nombre"] == "b.pdf")
    assert item["tipo"] == "pdf" and item["carpeta_etiqueta"] == "Finanzas" and item["confianza"] == 0.95
    assert movil.get("/api/files/organized", params={"since": r["hasta"]}, headers=_auth(token)).json()["items"] == []

    descarga = movil.get(f"/api/files/{item['id']}/download", headers=_auth(token))
    assert descarga.status_code == 200 and descarga.content == b"b.pdf" and "b.pdf" in descarga.headers["content-disposition"]
    parcial = movil.get(f"/api/files/{item['id']}/download", headers={**_auth(token), "Range": "bytes=2-"})
    assert parcial.status_code == 206 and parcial.content == b"pdf"
    assert movil.get("/api/files/no-existe/download", headers=_auth(token)).status_code == 404
    assert movil.get("/api/files/..%2F..%2Fwindows/download", headers=_auth(token)).status_code == 404

    # Bandeja desde el teléfono: ver, elegir carpeta, ignorar.
    dudas = movil.get("/api/pending", headers=_auth(token)).json()
    assert [d["archivo"]["nombre"] for d in dudas] == ["duda.txt"]
    assert movil.get("/api/destinations", headers=_auth(token)).status_code == 200
    r = movil.post("/api/feedback", headers=_auth(token), json={"archivo_id": dudas[0]["archivo"]["id"], "carpeta_elegida": "Finanzas"})
    assert r.status_code == 200 and (tmp_path / "organizado" / "Finanzas" / "duda.txt").exists()
    (entrada / "otra.txt").write_bytes(b"otra")
    llm.respuestas.append(_respuesta_llm("Finanzas", 0.4))
    _indexar(servicios, entrada / "otra.txt")
    duda = movil.get("/api/pending", headers=_auth(token)).json()[0]
    assert movil.post(f"/api/pending/{duda['pendiente_id']}/ignore", headers=_auth(token)).status_code == 204
    assert movil.get("/api/pending", headers=_auth(token)).json() == [] and (entrada / "otra.txt").exists()
    assert movil.post(f"/api/pending/{duda['pendiente_id']}/ignore", headers=_auth(token)).status_code == 404


def test_actualizacion_de_la_app_del_telefono(lan):
    cliente, movil, servicios, llm, entrada, tmp_path, _ = lan
    token = _vincular(movil, _codigo(cliente)).json()["token"]
    assert movil.post("/api/devices/heartbeat", headers=_auth(token)).json()["app_movil"] is None
    assert movil.get("/api/devices/app/apk", headers=_auth(token)).status_code == 404

    carpeta = servicios.settings.dir_datos / "app_movil"
    carpeta.mkdir()
    (carpeta / "nexora.apk").write_bytes(b"APK de prueba")
    (carpeta / "version.json").write_text(json.dumps({"codigo": 4242, "fecha": "2026-11-27T00:00:00Z", "tamano": 13, "sha256": "x"}))
    assert movil.post("/api/devices/heartbeat", headers=_auth(token)).json()["app_movil"]["codigo"] == 4242
    r = movil.get("/api/devices/app/apk", headers=_auth(token))
    assert r.status_code == 200 and r.content == b"APK de prueba" and "android.package-archive" in r.headers["content-type"]
    assert movil.get("/api/devices/app/apk").status_code == 401  # solo el teléfono vinculado
    assert cliente.get("/api/devices/phone").json()["app_movil"]["codigo"] == 4242


def test_informes_de_fallos_del_telefono(lan):
    cliente, movil, servicios, *_ = lan
    token = _vincular(movil, _codigo(cliente), "Pixel").json()["token"]
    texto = "tipo: excepcion_java\nfecha: x\ndispositivo: Xiaomi\nandroid: 14\nversion_app: 1\n\njava.lang.IllegalStateException: camara\n\tat app.tauri"
    r = movil.post("/api/devices/crash", headers=_auth(token), json={"nombre": "java-1.txt", "texto": texto, "version": "1"})
    assert r.status_code == 204
    assert movil.post("/api/devices/crash", headers=_auth(token), json={"nombre": "../malo.txt", "texto": "x"}).status_code == 422
    assert movil.get("/api/devices/crashes", headers=_auth(token)).status_code == 403  # solo el PC los lee
    lista = cliente.get("/api/devices/crashes").json()
    assert lista[0]["tipo"] == "excepcion_java" and "IllegalStateException" in lista[0]["primera_linea"]
    detalle = cliente.get(f"/api/devices/crashes/{lista[0]['archivo']}").json()
    assert "telefono: Pixel" in detalle["texto"] and "IllegalStateException" in detalle["texto"]
    assert cliente.get("/api/devices/crashes/..%5C..%5Cnexora.db").status_code == 404
