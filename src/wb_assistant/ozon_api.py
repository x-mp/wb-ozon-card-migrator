from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .ozon_schema import OzonOpenApi, canonical_json
from .storage import Artifact, ArtifactStore


READ_PATHS = {
    "/v1/roles",
    "/v1/description-category/tree",
    "/v1/description-category/attribute",
    "/v1/description-category/attribute/values",
    "/v1/description-category/attribute/values/search",
    "/v4/product/info/limit",
    "/v3/product/list",
    "/v3/product/info/list",
    "/v4/product/info/attributes",
    "/v1/product/info/description",
    "/v2/product/pictures/info",
    "/v1/product/import/info",
    "/v4/product/info/stocks",
    "/v5/product/info/prices",
}

CATALOG_WRITE_PATHS = {"/v3/product/import"}
COMMERCIAL_WRITE_PATHS = {
    "/v1/product/import/prices",
    "/v2/products/stocks",
}


def role_methods(response: dict[str, Any]) -> set[str]:
    methods: set[str] = set()
    for role in response.get("roles") or []:
        if not isinstance(role, dict):
            continue
        for path in role.get("methods") or []:
            if path:
                methods.add(str(path))
    return methods


@dataclass(frozen=True)
class TransportResult:
    status: int
    content_type: str
    body: bytes
    headers: dict[str, str]


@dataclass(frozen=True)
class OzonCall:
    operation_id: str
    path: str
    status: int
    response: dict[str, Any]
    artifact: Artifact


Transport = Callable[[str, bytes, dict[str, str], float], TransportResult]


