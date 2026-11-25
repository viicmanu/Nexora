"""Cambio automático de motor: si se agota la cuota del principal, se usa el de respaldo hasta que se renueve."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from nexora.core_ia.llm.base import AdaptadorLLM, ErrorLLM, Esfuerzo

log = logging.getLogger(__name__)
T = TypeVar("T")

ESPERA_POR_DEFECTO = 15 * 60  # segundos, si el proveedor no dice cuándo se renueva
# Aunque el proveedor anuncie horas, se comprueba antes: la cuota puede renovarse o ampliarse.
ESPERA_MAXIMA = 15 * 60


class AdaptadorConRespaldo(AdaptadorLLM):
    def __init__(self, principal: AdaptadorLLM, respaldo: AdaptadorLLM) -> None:
        self.principal = principal
        self.respaldo = respaldo
        self._agotado_hasta = 0.0
        self.reloj: Callable[[], float] = time.time

    @property
    def vuelve_en(self) -> int:
        """Segundos hasta volver a intentar con el principal (0 si está disponible)."""
        return max(0, round(self._agotado_hasta - self.reloj()))

    @property
    def activo(self) -> AdaptadorLLM:
        return self.respaldo if self.vuelve_en else self.principal

    @property
    def descripcion(self) -> str:
        return self.activo.descripcion

    async def ejecutar(self, operacion: Callable[[AdaptadorLLM], Awaitable[T]]) -> T:
        if self.activo is self.principal:
            try:
                return await operacion(self.principal)
            except ErrorLLM as exc:
                if not exc.cuota:
                    raise
                espera = min(max(exc.espera or ESPERA_POR_DEFECTO, 5), ESPERA_MAXIMA)
                self._agotado_hasta = self.reloj() + espera
                log.warning("Cuota agotada en %s: se usa %s", self.principal.descripcion, self.respaldo.descripcion)
        try:
            return await operacion(self.respaldo)
        except ErrorLLM as exc:
            raise ErrorLLM(
                f"Se agotó la cuota de {self.principal.descripcion} y el respaldo falló: {exc}", reintentable=True
            ) from exc

    async def generar_json(self, sistema: str, prompt: str, esquema: dict[str, Any]) -> dict[str, Any]:
        return await self.ejecutar(lambda llm: llm.generar_json(sistema, prompt, esquema))

    async def generar_texto(
        self, sistema: str, prompt: str, *, max_tokens: int = 4000, esfuerzo: Esfuerzo = "medio"
    ) -> str:
        return await self.ejecutar(
            lambda llm: llm.generar_texto(sistema, prompt, max_tokens=max_tokens, esfuerzo=esfuerzo)
        )
