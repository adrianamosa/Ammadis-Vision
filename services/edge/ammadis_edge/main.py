"""Uvicorn entrypoint. Loads AMMADIS_SITE_CONFIG (default /config/site.yaml)."""

from ammadis_edge.app import create_app

app = create_app()
