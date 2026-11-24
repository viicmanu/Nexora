"""Configuración del backend, leída de variables de entorno con prefijo NEXORA_ o de backend/.env."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ProveedorLLM = Literal["anthropic", "openai", "gemini", "ollama"]


def _dir_datos_por_defecto() -> Path:
    base = os.environ.get("APPDATA") or os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "Nexora"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NEXORA_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Red: solo localhost en la Fase 1. La app móvil (Fase 2) entrará por un túnel o relay, no abriendo este puerto.
    host: str = "127.0.0.1"
    port: int = 8765
    cors_origenes: list[str] = ["http://localhost:1420", "http://tauri.localhost", "tauri://localhost"]
    exponer_docs: bool = True

    # Almacenamiento local (SQLite, ChromaDB y token del escritorio).
    dir_datos: Path = Field(default_factory=_dir_datos_por_defecto)

    # Carpetas que se vigilan y raíz donde se organizan los archivos clasificados.
    # Formato en .env: NEXORA_CARPETAS_VIGILADAS='["C:/Users/yo/Downloads"]'
    carpetas_vigiladas: list[Path] = Field(default_factory=lambda: [Path.home() / "Downloads"])
    raiz_organizada: Path = Field(default_factory=lambda: Path.home() / "Nexora")
    habilitar_watcher: bool = True

    # Motor de clasificación.
    umbral_confianza: float = Field(default=0.85, ge=0, le=1)
    llm_proveedor: ProveedorLLM = "anthropic"
    llm_modelo: str | None = None  # obligatorio salvo con anthropic (por defecto claude-opus-5-5)
    ollama_url: str = "http://localhost:11434"
    max_caracteres_clasificacion: int = 12_000
    max_caracteres_explicacion: int = 400_000

    @property
    def ruta_db(self) -> Path:
        return self.dir_datos / "nexora.db"

    @property
    def ruta_chroma(self) -> Path:
        return self.dir_datos / "chroma"

    @property
    def ruta_token_escritorio(self) -> Path:
        return self.dir_datos / "desktop.token"
