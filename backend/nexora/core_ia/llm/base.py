"""Contrato común (patrón Adapter) que cumple cada proveedor de LLM."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal

Esfuerzo = Literal["bajo", "medio", "alto"]


class ErrorLLM(RuntimeError):
    """Fallo al consultar el modelo. `reintentable` indica si tiene sentido volver a intentarlo más tarde."""

    def __init__(self, mensaje: str, *, reintentable: bool = False) -> None:
        super().__init__(mensaje)
        self.reintentable = reintentable


class AdaptadorLLM(ABC):
    proveedor: str
    modelo: str

    @abstractmethod
    async def generar_json(self, sistema: str, prompt: str, esquema: dict[str, Any]) -> dict[str, Any]:
        """Devuelve un objeto JSON que cumple `esquema` (JSON Schema con additionalProperties=false)."""

    @abstractmethod
    async def generar_texto(self, sistema: str, prompt: str, *, max_tokens: int = 4000, esfuerzo: Esfuerzo = "medio") -> str:
        """Devuelve una respuesta en texto libre."""

    @property
    def descripcion(self) -> str:
        return f"{self.proveedor}:{self.modelo}"
