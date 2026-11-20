from dataclasses import dataclass, field
from pathlib import Path
import os

OFFICIAL_ORIGIN = "https://generativelanguage.googleapis.com"


def usable(value: str, minimum: int) -> bool:
    return len(value) >= minimum and not value.lower().startswith(
        ("replace_", "example_", "change_me")
    )


@dataclass(frozen=True)
class Settings:
    gateway_key: str = field(default="", repr=False)
    gemini_key: str = field(default="", repr=False)
    data_dir: Path = Path("/data")
    timeout_seconds: float = 180
    max_body_bytes: int = 1_048_576
    max_concurrent: int = 16

    def __post_init__(self):
        if not 5 <= self.timeout_seconds <= 600:
            raise ValueError("UPSTREAM_TIMEOUT_SECONDS must be between 5 and 600")
        if not 1024 <= self.max_body_bytes <= 16_777_216:
            raise ValueError("MAX_BODY_BYTES is outside the supported range")
        if not 1 <= self.max_concurrent <= 128:
            raise ValueError("MAX_CONCURRENT must be between 1 and 128")
        if self.gateway_key and self.gateway_key == self.gemini_key:
            raise ValueError("Gateway and upstream keys must be different")

    @property
    def configured(self) -> bool:
        return usable(self.gateway_key, 32) and usable(self.gemini_key, 1)

    @classmethod
    def from_env(cls):
        return cls(
            gateway_key=os.getenv("GATEWAY_API_KEY", ""),
            gemini_key=os.getenv("GEMINI_API_KEY", ""),
            data_dir=Path(os.getenv("DATA_DIR", "/data")),
            timeout_seconds=float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "180")),
            max_body_bytes=int(os.getenv("MAX_BODY_BYTES", "1048576")),
            max_concurrent=int(os.getenv("MAX_CONCURRENT", "16")),
        )
