from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ErrorDecision, classify_http_status
from .rate_limit import PersistentRateLimiter
from .semantics import SemanticsRegistry

DOMAIN_DOC_TITLES = {
    "general": "Общее (general)",
    "products": "Работа с товарами (items)",
    "orders_fbs": "Заказы FBS (order)",
    "orders_dbw": "Заказы DBW (ordersdbw)",
    "orders_dbs": "Заказы DBS (order)",
    "in_store_pickup": "Заказы Самовывоз (instorepickup)",
    "supplies_fbw": "Поставки FBW (ordersfbw)",
    "promotion": "Маркетинг и продвижение (promotion)",
    "communication": "Общение с покупателями (communication)",
    "tariffs": "Тарифы (rates)",
    "analytics": "Аналитика и данные (analytics)",
    "reports": "Отчёты (reports)",
    "finance_documents": "Документы и бухгалтерия (finances)",
}


@dataclass(frozen=True)
class CatalogExecutionResult:
    url: str
    http_status: int | None
    content_type: str
    body: bytes
    decision: ErrorDecision
    method_id: str
    domain: str
    module: str


class SafeCatalogExecutor:
    """Execute only methods classified as read/query/artifact."""

    def __init__(
        self,
        docs_catalog: Path = Path("config/docs_catalog.json"),
        semantics_root: Path = Path("config/semantics"),
        *,
        timeout: float = 30,
        max_retries: int = 3,
        rate_limiter: PersistentRateLimiter | None = None,
        account_id: str = "default",
    ) -> None:
        payload = json.loads(docs_catalog.read_text(encoding="utf-8"))
        self.methods = payload["methods"]
        self.semantics = SemanticsRegistry(semantics_root)
        self.timeout = timeout
        self.max_retries = max_retries
        self.rate_limiter = rate_limiter
        self.account_id = account_id

    def execute(
        self,
        *,
        domain: str,
        http_method: str,
        path: str,
        query: dict[str, str] | None = None,
        path_params: dict[str, str] | None = None,
        body: Any = None,
    ) -> CatalogExecutionResult:
        semantic = self.semantics.get(domain, http_method, path)
        if not semantic.safe_for_read_worker:
            raise PermissionError(
                f"{domain} {http_method} {path} is classified as "
                f"{semantic.semantics}; read worker refused it"
            )
        definition = self._find_definition(domain, http_method, path)
        urls = definition.get("production_urls") or []
        if not urls:
            raise ValueError(f"No production URL for {http_method} {path}")
        url = self._resolve_path(str(urls[0]), path_params or {})
        query_string = urllib.parse.urlencode(query or {})
        if query_string:
            url = f"{url}?{query_string}"

        encoded_body: bytes | None = None
        headers = {
            "Accept": "application/json, application/zip, application/pdf, */*",
            "User-Agent": os.getenv("WB_USER_AGENT", "wb-assistant/0.1"),
        }
        token = os.getenv("WB_API_TOKEN")
        if not token:
            raise RuntimeError("Secret is not configured: WB_API_TOKEN")
        scheme = os.getenv("WB_API_AUTH_SCHEME", "").strip()
        headers["Authorization"] = f"{scheme} {token}".strip()
        if body is not None:
            encoded_body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            max_bytes = int(os.getenv("WB_MAX_REQUEST_BYTES", str(10 * 1024 * 1024)))
            if len(encoded_body) > max_bytes:
                raise ValueError(
                    f"Request body is {len(encoded_body)} bytes; local limit is {max_bytes}"
                )
            headers["Content-Type"] = "application/json"

        method_upper = http_method.upper()
        request = urllib.request.Request(
            url, data=encoded_body, headers=headers, method=method_upper
        )
        method_id = f"{method_upper.lower()}-{path.strip('/').replace('/', '-')}"
        host = urllib.parse.urlparse(url).hostname or "unknown"
        for attempt in range(self.max_retries + 1):
            if self.rate_limiter:
                self.rate_limiter.acquire(
                    account_id=self.account_id,
                    host=host,
                    method_id=method_id,
                )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    status = response.status
                    content_type = response.headers.get_content_type()
                    payload_body = response.read()
                    return CatalogExecutionResult(
                        url=response.geturl(),
                        http_status=status,
                        content_type=content_type,
                        body=payload_body,
                        decision=classify_http_status(status),
                        method_id=method_id,
                        domain=domain,
                        module=semantic.module,
                    )
            except urllib.error.HTTPError as exc:
                response_body = exc.read()
                retry_after = _retry_after(exc.headers.get("Retry-After"))
                decision = classify_http_status(exc.code, retry_after)
                if self.rate_limiter:
                    self.rate_limiter.observe(
                        account_id=self.account_id,
                        host=host,
                        method_id=method_id,
                        http_status=exc.code,
                        retry_after_seconds=retry_after,
                    )
                if decision.retryable and attempt < self.max_retries:
                    delay = retry_after if retry_after is not None else min(2**attempt, 30)
                    time.sleep(delay)
                    continue
                return CatalogExecutionResult(
                    url=url,
                    http_status=exc.code,
                    content_type=exc.headers.get_content_type(),
                    body=response_body,
                    decision=decision,
                    method_id=method_id,
                    domain=domain,
                    module=semantic.module,
                )
            except urllib.error.URLError:
                if attempt < self.max_retries:
                    time.sleep(min(2**attempt, 30))
                    continue
                return CatalogExecutionResult(
                    url=url,
                    http_status=None,
                    content_type="application/octet-stream",
                    body=b"",
                    decision=classify_http_status(None),
                    method_id=method_id,
                    domain=domain,
                    module=semantic.module,
                )
        raise AssertionError("unreachable")

    def _find_definition(
        self, domain: str, http_method: str, path: str
    ) -> dict[str, Any]:
        expected_title = DOMAIN_DOC_TITLES[domain]
        for item in self.methods:
            if (
                item["docs_title"] == expected_title
                and item["http_method"] == http_method.upper()
                and item["path"] == path
            ):
                return item
        raise KeyError(f"Method is absent from docs catalog: {domain} {http_method} {path}")

    @staticmethod
    def _resolve_path(url: str, values: dict[str, str]) -> str:
        resolved = url
        for name, value in values.items():
            resolved = resolved.replace(
                "{" + name + "}", urllib.parse.quote(str(value), safe="")
            )
        if "{" in resolved or "}" in resolved:
            raise ValueError(f"Missing path parameter for URL: {resolved}")
        return resolved


def _retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return min(max(float(value), 0), 300)
    except ValueError:
        return None
