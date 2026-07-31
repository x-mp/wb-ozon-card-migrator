from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import ErrorDecision
from .workflows import JobState, assert_job_transition

SCHEMA_VERSION = 2


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MetadataDatabase:
    """SQLite control plane; large response bodies remain external files."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS accounts (
                    account_id TEXT PRIMARY KEY,
                    seller_sid TEXT UNIQUE,
                    name TEXT,
                    trademark TEXT,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS artifacts (
                    run_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    entity TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    http_status INTEGER,
                    request_url TEXT NOT NULL,
                    request_params_json TEXT NOT NULL,
                    request_body_file TEXT,
                    request_body_sha256 TEXT,
                    content_type TEXT NOT NULL,
                    data_file TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    method_id TEXT,
                    docs_url TEXT,
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS api_events (
                    event_id TEXT PRIMARY KEY,
                    account_id TEXT,
                    run_id TEXT,
                    method_id TEXT,
                    occurred_at TEXT NOT NULL,
                    http_status INTEGER,
                    error_kind TEXT NOT NULL,
                    retryable INTEGER NOT NULL,
                    manual_action INTEGER NOT NULL,
                    next_retry_at TEXT,
                    message TEXT NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES accounts(account_id),
                    FOREIGN KEY(run_id) REFERENCES artifacts(run_id)
                );

                CREATE TABLE IF NOT EXISTS sync_state (
                    account_id TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    entity TEXT NOT NULL,
                    scope_hash TEXT NOT NULL,
                    last_attempt_at TEXT,
                    last_success_at TEXT,
                    last_run_id TEXT,
                    last_error_kind TEXT,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    next_allowed_at TEXT,
                    PRIMARY KEY(account_id, domain, entity, scope_hash),
                    FOREIGN KEY(account_id) REFERENCES accounts(account_id)
                );

                CREATE TABLE IF NOT EXISTS rate_limit_buckets (
                    scope_key TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    host TEXT NOT NULL,
                    method_id TEXT,
                    next_allowed_epoch REAL NOT NULL,
                    last_http_status INTEGER,
                    reason TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS cursors (
                    account_id TEXT NOT NULL,
                    method_id TEXT NOT NULL,
                    scope_hash TEXT NOT NULL,
                    cursor_json TEXT NOT NULL,
                    source_run_id TEXT NOT NULL,
                    committed_at TEXT NOT NULL,
                    PRIMARY KEY(account_id, method_id, scope_hash),
                    FOREIGN KEY(account_id) REFERENCES accounts(account_id),
                    FOREIGN KEY(source_run_id) REFERENCES artifacts(run_id)
                );

                CREATE TABLE IF NOT EXISTS entity_index (
                    account_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    parent_type TEXT,
                    parent_id TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    latest_run_id TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(account_id, entity_type, entity_id),
                    FOREIGN KEY(account_id) REFERENCES accounts(account_id),
                    FOREIGN KEY(latest_run_id) REFERENCES artifacts(run_id)
                );

                CREATE TABLE IF NOT EXISTS artifact_entities (
                    run_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    PRIMARY KEY(run_id, account_id, entity_type, entity_id),
                    FOREIGN KEY(run_id) REFERENCES artifacts(run_id),
                    FOREIGN KEY(account_id, entity_type, entity_id)
                        REFERENCES entity_index(account_id, entity_type, entity_id)
                );

                CREATE TABLE IF NOT EXISTS commands (
                    command_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    entity_type TEXT,
                    entity_id TEXT,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    before_run_id TEXT,
                    approved_at TEXT,
                    applied_at TEXT,
                    verified_at TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(account_id, idempotency_key),
                    FOREIGN KEY(account_id) REFERENCES accounts(account_id)
                );

                CREATE TABLE IF NOT EXISTS command_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    command_id TEXT NOT NULL,
                    attempted_at TEXT NOT NULL,
                    run_id TEXT,
                    http_status INTEGER,
                    outcome TEXT NOT NULL,
                    error_kind TEXT,
                    FOREIGN KEY(command_id) REFERENCES commands(command_id),
                    FOREIGN KEY(run_id) REFERENCES artifacts(run_id)
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    workflow TEXT NOT NULL,
                    remote_job_id TEXT,
                    parameters_json TEXT NOT NULL,
                    parameters_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deadline_at TEXT,
                    next_poll_at TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    result_run_id TEXT,
                    UNIQUE(account_id, workflow, parameters_sha256),
                    FOREIGN KEY(account_id) REFERENCES accounts(account_id),
                    FOREIGN KEY(result_run_id) REFERENCES artifacts(run_id)
                );

                CREATE TABLE IF NOT EXISTS job_events (
                    event_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    run_id TEXT,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id),
                    FOREIGN KEY(run_id) REFERENCES artifacts(run_id)
                );

                CREATE TABLE IF NOT EXISTS documents (
                    document_key TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    service_name TEXT,
                    remote_id TEXT,
                    period_from TEXT,
                    period_to TEXT,
                    effective_at TEXT,
                    created_at_source TEXT,
                    first_seen_at TEXT NOT NULL,
                    latest_seen_at TEXT NOT NULL,
                    latest_run_id TEXT,
                    original_file TEXT,
                    sha256 TEXT,
                    mime_type TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(account_id) REFERENCES accounts(account_id),
                    FOREIGN KEY(latest_run_id) REFERENCES artifacts(run_id)
                );

                CREATE TABLE IF NOT EXISTS normalization_runs (
                    normalization_id TEXT PRIMARY KEY,
                    source_run_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    entity TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    output_file TEXT NOT NULL,
                    output_format TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    FOREIGN KEY(source_run_id) REFERENCES artifacts(run_id),
                    FOREIGN KEY(account_id) REFERENCES accounts(account_id)
                );

                CREATE TABLE IF NOT EXISTS product_latest (
                    account_id TEXT NOT NULL,
                    nm_id TEXT NOT NULL,
                    vendor_code TEXT,
                    imt_id TEXT,
                    card_run_id TEXT,
                    price_run_id TEXT,
                    stock_run_id TEXT,
                    card_observed_at TEXT,
                    price_observed_at TEXT,
                    stock_observed_at TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(account_id, nm_id),
                    FOREIGN KEY(account_id) REFERENCES accounts(account_id)
                );

                CREATE TABLE IF NOT EXISTS product_identifiers (
                    account_id TEXT NOT NULL,
                    identifier_type TEXT NOT NULL,
                    identifier_value TEXT NOT NULL,
                    nm_id TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    latest_run_id TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(account_id, identifier_type, identifier_value),
                    FOREIGN KEY(account_id, nm_id)
                        REFERENCES product_latest(account_id, nm_id),
                    FOREIGN KEY(latest_run_id) REFERENCES artifacts(run_id)
                );

                CREATE INDEX IF NOT EXISTS idx_artifacts_lookup
                    ON artifacts(source, entity, requested_at);
                CREATE INDEX IF NOT EXISTS idx_api_events_action
                    ON api_events(error_kind, manual_action, occurred_at);
                CREATE INDEX IF NOT EXISTS idx_jobs_due
                    ON jobs(state, next_poll_at);
                CREATE INDEX IF NOT EXISTS idx_rate_limits_due
                    ON rate_limit_buckets(account_id, host, next_allowed_epoch);
                CREATE INDEX IF NOT EXISTS idx_documents_lookup
                    ON documents(account_id, category, latest_seen_at);
                CREATE INDEX IF NOT EXISTS idx_entity_parent
                    ON entity_index(account_id, parent_type, parent_id);
                CREATE INDEX IF NOT EXISTS idx_product_identifiers_nm
                    ON product_identifiers(account_id, nm_id);

                CREATE VIEW IF NOT EXISTS latest_success AS
                SELECT source, entity, method_id, MAX(requested_at) AS last_success_at
                FROM artifacts
                WHERE status = 'success'
                GROUP BY source, entity, method_id;

                CREATE VIEW IF NOT EXISTS pending_manual_actions AS
                SELECT *
                FROM api_events
                WHERE manual_action = 1
                ORDER BY occurred_at DESC;

                CREATE VIEW IF NOT EXISTS product_data_status AS
                SELECT
                    p.account_id,
                    p.nm_id,
                    p.vendor_code,
                    p.imt_id,
                    p.card_run_id,
                    p.card_observed_at,
                    p.price_run_id,
                    p.price_observed_at,
                    p.stock_run_id,
                    p.stock_observed_at,
                    p.updated_at,
                    CASE
                        WHEN p.card_run_id IS NULL THEN 'missing_card'
                        WHEN p.price_run_id IS NULL THEN 'missing_price'
                        WHEN p.stock_run_id IS NULL THEN 'missing_stock'
                        ELSE 'complete'
                    END AS completeness
                FROM product_latest p;
                """
            )
            artifact_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(artifacts)").fetchall()
            }
            if "request_body_file" not in artifact_columns:
                connection.execute(
                    "ALTER TABLE artifacts ADD COLUMN request_body_file TEXT"
                )
            if "request_body_sha256" not in artifact_columns:
                connection.execute(
                    "ALTER TABLE artifacts ADD COLUMN request_body_sha256 TEXT"
                )
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def upsert_account(
        self,
        account_id: str,
        *,
        seller_sid: str | None = None,
        name: str | None = None,
        trademark: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO accounts(
                    account_id, seller_sid, name, trademark, created_at,
                    last_seen_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    seller_sid=COALESCE(excluded.seller_sid, accounts.seller_sid),
                    name=COALESCE(excluded.name, accounts.name),
                    trademark=COALESCE(excluded.trademark, accounts.trademark),
                    last_seen_at=excluded.last_seen_at,
                    metadata_json=excluded.metadata_json
                """,
                (
                    account_id,
                    seller_sid,
                    name,
                    trademark,
                    now,
                    now,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )

    def record_api_decision(
        self,
        decision: ErrorDecision,
        *,
        account_id: str | None,
        run_id: str | None,
        method_id: str | None,
        http_status: int | None,
        next_retry_at: str | None = None,
    ) -> str:
        event_id = uuid.uuid4().hex
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO api_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    account_id,
                    run_id,
                    method_id,
                    utc_now(),
                    http_status,
                    decision.kind.value,
                    int(decision.retryable),
                    int(decision.manual_action),
                    next_retry_at,
                    decision.message,
                ),
            )
        return event_id

    def create_mutation_plan(
        self,
        *,
        account_id: str,
        domain: str,
        operation: str,
        payload: Any,
        entity_type: str | None = None,
        entity_id: str | None = None,
        risk_level: str = "high",
        before_run_id: str | None = None,
    ) -> str:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        payload_hash = hashlib.sha256(canonical.encode()).hexdigest()
        idempotency_source = "|".join(
            [account_id, domain, operation, entity_type or "", entity_id or "", payload_hash]
        )
        idempotency_key = hashlib.sha256(idempotency_source.encode()).hexdigest()
        command_id = uuid.uuid4().hex
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT command_id FROM commands WHERE account_id=? AND idempotency_key=?",
                (account_id, idempotency_key),
            ).fetchone()
            if existing:
                return str(existing["command_id"])
            connection.execute(
                """
                INSERT INTO commands(
                    command_id, account_id, domain, operation, entity_type,
                    entity_id, payload_json, payload_sha256, idempotency_key,
                    status, risk_level, before_run_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?, ?, ?)
                """,
                (
                    command_id,
                    account_id,
                    domain,
                    operation,
                    entity_type,
                    entity_id,
                    canonical,
                    payload_hash,
                    idempotency_key,
                    risk_level,
                    before_run_id,
                    utc_now(),
                ),
            )
        return command_id

    def approve_mutation_plan(self, command_id: str, confirmation: str) -> None:
        if confirmation != f"APPROVE:{command_id}":
            raise PermissionError(
                f"Explicit confirmation required: APPROVE:{command_id}"
            )
        with self.connect() as connection:
            command = connection.execute(
                "SELECT status FROM commands WHERE command_id=?", (command_id,)
            ).fetchone()
            if command is None:
                raise KeyError(f"Unknown command: {command_id}")
            if command["status"] != "planned":
                raise ValueError(f"Command is already {command['status']}")
            connection.execute(
                "UPDATE commands SET status='approved', approved_at=? WHERE command_id=?",
                (utc_now(), command_id),
            )

    def create_job(
        self,
        *,
        account_id: str,
        domain: str,
        workflow: str,
        parameters: Any,
        deadline_at: str | None = None,
    ) -> str:
        canonical = json.dumps(parameters, ensure_ascii=False, sort_keys=True)
        parameters_hash = hashlib.sha256(canonical.encode()).hexdigest()
        with self.connect() as connection:
            existing = connection.execute(
                """
                SELECT job_id FROM jobs
                WHERE account_id=? AND workflow=? AND parameters_sha256=?
                """,
                (account_id, workflow, parameters_hash),
            ).fetchone()
            if existing:
                return str(existing["job_id"])
            job_id = uuid.uuid4().hex
            now = utc_now()
            connection.execute(
                """
                INSERT INTO jobs(
                    job_id, account_id, domain, workflow, parameters_json,
                    parameters_sha256, state, created_at, updated_at, deadline_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    account_id,
                    domain,
                    workflow,
                    canonical,
                    parameters_hash,
                    JobState.DRAFT.value,
                    now,
                    now,
                    deadline_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO job_events(
                    event_id, job_id, occurred_at, from_state, to_state, details_json
                ) VALUES (?, ?, ?, NULL, ?, '{}')
                """,
                (uuid.uuid4().hex, job_id, now, JobState.DRAFT.value),
            )
        return job_id

    def transition_job(
        self,
        job_id: str,
        target: JobState,
        *,
        details: dict[str, Any] | None = None,
        run_id: str | None = None,
        remote_job_id: str | None = None,
        next_poll_at: str | None = None,
    ) -> None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT state FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown job: {job_id}")
            current = JobState(row["state"])
            assert_job_transition(current, target)
            now = utc_now()
            connection.execute(
                """
                UPDATE jobs SET
                    state=?, updated_at=?,
                    remote_job_id=COALESCE(?, remote_job_id),
                    next_poll_at=?,
                    result_run_id=COALESCE(?, result_run_id),
                    attempt_count=attempt_count + CASE WHEN ?='polling' THEN 1 ELSE 0 END
                WHERE job_id=?
                """,
                (
                    target.value,
                    now,
                    remote_job_id,
                    next_poll_at,
                    run_id if target in {JobState.VERIFIED, JobState.DONE} else None,
                    target.value,
                    job_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO job_events VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    job_id,
                    now,
                    current.value,
                    target.value,
                    run_id,
                    json.dumps(details or {}, ensure_ascii=False),
                ),
            )

    def list_rows(self, table: str, limit: int = 50) -> list[dict[str, Any]]:
        allowed = {
            "accounts",
            "api_events",
            "rate_limit_buckets",
            "sync_state",
            "commands",
            "jobs",
            "documents",
            "normalization_runs",
            "product_latest",
            "product_identifiers",
            "product_data_status",
            "pending_manual_actions",
        }
        if table not in allowed:
            raise ValueError(f"Unsupported table/view: {table}")
        order_columns = {
            "accounts": "last_seen_at",
            "api_events": "occurred_at",
            "rate_limit_buckets": "updated_at",
            "sync_state": "last_attempt_at",
            "commands": "created_at",
            "jobs": "updated_at",
            "documents": "latest_seen_at",
            "normalization_runs": "completed_at",
            "product_latest": "updated_at",
            "product_identifiers": "last_seen_at",
            "product_data_status": "updated_at",
            "pending_manual_actions": "occurred_at",
        }
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM {table} ORDER BY {order_columns[table]} DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
