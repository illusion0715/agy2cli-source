"""Test-image-only real HTTP server. Production web.py never imports this module."""
from pathlib import Path
import httpx
from src.gateway.app import create_app
from src.gateway.settings import Settings
from tests.fixtures import GATEWAY_KEY, OFFICIAL_KEY, official
app = create_app(Settings(gateway_key=GATEWAY_KEY, gemini_key=OFFICIAL_KEY, data_dir=Path("/data")),
                 transport=httpx.MockTransport(official))
