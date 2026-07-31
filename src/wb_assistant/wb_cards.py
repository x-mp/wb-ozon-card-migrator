from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .catalog_executor import SafeCatalogExecutor
from .storage import Artifact, ArtifactStore


METHOD_ID = "content.cards.list"
DOCS_URL = "https://dev.wildberries.ru/docs/openapi/work-with-products"


@dataclass(frozen=True)
class WbCardCollection:
    nm_id: int
    cards: tuple[dict[str, Any], ...]
    artifacts: tuple[Artifact, ...]

    @property
    def exact_card(self) -> dict[str, Any] | None:
        for card in self.cards:
            if int(card.get("nmID") or 0) == self.nm_id:
                return card
        return None


class WbCardCollector:
    """Collect seller-owned WB cards through the official read-only POST method."""

    def __init__(
        self,
        *,
        executor: SafeCatalogExecutor,
        store: ArtifactStore,
        page_limit: int = 100,
    ) -> None:
        if not 1 <= page_limit <= 100:
            raise ValueError("page_limit must be between 1 and 100")
        self.executor = executor
        self.store = store
        self.page_limit = page_limit

    def collect_nm_id(self, nm_id: int) -> WbCardCollection:
        if nm_id <= 0:
            raise ValueError("nm_id must be positive")

        artifacts: list[Artifact] = []
        cards: list[dict[str, Any]] = []
        cursor: dict[str, Any] = {"limit": self.page_limit}
        seen_cursors: set[tuple[str, int]] = set()

        while True:
            body = {
                "settings": {
                    "sort": {"ascending": True},
                    "filter": {"withPhoto": -1, "textSearch": str(nm_id)},
                    "cursor": cursor,
                }
            }
            result = self.executor.execute(
                domain="products",
                http_method="POST",
                path="/content/v2/get/cards/list",
                query={"locale": "ru"},
                body=body,
            )
            request_body = json.dumps(
                body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            error = (
                None
                if result.decision.kind.value in {"success", "accepted", "duplicate"}
                else result.decision.message
            )
            artifact = self.store.save(
                source="seller_api",
                entity="product_cards",
                request_url=result.url,
                request_params={"locale": "ru", "nm_id": str(nm_id)},
                request_body=request_body,
                body=result.body,
                content_type=result.content_type,
                http_status=result.http_status,
                method_id=METHOD_ID,
                docs_url=DOCS_URL,
                error=error,
            )
            artifacts.append(artifact)
            if error:
                exc = RuntimeError(
                    f"WB card request failed; archived as {artifact.run_id}: {error}"
                )
                setattr(exc, "artifact", artifact)
                raise exc

            try:
                response = json.loads(result.body)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"WB returned non-JSON card data; archived as {artifact.run_id}"
                ) from exc
            page_cards = response.get("cards") or []
            if not isinstance(page_cards, list):
                raise RuntimeError(
                    f"WB card response has no cards array; archived as {artifact.run_id}"
                )
            cards.extend(card for card in page_cards if isinstance(card, dict))

            response_cursor = response.get("cursor") or {}
            total = int(response_cursor.get("total") or len(page_cards))
            if total < self.page_limit:
                break
            updated_at = str(response_cursor.get("updatedAt") or "")
            cursor_nm_id = int(response_cursor.get("nmID") or 0)
            cursor_key = (updated_at, cursor_nm_id)
            if not updated_at or not cursor_nm_id:
                raise RuntimeError(
                    f"WB pagination cursor is incomplete; archived as {artifact.run_id}"
                )
            if cursor_key in seen_cursors:
                raise RuntimeError(
                    f"WB pagination cursor repeated; archived as {artifact.run_id}"
                )
            seen_cursors.add(cursor_key)
            cursor = {
                "limit": self.page_limit,
                "updatedAt": updated_at,
                "nmID": cursor_nm_id,
            }

        return WbCardCollection(
            nm_id=nm_id,
            cards=tuple(cards),
            artifacts=tuple(artifacts),
        )
