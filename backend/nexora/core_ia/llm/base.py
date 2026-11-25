"""Contrato común (patrón Adapter) que cumple cada proveedor de LLM."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, Literal, TypeVar

T = TypeVar("T")
Esfuerzo = Literal["bajo", "medio", "alto"]


class ErrorLLM(RuntimeError):
    """Fallo al consultar el modelo. `reintentable` indica si tiene sentido volver a intentarlo más tarde."""

    def __init__(
        self, mensaje: str, *, reintentable: bool = False, cuota: bool = False, espera: float | None = None
    ) -> None:
        super().__init__(mensaje)
        self.reintentable = reintentable
        self.cuota = cuota  # se agotó el límite del proveedor
        self.espera = espera  # segundos hasta que se renueva, si el proveedor lo indica


class AdaptadorLLM(ABC):
    proveedor: str
    modelo: str
    # Límites para modelos pequeños (None = sin límite).
    max_caracteres_clasificacion: int | None = None
    max_caracteres_explicacion: int | None = None
    max_carpetas: int | None = None
    # Confianza máxima que se le concede sin ejemplos previos del usuario que la respalden.
    confianza_maxima: float = 1.0
    # Sus aciertos se guardan como ejemplos para que aprenda el motor de respaldo.
    ensena: bool = True

    @abstractmethod
    async def generar_json(self, sistema: str, prompt: str, esquema: dict[str, Any]) -> dict[str, Any]:
        """Devuelve un objeto JSON que cumple `esquema` (JSON Schema con additionalProperties=false)."""

    @abstractmethod
    async def generar_texto(self, sistema: str, prompt: str, *, max_tokens: int = 4000, esfuerzo: Esfuerzo = "medio") -> str:
        """Devuelve una respuesta en texto libre."""

    async def ejecutar(self, operacion: Callable[["AdaptadorLLM"], Awaitable[T]]) -> T:
        """Ejecuta la operación con el motor que corresponda. Debe construir el prompt con los límites de ese motor."""
        return await operacion(self)

    @property
    def descripcion(self) -> str:
        return f"{self.proveedor}:{self.modelo}"
