"""Uvicorn entrypoint for the demo webhook receiver."""

from cloud_stub.app import create_app

app = create_app()
