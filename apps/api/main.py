"""ASGI entry point used by Uvicorn."""

from awn.api.app import create_app

app = create_app()
