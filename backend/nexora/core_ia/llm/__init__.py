"""Fábrica de adaptadores LLM: el resto del backend solo conoce `AdaptadorLLM`."""

from __future__ import annotations

from nexora.config import Settings
from nexora.core_ia.llm.base import AdaptadorLLM, ErrorLLM


def crear_adaptador(settings: Settings) -> AdaptadorLLM:
    proveedor = settings.llm_proveedor
    modelo = settings.llm_modelo

    if proveedor == "anthropic":
        from nexora.core_ia.llm.anthropic_adapter import AdaptadorAnthropic

        return AdaptadorAnthropic(modelo)

    if not modelo:
        raise ErrorLLM(f"Define NEXORA_LLM_MODELO para el proveedor '{proveedor}'.")
    if proveedor == "openai":
        from nexora.core_ia.llm.openai_adapter import AdaptadorOpenAI

        return AdaptadorOpenAI(modelo)
    if proveedor == "gemini":
        from nexora.core_ia.llm.gemini_adapter import AdaptadorGemini

        return AdaptadorGemini(modelo)
    if proveedor == "ollama":
        from nexora.core_ia.llm.ollama_adapter import AdaptadorOllama

        return AdaptadorOllama(modelo, settings.ollama_url)
    raise ErrorLLM(f"Proveedor de LLM desconocido: {proveedor}")


__all__ = ["AdaptadorLLM", "ErrorLLM", "crear_adaptador"]
