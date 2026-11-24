"""Adaptador para Claude mediante el SDK oficial `anthropic`.

Credenciales: ANTHROPIC_API_KEY (o un perfil de `ant auth login`), resueltas por el SDK.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic

from nexora.core_ia.llm.base import AdaptadorLLM, ErrorLLM, Esfuerzo

MODELO_POR_DEFECTO = "claude-opus-5-5"

# Modelos que admiten el reintento automático en el servidor cuando el modelo rechaza una petición.
_MODELOS_CON_FALLBACK = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-2", "claude-fable-5-1"}

_ESFUERZO = {"bajo": "low", "medio": "medium", "alto": "high"}


class AdaptadorAnthropic(AdaptadorLLM):
    proveedor = "anthropic"

    def __init__(self, modelo: str | None = None) -> None:
        self.modelo = modelo or MODELO_POR_DEFECTO
        self._cliente = anthropic.AsyncAnthropic()

    async def generar_json(self, sistema: str, prompt: str, esquema: dict[str, Any]) -> dict[str, Any]:
        # Clasificar es una tarea corta: esfuerzo bajo para reducir coste y latencia.
        texto = await self._llamar(
            sistema, prompt, max_tokens=8000,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": esquema}},
        )
        try:
            return json.loads(texto)
        except json.JSONDecodeError as exc:
            raise ErrorLLM(f"Claude devolvió JSON no válido: {exc}") from exc

    async def generar_texto(self, sistema: str, prompt: str, *, max_tokens: int = 4000, esfuerzo: Esfuerzo = "medio") -> str:
        return await self._llamar(sistema, prompt, max_tokens=max_tokens, output_config={"effort": _ESFUERZO[esfuerzo]})

    async def _llamar(self, sistema: str, prompt: str, *, max_tokens: int, output_config: dict[str, Any]) -> str:
        extra: dict[str, Any] = {}
        if self.modelo in _MODELOS_CON_FALLBACK:
            # Si el modelo rechaza la petición, el servidor la reintenta con otro modelo en la misma llamada.
            extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        try:
            respuesta = await self._cliente.beta.messages.create(
                model=self.modelo,
                max_tokens=max_tokens,
                system=sistema,
                messages=[{"role": "user", "content": prompt}],
                output_config=output_config,
                **extra,
            )
        except anthropic.RateLimitError as exc:
            raise ErrorLLM("Límite de peticiones de Anthropic alcanzado.", reintentable=True) from exc
        except anthropic.AuthenticationError as exc:
            raise ErrorLLM("Credenciales de Anthropic no válidas (revisa ANTHROPIC_API_KEY).") from exc
        except anthropic.APIStatusError as exc:
            raise ErrorLLM(f"Error de la API de Anthropic ({exc.status_code}).", reintentable=exc.status_code >= 500) from exc
        except anthropic.APIConnectionError as exc:
            raise ErrorLLM("No se pudo conectar con la API de Anthropic.", reintentable=True) from exc

        if respuesta.stop_reason == "refusal":
            raise ErrorLLM("El modelo rechazó procesar este contenido.")
        if respuesta.stop_reason == "max_tokens":
            raise ErrorLLM("La respuesta del modelo se cortó por longitud.", reintentable=True)
        texto = "".join(b.text for b in respuesta.content if b.type == "text").strip()
        if not texto:
            raise ErrorLLM("El modelo devolvió una respuesta vacía.", reintentable=True)
        return texto
