"""Embeddings multilingües locales (paraphrase-multilingual-MiniLM-L12-v2 cuantizado, en ONNX).

El modelo (~135 MB) se descarga una vez, de una versión fija y verificando su SHA-256.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

_BASE = (
    "https://huggingface.co/Xenova/paraphrase-multilingual-MiniLM-L12-v2/resolve/"
    "2c4055b12046f11709e9df2c122e59ffbdc2f900/"
)
_ARCHIVOS = {
    "modelo.onnx": ("onnx/model_quantized.onnx", "66fc00f5f29afcaff34092e1bdd20008ca3918265a82fb9695a551e510cc4ebc"),
    "tokenizer.json": ("tokenizer.json", "b60b6b43406a48bf3638526314f3d232d97058bc93472ff2de930d43686fa441"),
}
MAX_TOKENS = 128
LOTE = 32


def _descargar(destino: Path, remoto: str, sha256: str) -> None:
    parcial = destino.with_suffix(".part")
    resumen = hashlib.sha256()
    log.info("Descargando %s (solo la primera vez)", destino.name)
    with httpx.stream("GET", _BASE + remoto, follow_redirects=True, timeout=60) as r, parcial.open("wb") as f:
        r.raise_for_status()
        for bloque in r.iter_bytes():
            resumen.update(bloque)
            f.write(bloque)
    if resumen.hexdigest() != sha256:
        parcial.unlink()
        raise ValueError(f"{destino.name}: la descarga no coincide con la versión esperada")
    parcial.replace(destino)


class EmbeddingsMultilingues:
    def __init__(self, carpeta: Path) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        carpeta.mkdir(parents=True, exist_ok=True)
        for nombre, (remoto, sha256) in _ARCHIVOS.items():
            if not (carpeta / nombre).is_file():
                _descargar(carpeta / nombre, remoto, sha256)
        self._sesion = ort.InferenceSession(str(carpeta / "modelo.onnx"), providers=["CPUExecutionProvider"])
        self._tokenizador = Tokenizer.from_file(str(carpeta / "tokenizer.json"))
        self._tokenizador.enable_truncation(MAX_TOKENS)
        self._tokenizador.enable_padding()

    def __call__(self, textos: list[str]) -> list[list[float]]:
        import numpy as np

        salida: list[list[float]] = []
        for i in range(0, len(textos), LOTE):
            lote = self._tokenizador.encode_batch(textos[i : i + LOTE])
            ids = np.array([e.ids for e in lote], dtype=np.int64)
            mascara = np.array([e.attention_mask for e in lote], dtype=np.int64)
            oculto = self._sesion.run(
                None, {"input_ids": ids, "attention_mask": mascara, "token_type_ids": np.zeros_like(ids)}
            )[0]
            media = (oculto * mascara[..., None]).sum(1) / mascara.sum(1)[:, None]
            salida += (media / np.linalg.norm(media, axis=1, keepdims=True)).tolist()
        return salida
