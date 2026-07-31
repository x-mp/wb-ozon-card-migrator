from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Artifact:
    run_id: str
    source: str
    entity: str
    requested_at: str
    status: str
    http_status: int | None
    request_url: str
    request_params: dict[str, str]
    request_body_file: str | None
    request_body_sha256: str | None
    content_type: str
    data_file: str
    sha256: str
    size_bytes: int
    method_id: str | None = None
    docs_url: str | None = None
    error: str | None = None


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.raw_root = root / "raw"
        self.catalog_path = root / "catalog.sqlite"
        self._initialize_catalog()

    def _connect(self) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.catalog_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_catalog(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
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
                )
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(artifacts)").fetchall()
            }
            if "request_body_file" not in columns:
                connection.execute(
                    "ALTER TABLE artifacts ADD COLUMN request_body_file TEXT"
                )
            if "request_body_sha256" not in columns:
                connection.execute(
                    "ALTER TABLE artifacts ADD COLUMN request_body_sha256 TEXT"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_artifacts_lookup "
                "ON artifacts(source, entity, requested_at)"
            )

    def save(
        self,
        *,
        source: str,
        entity: str,
        request_url: str,
        request_params: dict[str, str],
        request_body: bytes | None = None,
        body: bytes,
        content_type: str,
        http_status: int | None,
        method_id: str | None = None,
        docs_url: str | None = None,
        error: str | None = None,
        requested_at: datetime | None = None,
    ) -> Artifact:
        now = requested_at or datetime.now(timezone.utc)
        run_id = f"{now.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex[:8]}"
        relative_dir = Path(source) / entity / now.strftime("%Y/%m/%d") / run_id
        target_dir = self.raw_root / relative_dir
        target_dir.mkdir(parents=True, exist_ok=False)
        suffix = _suffix_for(content_type)
        data_path = target_dir / f"response{suffix}"
        data_path.write_bytes(body)
        request_body_path: Path | None = None
        request_body_sha256: str | None = None
        if request_body is not None:
            request_body_path = target_dir / "request.json"
            request_body_path.write_bytes(request_body)
            request_body_sha256 = hashlib.sha256(request_body).hexdigest()
        digest = hashlib.sha256(body).hexdigest()
        artifact = Artifact(
            run_id=run_id,
            source=source,
            entity=entity,
            requested_at=now.isoformat(),
            status="error" if error else "success",
            http_status=http_status,
            request_url=request_url,
            request_params=request_params,
            request_body_file=(
                str(request_body_path.relative_to(self.root))
                if request_body_path is not None
                else None
            ),
            request_body_sha256=request_body_sha256,
            content_type=content_type,
            data_file=str(data_path.relative_to(self.root)),
            sha256=digest,
            size_bytes=len(body),
            method_id=method_id,
            docs_url=docs_url,
            error=error,
        )
        manifest_path = target_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(asdict(artifact), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO artifacts (
                    run_id, source, entity, requested_at, status, http_status,
                    request_url, request_params_json, request_body_file,
                    request_body_sha256, content_type, data_file, sha256,
                    size_bytes, method_id, docs_url, error
                ) VALUES (
                    :run_id, :source, :entity, :requested_at, :status,
                    :http_status, :request_url, :request_params_json,
                    :request_body_file, :request_body_sha256, :content_type,
                    :data_file, :sha256, :size_bytes, :method_id, :docs_url, :error
                )
                """,
                {**asdict(artifact), "request_params_json": json.dumps(request_params)},
            )
        return artifact

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT run_id, source, entity, requested_at, status, http_status, "
                "data_file, size_bytes FROM artifacts "
                "ORDER BY requested_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


def _suffix_for(content_type: str) -> str:
    lowered = content_type.lower()
    if "json" in lowered:
        return ".json"
    if "html" in lowered:
        return ".html"
    if "pdf" in lowered:
        return ".pdf"
    if "csv" in lowered:
        return ".csv"
    if lowered.startswith("text/"):
        return ".txt"
    return ".bin"
