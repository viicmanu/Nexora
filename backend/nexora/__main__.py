"""Arranque: python -m nexora"""

from __future__ import annotations

import logging

import asyncio
from dotenv import load_dotenv


def main() -> None:
    # Carga backend/.env también en os.environ para que los SDK lean sus claves (ANTHROPIC_API_KEY, etc.).
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from nexora.config import Settings
    from nexora.main import create_app
    from nexora.servidor import servir

    settings = Settings()
    asyncio.run(servir(create_app(settings), settings))


if __name__ == "__main__":
    main()
