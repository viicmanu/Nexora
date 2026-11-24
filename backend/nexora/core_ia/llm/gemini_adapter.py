"""Adaptador para Gemini (pip install "nexora-backend[gemini]"). Credenciales: GEMINI_API_KEY o GOOGLE_API_KEY."""

from __future__ import annotations

import json
from typing import Any

from nexora.core_ia.llm.base import AdaptadorLLM, ErrorLLM, Esfuerzo


class AdaptadorGemini(AdaptadorLLM):
    proveedor = "gemini"

    def __init__(self, modelo: str) -> None:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise ErrorLLM('Falta el paquete google-genai: pip install "nexora-backend[gemini]"') from exc
        self._types = types
        self.modelo = modelo
        self._cliente = genai.Client()

    async def generar_json(self, sistema: str, prompt: str, esquema: dict[str, Any]) -> dict[str, Any]:
        config = self._types.GenerateContentConfig(
            system_instruction=sistema, response_mime_type="application/json", response_json_schema=esquema
        )
        texto = await self._llamar(prompt, config)
        try:
            return json.loads(texto)
        except json.JSONDecodeError as exc:
            raise ErrorLLM(f"Gemini devolvió JSON no válido: {exc}") from exc

    async def generar_texto(self, sistema: str, prompt: str, *, max_tokens: int = 4000, esfuerzo: Esfuerzo = "medio") -> str:
        config = self._types.GenerateContentConfig(system_instruction=sistema, max_output_tokens=max_tokens)
        return await self._llamar(prompt, config)

    async def _llamar(self, prompt: str, config: Any) -> str:
        try:
            respuesta = await self._cliente.aio.models.generate_content(model=self.modelo, contents=prompt, config=config)
        except Exception as exc:  # el SDK de Google no expone una jerarquía de errores estable entre versiones
            raise ErrorLLM(f"Error al consultar Gemini: {exc}", reintentable=True) from exc
        texto = (respuesta.text or "").strip()
        if not texto:
            raise ErrorLLM("Gemini devolvió una respuesta vacía.", reintentable=True)
        return texto
