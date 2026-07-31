from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PersistentRateLimiter:
    """A conservative account/host limiter shared by local processes."""

    def __init__(
        self,
        database_path: Path,
        *,
        minimum_interval_seconds: float | None = None,
        max_wait_seconds: float = 300,
    ) -> None:
        self.database_path = database_path
        self.minimum_interval_seconds = (
            minimum_interval_seconds
            if minimum_interval_seconds is not None
            else float(os.getenv("WB_MIN_REQUEST_INTERVAL_SECONDS", "0.6"))
        )
        self.max_wait_seconds = max_wait_seconds

    def acquire(self, *, account_id: str, host: str, method_id: str) -> None:
        """Wait for the host bucket, then atomically reserve the next slot."""
        scope_key = self._scope_key(account_id, host)
        while True:
            now = time.time()
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT next_allowed_epoch FROM rate_limit_buckets WHERE scope_key=?",
                    (scope_key,),
                ).fetchone()
                due = float(row[0]) if row else 0.0
                wait_seconds = max(due - now, 0.0)
                if wait_seconds <= 0:
                    next_allowed = now + self.minimum_interval_seconds
                    connection.execute(
                        """
                        INSERT INTO rate_limit_buckets(
                            scope_key, account_id, host, method_id,
                            next_allowed_epoch, reason, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 'scheduled', ?)
                        ON CONFLICT(scope_key) DO UPDATE SET
                            method_id=excluded.method_id,
                            next_allowed_epoch=excluded.next_allowed_epoch,
                            reason=excluded.reason,
                            updated_at=excluded.updated_at
                        """,
                        (
                            scope_key,
                            account_id,
                            host,
                            method_id,
                            next_allowed,
                            _utc_now(),
                        ),
                    )
                    return
            if wait_seconds > self.max_wait_seconds:
                raise TimeoutError(
                    f"Rate-limit cooldown for {host} is {wait_seconds:.1f}s; "
                    "run later or increase the local maximum wait"
                )
            time.sleep(min(wait_seconds, 1.0))

    def observe(
        self,
        *,
        account_id: str,
        host: str,
        method_id: str,
        http_status: int,
        retry_after_seconds: float | None,
    ) -> None:
        """Persist cooldown learned from WB responses."""
        penalty = 0.0
        reason = "response"
        if http_status == 429:
            penalty = (
                retry_after_seconds
                if retry_after_seconds is not None
                else max(self.minimum_interval_seconds * 10, 3.0)
            )
            reason = "429"
        elif 400 <= http_status < 500:
            multiplier = float(os.getenv("WB_4XX_PENALTY_MULTIPLIER", "10"))
            penalty = self.minimum_interval_seconds * max(multiplier, 1)
            reason = "weighted_4xx"
        if penalty <= 0:
            return
        scope_key = self._scope_key(account_id, host)
        proposed = time.time() + penalty
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT next_allowed_epoch FROM rate_limit_buckets WHERE scope_key=?",
                (scope_key,),
            ).fetchone()
            current = float(row[0]) if row else 0.0
            connection.execute(
                """
                INSERT INTO rate_limit_buckets(
                    scope_key, account_id, host, method_id, next_allowed_epoch,
                    last_http_status, reason, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope_key) DO UPDATE SET
                    method_id=excluded.method_id,
                    next_allowed_epoch=excluded.next_allowed_epoch,
                    last_http_status=excluded.last_http_status,
                    reason=excluded.reason,
                    updated_at=excluded.updated_at
                """,
                (
                    scope_key,
                    account_id,
                    host,
                    method_id,
                    max(current, proposed),
                    http_status,
                    reason,
                    _utc_now(),
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @staticmethod
    def _scope_key(account_id: str, host: str) -> str:
        return hashlib.sha256(f"{account_id}|{host.lower()}".encode()).hexdigest()
