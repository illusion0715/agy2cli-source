"""Daily aggregate counters only: no prompts, responses, models, identities or keys."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


class Store:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "usage.sqlite3"
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS usage (
                day TEXT, route TEXT, status INTEGER, requests INTEGER,
                input_tokens INTEGER, output_tokens INTEGER, elapsed_ms INTEGER,
                PRIMARY KEY(day, route, status))""")

    @contextmanager
    def connect(self):
        # sqlite3.Connection.__exit__ handles commit/rollback, but does not close.
        # Each operation owns its own connection, including when transaction exit fails.
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def record(self, route: str, status: int, elapsed_ms: int, usage: dict):
        # A storage failure must not turn a completed upstream request into a retry.
        try:
            with self.connect() as db:
                day = datetime.now(timezone.utc).date().isoformat()
                db.execute("""INSERT INTO usage VALUES (?, ?, ?, 1, ?, ?, ?)
                    ON CONFLICT(day,route,status) DO UPDATE SET
                    requests=requests+1, input_tokens=input_tokens+excluded.input_tokens,
                    output_tokens=output_tokens+excluded.output_tokens,
                    elapsed_ms=elapsed_ms+excluded.elapsed_ms""",
                    (day, route, status, usage.get("prompt_tokens", 0),
                     usage.get("completion_tokens", 0), elapsed_ms))
                db.execute("DELETE FROM usage WHERE day < date('now', '-90 days')")
        except sqlite3.Error:
            pass

    def summary(self):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(
                "SELECT * FROM usage ORDER BY day DESC, route, status")]
