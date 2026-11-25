"""Adaptador para Gemini (pip install "nexora-backend[gemini]"). Credenciales: GEMINI_API_KEY o GOOGLE_API_KEY."""

from __future__ import annotations

import json
import re
from typing import Any

from nexora.core_ia.llm.base import AdaptadorLLM, ErrorLLM, Esfuerzo

TIEMPO_LIMITE_MS = 60_000


def _segundos_de_espera(exc: Exception) -> float | None:
    """Gemini indica cuándo reintentar en RetryInfo ("retryDelay": "29389s")."""
    encontrado = re.search(r"retryDelay\W+([\d.]+)s", str(exc))
    return float(encontrado.group(1)) if encontrado else None


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
        # Sin límite, un modelo saturado dejaría el watcher bloqueado indefinidamente.
        self._cliente = genai.Client(
            http_options=types.HttpOptions(timeout=TIEMPO_LIMITE_MS, retry_options=types.HttpRetryOptions(attempts=1))
        )

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
            if getattr(exc, "code", None) == 429:
                raise ErrorLLM(
                    f"Se agotó la cuota de Gemini para {self.modelo}. Espera a que se renueve o cambia de modelo.",
                    reintentable=True, cuota=True, espera=_segundos_de_espera(exc),
                ) from exc
            raise ErrorLLM(f"Error al consultar Gemini: {exc}", reintentable=True) from exc
        texto = (respuesta.text or "").strip()
        if not texto:
            raise ErrorLLM("Gemini devolvió una respuesta vacía.", reintentable=True)
        return texto
