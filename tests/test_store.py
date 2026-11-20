"""Connection lifecycle tests retain handles so correctness cannot depend on GC."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from src.gateway.store import Store


class TrackedConnection(sqlite3.Connection):
    close_calls = 0
    fail_cleanup = False
    fail_create = False

    def execute(self, sql, *args, **kwargs):
        if self.fail_cleanup and sql.startswith("DELETE FROM usage"):
            raise sqlite3.OperationalError("synthetic cleanup failure")
        if self.fail_create and sql.startswith("CREATE TABLE"):
            raise sqlite3.OperationalError("synthetic initialization failure")
        return super().execute(sql, *args, **kwargs)

    def close(self):
        self.close_calls += 1
        return super().close()


class StoreLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.handles = []
        self.traces = []
        self.fail_cleanup = False
        self.fail_create = False
        original = sqlite3.connect

        def connect(*args, **kwargs):
            db = original(*args, **kwargs, factory=TrackedConnection)
            db.fail_cleanup = self.fail_cleanup
            db.fail_create = self.fail_create
            statements = []
            db.set_trace_callback(statements.append)
            self.handles.append(db)
            self.traces.append(statements)
            return db

        self.patch = patch("src.gateway.store.sqlite3.connect", side_effect=connect)
        self.patch.start()
        self.store = Store(Path(self.tmp.name))

    def tearDown(self):
        # Always clean up even on the unfixed implementation, to expose one precise failure.
        for handle in self.handles:
            if not handle.close_calls:
                handle.close()
        self.patch.stop()
        self.tmp.cleanup()

    def assert_closed(self):
        for handle in self.handles:
            self.assertEqual(handle.close_calls, 1)
            with self.assertRaises(sqlite3.ProgrammingError):
                handle.execute("SELECT 1")

    def test_operations_close_and_file_can_be_deleted_and_recreated(self):
        self.store.record("chat", 200, 10, {"prompt_tokens": 3})
        self.assertEqual(self.store.summary()[0]["requests"], 1)
        self.assert_closed()
        self.assertTrue(any("COMMIT" in statements for statements in self.traces))
        self.store.path.unlink()  # Native Windows enforces the file-handle requirement.
        self.assertFalse(self.store.path.exists())
        fresh = Store(Path(self.tmp.name))
        self.assertEqual(fresh.summary(), [])
        self.assert_closed()

    def test_exception_rolls_back_and_closes(self):
        with self.assertRaisesRegex(RuntimeError, "synthetic"):
            with self.store.connect() as db:
                db.execute("INSERT INTO usage VALUES ('2000-01-01','chat',200,1,0,0,0)")
                raise RuntimeError("synthetic application failure")
        self.assertTrue(any("ROLLBACK" in statements for statements in self.traces))
        self.assertEqual(self.store.summary(), [])
        self.assert_closed()

    def test_record_failure_after_insert_rolls_back_and_closes(self):
        self.fail_cleanup = True
        self.store.record("chat", 200, 10, {})
        self.fail_cleanup = False
        self.assertEqual(self.store.summary(), [])
        self.assertTrue(any("ROLLBACK" in statements for statements in self.traces))
        self.assert_closed()

    def test_commit_failure_rolls_back_and_closes(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.connect() as db:
                db.execute("PRAGMA foreign_keys=ON")
                db.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
                db.execute("""CREATE TABLE child(parent_id INTEGER REFERENCES parent(id)
                              DEFERRABLE INITIALLY DEFERRED)""")
                db.execute("INSERT INTO child VALUES (999)")
        self.assertTrue(any("ROLLBACK" in statements for statements in self.traces))
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM child").fetchone()[0], 0)
        self.assert_closed()

    def test_initialization_failure_closes(self):
        self.fail_create = True
        with self.assertRaises(sqlite3.OperationalError):
            Store(Path(self.tmp.name))
        self.assert_closed()

    def test_parallel_operations_use_distinct_owned_connections(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: self.store.record("chat", 200, 1, {}), range(16)))
        self.assertEqual(self.store.summary()[0]["requests"], 16)
        self.assertEqual(len({id(handle) for handle in self.handles}), 18)
        self.assert_closed()


if __name__ == "__main__":
    unittest.main()
