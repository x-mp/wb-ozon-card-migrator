import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wb_assistant.database import MetadataDatabase
from wb_assistant.rate_limit import PersistentRateLimiter


class PersistentRateLimiterTests(unittest.TestCase):
    def test_429_cooldown_is_persisted_for_account_and_host(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = MetadataDatabase(Path(directory) / "catalog.sqlite")
            database.initialize()
            limiter = PersistentRateLimiter(
                database.path, minimum_interval_seconds=0.01
            )
            with mock.patch("wb_assistant.rate_limit.time.time", return_value=100.0):
                limiter.observe(
                    account_id="main",
                    host="content-api.wildberries.ru",
                    method_id="get-test",
                    http_status=429,
                    retry_after_seconds=7,
                )
            with database.connect() as connection:
                row = connection.execute(
                    "SELECT * FROM rate_limit_buckets"
                ).fetchone()
            self.assertEqual(row["reason"], "429")
            self.assertEqual(row["last_http_status"], 429)
            self.assertEqual(row["next_allowed_epoch"], 107.0)

