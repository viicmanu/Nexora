"""Adaptador para OpenAI (pip install "nexora-backend[openai]"). Credenciales: OPENAI_API_KEY."""

from __future__ import annotations

import json
from typing import Any

from nexora.core_ia.llm.base import AdaptadorLLM, ErrorLLM, Esfuerzo


class AdaptadorOpenAI(AdaptadorLLM):
    proveedor = "openai"

    def __init__(self, modelo: str) -> None:
        try:
            import openai
        except ImportError as exc:
            raise ErrorLLM('Falta el paquete openai: pip install "nexora-backend[openai]"') from exc
        self._openai = openai
        self.modelo = modelo
        self._cliente = openai.AsyncOpenAI()

    async def generar_json(self, sistema: str, prompt: str, esquema: dict[str, Any]) -> dict[str, Any]:
        texto = await self._llamar(
            sistema, prompt,
            response_format={"type": "json_schema", "json_schema": {"name": "resultado", "schema": esquema, "strict": True}},
        )
        try:
            return json.loads(texto)
        except json.JSONDecodeError as exc:
            raise ErrorLLM(f"OpenAI devolvió JSON no válido: {exc}") from exc

    async def generar_texto(self, sistema: str, prompt: str, *, max_tokens: int = 4000, esfuerzo: Esfuerzo = "medio") -> str:
        return await self._llamar(sistema, prompt, max_completion_tokens=max_tokens)

    async def _llamar(self, sistema: str, prompt: str, **opciones: Any) -> str:
        try:
            respuesta = await self._cliente.chat.completions.create(
                model=self.modelo,
                messages=[{"role": "system", "content": sistema}, {"role": "user", "content": prompt}],
                **opciones,
            )
        except self._openai.RateLimitError as exc:
            raise ErrorLLM("Límite de peticiones de OpenAI alcanzado.", reintentable=True) from exc
        except self._openai.APIStatusError as exc:
            raise ErrorLLM(f"Error de la API de OpenAI ({exc.status_code}).", reintentable=exc.status_code >= 500) from exc
        except self._openai.APIConnectionError as exc:
            raise ErrorLLM("No se pudo conectar con la API de OpenAI.", reintentable=True) from exc
        texto = (respuesta.choices[0].message.content or "").strip()
        if not texto:
            raise ErrorLLM("OpenAI devolvió una respuesta vacía.", reintentable=True)
        return texto
