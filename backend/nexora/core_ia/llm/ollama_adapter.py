"""Adaptador para Ollama (modelos locales, sin enviar datos fuera del PC). Usa su API HTTP en NEXORA_OLLAMA_URL."""

from __future__ import annotations

import json
from typing import Any

import httpx

from nexora.core_ia.llm.base import AdaptadorLLM, ErrorLLM, Esfuerzo


class AdaptadorOllama(AdaptadorLLM):
    proveedor = "ollama"

    def __init__(self, modelo: str, url_base: str) -> None:
        self.modelo = modelo
        self._cliente = httpx.AsyncClient(base_url=url_base, timeout=httpx.Timeout(600, connect=5))

    async def generar_json(self, sistema: str, prompt: str, esquema: dict[str, Any]) -> dict[str, Any]:
        texto = await self._llamar(sistema, prompt, format=esquema, options={"temperature": 0})
        try:
            return json.loads(texto)
        except json.JSONDecodeError as exc:
            raise ErrorLLM(f"Ollama devolvió JSON no válido: {exc}") from exc

    async def generar_texto(self, sistema: str, prompt: str, *, max_tokens: int = 4000, esfuerzo: Esfuerzo = "medio") -> str:
        return await self._llamar(sistema, prompt, options={"num_predict": max_tokens})

    async def _llamar(self, sistema: str, prompt: str, **opciones: Any) -> str:
        cuerpo = {
            "model": self.modelo,
            "stream": False,
            "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": prompt}],
            **opciones,
        }
        try:
            respuesta = await self._cliente.post("/api/chat", json=cuerpo)
            respuesta.raise_for_status()
        except httpx.ConnectError as exc:
            raise ErrorLLM("Ollama no está en ejecución (ollama serve).", reintentable=True) from exc
        except httpx.HTTPStatusError as exc:
            raise ErrorLLM(f"Ollama respondió {exc.response.status_code}: {exc.response.text[:200]}") from exc
        except httpx.HTTPError as exc:
            raise ErrorLLM(f"Error al consultar Ollama: {exc}", reintentable=True) from exc
        texto = respuesta.json().get("message", {}).get("content", "").strip()
        if not texto:
            raise ErrorLLM("Ollama devolvió una respuesta vacía.", reintentable=True)
        return texto
