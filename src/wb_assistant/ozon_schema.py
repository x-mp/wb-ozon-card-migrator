from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


@dataclass(frozen=True)
class OzonOperation:
    path: str
    http_method: str
    operation_id: str
    summary: str
    tags: tuple[str, ...]
    request_schema_ref: str | None
    response_codes: tuple[str, ...]
    deprecated: bool


class OzonOpenApi:
    """Read and validate against a captured official Ozon OpenAPI document."""

    def __init__(self, export_path: Path) -> None:
        self.export_path = export_path
        raw = export_path.read_bytes()
        self.sha256 = hashlib.sha256(raw).hexdigest()
        document = json.loads(raw)
        specifications = document.get("specifications")
        if not isinstance(specifications, list) or len(specifications) != 1:
            raise ValueError("Expected one specification in the Ozon export bundle")
        spec = specifications[0].get("spec")
        if not isinstance(spec, dict) or not isinstance(spec.get("paths"), dict):
            raise ValueError("Ozon export does not contain an OpenAPI specification")
        self.spec = spec

    @property
    def version(self) -> str:
        return str((self.spec.get("info") or {}).get("version", ""))

    @property
    def server_url(self) -> str:
        servers = self.spec.get("servers") or []
        value = str(servers[0].get("url", "")) if servers else ""
        if value.startswith("//"):
            value = "https:" + value
        if value != "https://api-seller.ozon.ru":
            raise ValueError(f"Unexpected Ozon Seller API server: {value!r}")
        return value

    def operation(self, path: str, http_method: str = "POST") -> OzonOperation:
        verb = http_method.lower()
        operation = (self.spec.get("paths") or {}).get(path, {}).get(verb)
        if not isinstance(operation, dict):
            raise KeyError(f"Method is absent from captured Ozon schema: {http_method} {path}")
        request_schema = (
            ((operation.get("requestBody") or {}).get("content") or {})
            .get("application/json", {})
            .get("schema")
        )
        return OzonOperation(
            path=path,
            http_method=verb.upper(),
            operation_id=str(operation.get("operationId") or ""),
            summary=str(operation.get("summary") or ""),
            tags=tuple(str(tag) for tag in operation.get("tags") or []),
            request_schema_ref=(
                str(request_schema["$ref"])
                if isinstance(request_schema, dict) and request_schema.get("$ref")
                else None
            ),
            response_codes=tuple(str(code) for code in (operation.get("responses") or {})),
            deprecated=bool(operation.get("deprecated", False)),
        )

    def validate_request(
        self, path: str, payload: dict[str, Any], http_method: str = "POST"
    ) -> list[str]:
        operation = self.operation(path, http_method)
        if not operation.request_schema_ref:
            return []
        schema = self.resolve_ref(operation.request_schema_ref)
        errors: list[str] = []
        self._validate(schema, payload, "$", errors)
        return errors

    def resolve_ref(self, reference: str) -> dict[str, Any]:
        if not reference.startswith("#/"):
            raise ValueError(f"Only local schema references are supported: {reference}")
        current: Any = self.spec
        for part in reference[2:].split("/"):
            current = current[part.replace("~1", "/").replace("~0", "~")]
        if not isinstance(current, dict):
            raise ValueError(f"Schema reference is not an object: {reference}")
        return current

    def _validate(
        self, schema: dict[str, Any], value: Any, path: str, errors: list[str]
    ) -> None:
        if "$ref" in schema:
            self._validate(self.resolve_ref(str(schema["$ref"])), value, path, errors)
            return
        if "allOf" in schema:
            for child in schema["allOf"]:
                self._validate(child, value, path, errors)
        nullable = bool(schema.get("nullable"))
        if value is None and nullable:
            return
        expected = schema.get("type")
        if expected == "object":
            if not isinstance(value, dict):
                errors.append(f"{path}: expected object")
                return
            for name in schema.get("required") or []:
                if name not in value or value[name] is None or value[name] == "":
                    errors.append(f"{path}.{name}: required")
            properties = schema.get("properties") or {}
            for name, item in value.items():
                child = properties.get(name)
                if isinstance(child, dict):
                    self._validate(child, item, f"{path}.{name}", errors)
        elif expected == "array":
            if not isinstance(value, list):
                errors.append(f"{path}: expected array")
                return
            if schema.get("minItems") is not None and len(value) < schema["minItems"]:
                errors.append(f"{path}: fewer than {schema['minItems']} items")
            if schema.get("maxItems") is not None and len(value) > schema["maxItems"]:
                errors.append(f"{path}: more than {schema['maxItems']} items")
            child = schema.get("items")
            if isinstance(child, dict):
                for index, item in enumerate(value):
                    self._validate(child, item, f"{path}[{index}]", errors)
        elif expected == "string":
            if not isinstance(value, str):
                errors.append(f"{path}: expected string")
                return
            if schema.get("minLength") is not None and len(value) < schema["minLength"]:
                errors.append(f"{path}: shorter than {schema['minLength']}")
            if schema.get("maxLength") is not None and len(value) > schema["maxLength"]:
                errors.append(f"{path}: longer than {schema['maxLength']}")
        elif expected == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                errors.append(f"{path}: expected integer")
                return
        elif expected == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                errors.append(f"{path}: expected number")
                return
        elif expected == "boolean" and not isinstance(value, bool):
            errors.append(f"{path}: expected boolean")
            return
        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"{path}: value is outside enum")


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()
