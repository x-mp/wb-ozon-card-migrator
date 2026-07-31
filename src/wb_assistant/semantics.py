from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ALLOWED_SEMANTICS = {"read", "query", "job", "artifact", "mutate"}


@dataclass(frozen=True)
class SemanticMethod:
    domain: str
    http_method: str
    path: str
    semantics: str
    module: str

    @property
    def safe_for_read_worker(self) -> bool:
        return self.semantics in {"read", "query", "artifact"}


class SemanticsRegistry:
    def __init__(self, root: Path = Path("config/semantics")) -> None:
        methods: list[SemanticMethod] = []
        for path in sorted(root.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != 1:
                raise ValueError(f"Unsupported semantics schema: {path}")
            for item in payload.get("methods") or []:
                method = SemanticMethod(
                    domain=str(item["domain"]),
                    http_method=str(item["http_method"]).upper(),
                    path=str(item["path"]),
                    semantics=str(item["semantics"]),
                    module=str(item["module"]),
                )
                if method.semantics not in ALLOWED_SEMANTICS:
                    raise ValueError(
                        f"Unknown semantics {method.semantics!r} in {path}"
                    )
                methods.append(method)
        keys = [(item.domain, item.http_method, item.path) for item in methods]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate exact method in semantics registry")
        self._methods = sorted(
            methods, key=lambda item: (item.domain, item.path, item.http_method)
        )
        self._index = {
            (item.domain, item.http_method, item.path): item for item in self._methods
        }

    def all(
        self, *, domain: str | None = None, semantics: str | None = None
    ) -> list[SemanticMethod]:
        return [
            item
            for item in self._methods
            if (domain is None or item.domain == domain)
            and (semantics is None or item.semantics == semantics)
        ]

    def get(self, domain: str, http_method: str, path: str) -> SemanticMethod:
        try:
            return self._index[(domain, http_method.upper(), path)]
        except KeyError as exc:
            raise KeyError(f"Unclassified method: {domain} {http_method} {path}") from exc

