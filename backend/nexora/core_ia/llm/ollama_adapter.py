"""Adaptador para Ollama (modelos locales, sin enviar datos fuera del PC). Usa su API HTTP en NEXORA_OLLAMA_URL."""

from __future__ import annotations

import json
from typing import Any

import httpx

from nexora.core_ia.llm.base import AdaptadorLLM, ErrorLLM, Esfuerzo

CONTEXTO = 4096


class AdaptadorOllama(AdaptadorLLM):
    proveedor = "ollama"
    # Pensado para modelos de 3-4B en CPU: leer el prompt es lo más lento y suelen ser demasiado confiados,
    # así que por sí solos no mueven archivos (0,80 queda por debajo del umbral por defecto).
    max_caracteres_clasificacion = 2000
    max_caracteres_explicacion = 8000
    max_carpetas = 25
    confianza_maxima = 0.80
    ensena = False

    def __init__(self, modelo: str, url_base: str) -> None:
        self.modelo = modelo
        self._cliente = httpx.AsyncClient(base_url=url_base, timeout=httpx.Timeout(600, connect=5))

    async def generar_json(self, sistema: str, prompt: str, esquema: dict[str, Any]) -> dict[str, Any]:
        texto = await self._llamar(sistema, prompt, format=esquema, options={"temperature": 0, "num_predict": 800})
        try:
            return json.loads(texto)
        except json.JSONDecodeError as exc:
            raise ErrorLLM(f"Ollama devolvió JSON no válido: {exc}") from exc

    async def generar_texto(self, sistema: str, prompt: str, *, max_tokens: int = 4000, esfuerzo: Esfuerzo = "medio") -> str:
        return await self._llamar(sistema, prompt, options={"num_predict": min(max_tokens, 700)})

    async def _llamar(self, sistema: str, prompt: str, **opciones: Any) -> str:
        cuerpo = {
            "model": self.modelo,
            "stream": False,
            "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": prompt}],
            **opciones,
        }
        # Sin num_ctx, Ollama puede recortar el prompt sin avisar.
        cuerpo["options"] = {**cuerpo.get("options", {}), "num_ctx": CONTEXTO}
        try:
            respuesta = await self._cliente.post("/api/chat", json=cuerpo)
            respuesta.raise_for_status()
        except httpx.ConnectError as exc:
            raise ErrorLLM("Ollama no está en ejecución (ollama serve).", reintentable=True) from exc
        except httpx.HTTPStatusError as exc:
            raise ErrorLLM(f"Ollama respondió {exc.response.status_code}: {exc.response.text[:200]}") from exc
        except httpx.HTTPError as exc:
            raise ErrorLLM(f"Error al consultar Ollama: {exc}", reintentable=True) from exc
        datos = respuesta.json()
        if datos.get("done_reason") == "length" and "format" in opciones:
            raise ErrorLLM("La respuesta del modelo se cortó por longitud.", reintentable=True)
        texto = datos.get("message", {}).get("content", "").strip()
        if not texto:
            raise ErrorLLM("Ollama devolvió una respuesta vacía.", reintentable=True)
        return texto
