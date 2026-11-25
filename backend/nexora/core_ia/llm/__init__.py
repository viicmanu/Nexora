"""Fábrica de adaptadores LLM: el resto del backend solo conoce `AdaptadorLLM`."""

from __future__ import annotations

from nexora.config import Settings
from nexora.core_ia.llm.base import AdaptadorLLM, ErrorLLM
from nexora.core_ia.llm.respaldo import AdaptadorConRespaldo


def crear_adaptador(settings: Settings) -> AdaptadorLLM:
    principal = _crear(settings, settings.llm_proveedor, settings.llm_modelo)
    if not settings.llm_respaldo_proveedor:
        return principal
    respaldo = _crear(settings, settings.llm_respaldo_proveedor, settings.llm_respaldo_modelo)
    return AdaptadorConRespaldo(principal, respaldo)


def _crear(settings: Settings, proveedor: str, modelo: str | None) -> AdaptadorLLM:
    if proveedor == "anthropic":
        from nexora.core_ia.llm.anthropic_adapter import AdaptadorAnthropic

        return AdaptadorAnthropic(modelo)

    if not modelo:
        raise ErrorLLM(f"Falta el modelo (NEXORA_LLM_MODELO o NEXORA_LLM_RESPALDO_MODELO) para el proveedor '{proveedor}'.")
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


__all__ = ["AdaptadorConRespaldo", "AdaptadorLLM", "ErrorLLM", "crear_adaptador"]