class OzonSellerClient:
    """Server-side Ozon Seller API client with archived requests and responses."""

    def __init__(
        self,
        *,
        schema: OzonOpenApi,
        store: ArtifactStore,
        account_id: str,
        timeout: float = 30,
        max_retries: int = 3,
        transport: Transport | None = None,
    ) -> None:
        self.schema = schema
        self.store = store
        self.account_id = account_id
        self.timeout = timeout
        self.max_retries = max_retries
        self.transport = transport or self._urlopen_transport

    def read(self, path: str, payload: dict[str, Any] | None = None) -> OzonCall:
        if path not in READ_PATHS:
            raise PermissionError(f"Ozon method is not in the curated read allowlist: {path}")
        return self._post(path, payload or {}, mode="read")

    def upload_catalog(
        self,
        payload: dict[str, Any],
        *,
        confirmation: str,
        expected_confirmation: str,
    ) -> OzonCall:
        path = "/v3/product/import"
        if os.getenv("OZON_ENABLE_WRITES") != "UPLOAD_APPROVED_PLANS":
            raise PermissionError(
                "Ozon writes are disabled. Set OZON_ENABLE_WRITES=UPLOAD_APPROVED_PLANS "
                "only for an approved migration plan."
            )
        if confirmation != expected_confirmation:
            raise PermissionError("Catalog upload confirmation does not match the plan")
        return self._post(path, payload, mode="catalog_write")

    def _post(self, path: str, payload: dict[str, Any], *, mode: str) -> OzonCall:
        operation = self.schema.operation(path, "POST")
        validation_errors = self.schema.validate_request(path, payload)
        if validation_errors:
            raise ValueError(
                "Payload does not match captured Ozon OpenAPI: "
                + "; ".join(validation_errors[:20])
            )
        if mode == "read" and path not in READ_PATHS:
            raise PermissionError(path)
        if mode == "catalog_write" and path not in CATALOG_WRITE_PATHS:
            raise PermissionError(path)
        if mode == "commercial_write":
            raise PermissionError("Commercial Ozon writes are not implemented")

        client_id = os.getenv("OZON_CLIENT_ID", "").strip()
        api_key = os.getenv("OZON_API_KEY", "").strip()
        if not client_id:
            raise RuntimeError("Secret is not configured: OZON_CLIENT_ID")
        if not api_key:
            raise RuntimeError("Secret is not configured: OZON_API_KEY")

        request_body = canonical_json(payload)
        url = self.schema.server_url.rstrip("/") + path
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Client-Id": client_id,
            "Api-Key": api_key,
            "User-Agent": os.getenv(
                "OZON_USER_AGENT", "wb-assistant-ozon-migration/0.1"
            ),
        }
        retries = self.max_retries if mode == "read" else 0
        result: TransportResult | None = None
        error_text: str | None = None

        for attempt in range(retries + 1):
            try:
                result = self.transport(url, request_body, headers, self.timeout)
            except RuntimeError:
                if mode != "read" or attempt == retries:
                    raise
                time.sleep(min(2.0**attempt, 10.0))
                continue
            if 200 <= result.status < 300:
                break
            retryable = result.status in {429, 500, 502, 503, 504}
            if any(key.lower() == "item-retry-after" for key in result.headers):
                retryable = False
            if not retryable or attempt == retries:
                error_text = self._error_message(result)
                break
            delay = self._retry_delay(result.headers, attempt)
            time.sleep(delay)

        if result is None:
            raise AssertionError("Transport did not return a result")
        artifact = self.store.save(
            source="ozon_api",
            entity=operation.operation_id or path.strip("/").replace("/", "."),
            request_url=url,
            request_params={"account_id": self.account_id, "mode": mode},
            request_body=request_body,
            body=result.body,
            content_type=result.content_type,
            http_status=result.status,
            method_id=operation.operation_id,
            docs_url=f"https://docs.ozon.ru/api/seller/#operation/{operation.operation_id}",
            error=error_text,
        )
        try:
            response = json.loads(result.body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Ozon returned non-JSON response; archived as {artifact.run_id}"
            ) from exc
        if error_text:
            error = RuntimeError(
                f"Ozon HTTP {result.status}; archived as {artifact.run_id}: {error_text}"
            )
            setattr(error, "artifact", artifact)
            setattr(error, "response", response)
            raise error
        return OzonCall(
            operation_id=operation.operation_id,
            path=path,
            status=result.status,
            response=response,
            artifact=artifact,
        )

    @staticmethod
    def _retry_delay(headers: dict[str, str], attempt: int) -> float:
        normalized = {key.lower(): value for key, value in headers.items()}
        value = normalized.get("retry-after")
        if value and value.isdigit():
            return min(float(value), 30.0)
        # Item-Retry-After is documented in minutes and can be very large.
        # Do not block a CLI process for that period.
        if normalized.get("item-retry-after"):
            return min(2.0**attempt, 10.0)
        return min(2.0**attempt, 10.0)

    @staticmethod
    def _error_message(result: TransportResult) -> str:
        try:
            value = json.loads(result.body)
            if isinstance(value, dict):
                return str(
                    value.get("message")
                    or value.get("error")
                    or value.get("code")
                    or f"HTTP {result.status}"
                )[:1000]
        except json.JSONDecodeError:
            pass
        return f"HTTP {result.status}: {result.body[:500]!r}"

    @staticmethod
    def _urlopen_transport(
        url: str, body: bytes, headers: dict[str, str], timeout: float
    ) -> TransportResult:
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return TransportResult(
                    status=response.status,
                    content_type=response.headers.get_content_type(),
                    body=response.read(),
                    headers=dict(response.headers.items()),
                )
        except urllib.error.HTTPError as exc:
            return TransportResult(
                status=exc.code,
                content_type=exc.headers.get_content_type(),
                body=exc.read(),
                headers=dict(exc.headers.items()),
            )
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Ozon network error: {exc.reason}") from exc


def default_ozon_schema() -> OzonOpenApi:
    return OzonOpenApi(
        Path(
            os.getenv(
                "OZON_OPENAPI_FILE",
                "openapi/ozon/ozon-openapi-export-2026-07-30T11-06-35-360Z.json",
            )
        )
    )
