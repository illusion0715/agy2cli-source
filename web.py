"""Production entrypoint; tests inject transport via create_app, never an upstream URL."""
from src.gateway.app import create_app
app = create_app()
