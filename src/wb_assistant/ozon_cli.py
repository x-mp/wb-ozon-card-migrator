from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .env import load_dotenv
from .ozon_api import OzonSellerClient, default_ozon_schema, role_methods
from .ozon_migration import (
    approve_plan,
    build_migration_plan,
    extract_wb_products,
    flatten_ozon_categories,
    merge_canonical_products,
    save_plan,
    suggest_ozon_categories,
    verify_approval,
)
from .ozon_media import OzonMediaPreparer
from .ozon_schema import canonical_json
from .storage import ArtifactStore


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _write_new(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        output.write(canonical_json(value) + b"\n")


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _data_root() -> Path:
    return Path(os.getenv("WB_DATA_DIR", "data"))


def _client() -> OzonSellerClient:
    return OzonSellerClient(
        schema=default_ozon_schema(),
        store=ArtifactStore(_data_root()),
        account_id=os.getenv("OZON_ACCOUNT_ID", "main"),
        timeout=float(os.getenv("OZON_HTTP_TIMEOUT_SECONDS", "30")),
        max_retries=int(os.getenv("OZON_HTTP_MAX_RETRIES", "3")),
    )


def _products_from_file(path: Path) -> list[dict[str, Any]]:
    value = _json(path)
    if isinstance(value.get("products"), list):
        return [item for item in value["products"] if isinstance(item, dict)]
    if value.get("product_uid"):
        return [value]
    raise ValueError("Canonical file must contain a product or a products array")


def _select_product(path: Path, nm_id: int | None) -> dict[str, Any]:
    products = _products_from_file(path)
    if nm_id is not None:
        products = [
            product
            for product in products
            if int(product.get("source", {}).get("external_id") or 0) == nm_id
        ]
    if len(products) != 1:
        raise ValueError(
            f"Select exactly one canonical product; matched {len(products)}"
        )
    return products[0]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ozon-migrate",
        description="Safe WB to Ozon catalog migration pipeline",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("schema-status", help="Inspect captured official Ozon schema")
    commands.add_parser("auth-check", help="Read API-key roles without changing Ozon")

    categories = commands.add_parser(
        "categories-fetch", help="Download and archive the current Ozon category tree"
    )
    categories.add_argument("--output", type=Path)

    extract = commands.add_parser(
        "extract-wb", help="Extract canonical products from a WB API/capture JSON"
    )
    extract.add_argument("--input", type=Path, action="append", required=True)
    extract.add_argument("--nm-id", type=int, action="append")
    extract.add_argument("--output", type=Path, required=True)

    suggest = commands.add_parser(
        "categories-suggest", help="Rank Ozon category candidates for WB products"
    )
    suggest.add_argument("--canonical", type=Path, required=True)
    suggest.add_argument("--tree", type=Path, required=True)
    suggest.add_argument("--nm-id", type=int)
    suggest.add_argument("--limit", type=int, default=10)
    suggest.add_argument("--output", type=Path, required=True)

    media = commands.add_parser(
        "media-prepare",
        help="Download WB images and convert them to JPEG for public hosting",
    )
    media.add_argument("--canonical", type=Path, required=True)
    media.add_argument("--nm-id", type=int)
    media.add_argument("--public-base-url")
    media.add_argument("--jpeg-quality", type=int, default=92)

    attributes = commands.add_parser(
        "attributes-fetch",
        help="Download category attributes and all dictionary values",
    )
    attributes.add_argument("--category-id", type=int, required=True)
    attributes.add_argument("--type-id", type=int, required=True)
    attributes.add_argument("--output", type=Path)

    prepare = commands.add_parser(
        "prepare", help="Build a validated dry-run migration plan"
    )
    prepare.add_argument("--canonical", type=Path, required=True)
    prepare.add_argument("--nm-id", type=int)
    prepare.add_argument("--attributes", type=Path, required=True)
    prepare.add_argument("--category-id", type=int, required=True)
    prepare.add_argument("--type-id", type=int, required=True)
    prepare.add_argument("--type-name", required=True)
    prepare.add_argument("--media-map", type=Path)
    prepare.add_argument("--overrides", type=Path)
    prepare.add_argument("--source-account", default="main")
    prepare.add_argument("--target-account", default=os.getenv("OZON_ACCOUNT_ID", "main"))

    approve = commands.add_parser(
        "approve", help="Approve an error-free immutable migration plan"
    )
    approve.add_argument("--plan", type=Path, required=True)
    approve.add_argument("--confirm", required=True)

    upload = commands.add_parser(
        "upload", help="Upload one explicitly approved catalog plan"
    )
    upload.add_argument("--plan", type=Path, required=True)
    upload.add_argument("--approval", type=Path, required=True)
    upload.add_argument("--confirm", required=True)

    poll = commands.add_parser(
        "poll", help="Read the status of an Ozon product import task"
    )
    poll.add_argument("--task-id", type=int, required=True)

    product_check = commands.add_parser(
        "product-check",
        help="Read an Ozon product and its validation/moderation state by offer_id",
    )
    product_check.add_argument("--offer-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    try:
        if args.command == "schema-status":
            schema = default_ozon_schema()
            print(
                json.dumps(
                    {
                        "file": str(schema.export_path),
                        "sha256": schema.sha256,
                        "openapi": schema.spec.get("openapi"),
                        "document_version": schema.version,
                        "methods": sum(
                            1
                            for item in schema.spec["paths"].values()
                            for method in item
                            if method.lower()
                            in {
                                "get",
                                "post",
                                "put",
                                "patch",
                                "delete",
                                "head",
                                "options",
                                "trace",
                            }
                        ),
                        "server": schema.server_url,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        if args.command == "auth-check":
            call = _client().read("/v1/roles", {})
            methods = role_methods(call.response)
            print(
                json.dumps(
                    {
                        "run_id": call.artifact.run_id,
                        "status": call.status,
                        "roles": [
                            str(role.get("name") or "")
                            for role in call.response.get("roles") or []
                            if isinstance(role, dict)
                        ],
                        "method_count": len(methods),
                        "can_import_products": "/v3/product/import" in methods,
                        "expires_at": call.response.get("expires_at"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        if args.command == "categories-fetch":
            call = _client().read("/v1/description-category/tree", {"language": "RU"})
            output = args.output or (
                _data_root()
                / "marts"
                / "ozon_migration"
                / "discovery"
                / f"categories-{_timestamp()}.json"
            )
            value = {
                "schema_version": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source_run_id": call.artifact.run_id,
                "official_schema_sha256": default_ozon_schema().sha256,
                **call.response,
            }
            _write_new(output, value)
            print(
                json.dumps(
                    {
                        "run_id": call.artifact.run_id,
                        "output": str(output),
                        "leaf_types": len(flatten_ozon_categories(call.response)),
                    },
                    ensure_ascii=False,
                )
            )
            return 0

        if args.command == "extract-wb":
            selected_ids = set(args.nm_id or []) or None
            products = merge_canonical_products(
                [
                    extract_wb_products(
                        _json(input_path),
                        source_path=input_path,
                        nm_ids=None,
                    )
                    for input_path in args.input
                ],
                nm_ids=selected_ids,
            )
            output = {
                "schema_version": 1,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "products": products,
            }
            _write_new(args.output, output)
            print(
                json.dumps(
                    {
                        "output": str(args.output),
                        "products": len(products),
                        "nm_ids": [
                            product["source"]["external_id"] for product in products
                        ],
                    },
                    ensure_ascii=False,
                )
            )
            return 0

        if args.command == "categories-suggest":
            products = _products_from_file(args.canonical)
            if args.nm_id is not None:
                products = [
                    product
                    for product in products
                    if product["source"]["external_id"] == args.nm_id
                ]
            categories = flatten_ozon_categories(_json(args.tree))
            result = {
                "schema_version": 1,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "source_tree": str(args.tree),
                "suggestions": [
                    {
                        "product_uid": product["product_uid"],
                        "wb_nm_id": product["source"]["external_id"],
                        "source_category": product["content"]["source_category"],
                        "candidates": suggest_ozon_categories(
                            product, categories, limit=args.limit
                        ),
                    }
                    for product in products
                ],
            }
            _write_new(args.output, result)
            print(
                json.dumps(
                    {"output": str(args.output), "products": len(products)},
                    ensure_ascii=False,
                )
            )
            return 0

        if args.command == "media-prepare":
            if not 65 <= args.jpeg_quality <= 100:
                raise ValueError("--jpeg-quality must be between 65 and 100")
            product = _select_product(args.canonical, args.nm_id)
            result = OzonMediaPreparer(
                _data_root(),
                timeout=float(os.getenv("OZON_HTTP_TIMEOUT_SECONDS", "30")),
            ).prepare(
                product,
                public_base_url=args.public_base_url
                or os.getenv("OZON_MEDIA_BASE_URL"),
                jpeg_quality=args.jpeg_quality,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ready_for_ozon"] else 3

        if args.command == "attributes-fetch":
            client = _client()
            category_id = args.category_id
            type_id = args.type_id
            body = {
                "description_category_id": category_id,
                "type_id": type_id,
                "language": "RU",
            }
            call = client.read("/v1/description-category/attribute", body)
            attributes = call.response.get("result") or []
            dictionaries: dict[str, list[dict[str, Any]]] = {}
            source_runs = [call.artifact.run_id]
            for attribute in attributes:
                if not attribute.get("dictionary_id"):
                    continue
                attribute_id = int(attribute["id"])
                values: list[dict[str, Any]] = []
                last_value_id = 0
                seen_cursors: set[int] = set()
                while True:
                    payload = {
                        "attribute_id": attribute_id,
                        "description_category_id": category_id,
                        "type_id": type_id,
                        "language": "RU",
                        "limit": 2000,
                        "last_value_id": last_value_id,
                    }
                    page = client.read(
                        "/v1/description-category/attribute/values", payload
                    )
                    source_runs.append(page.artifact.run_id)
                    page_values = page.response.get("result") or []
                    values.extend(item for item in page_values if isinstance(item, dict))
                    if not page.response.get("has_next"):
                        break
                    if not page_values:
                        raise RuntimeError(
                            f"Empty dictionary page with has_next for {attribute_id}"
                        )
                    last_value_id = int(page_values[-1]["id"])
                    if last_value_id in seen_cursors:
                        raise RuntimeError(
                            f"Repeated dictionary cursor for {attribute_id}"
                        )
                    seen_cursors.add(last_value_id)
                dictionaries[str(attribute_id)] = values
            output = args.output or (
                _data_root()
                / "marts"
                / "ozon_migration"
                / "dictionaries"
                / f"{category_id}-{type_id}-{_timestamp()}.json"
            )
            snapshot = {
                "schema_version": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "official_schema_sha256": default_ozon_schema().sha256,
                "description_category_id": category_id,
                "type_id": type_id,
                "source_run_ids": source_runs,
                "attributes": attributes,
                "dictionaries": dictionaries,
            }
            _write_new(output, snapshot)
            print(
                json.dumps(
                    {
                        "output": str(output),
                        "attributes": len(attributes),
                        "dictionaries": len(dictionaries),
                        "raw_runs": len(source_runs),
                    },
                    ensure_ascii=False,
                )
            )
            return 0

        if args.command == "prepare":
            product = _select_product(args.canonical, args.nm_id)
            snapshot = _json(args.attributes)
            if int(snapshot.get("description_category_id") or 0) != args.category_id:
                raise ValueError("Attribute snapshot category does not match --category-id")
            if int(snapshot.get("type_id") or 0) != args.type_id:
                raise ValueError("Attribute snapshot type does not match --type-id")
            result = build_migration_plan(
                product=product,
                category_id=args.category_id,
                type_id=args.type_id,
                type_name=args.type_name,
                attribute_snapshot=snapshot,
                schema=default_ozon_schema(),
                seo_policy=_json(Path("config/ozon/seo-policy.json")),
                alias_config=_json(Path("config/ozon/attribute-aliases.json")),
                media_map=_json(args.media_map) if args.media_map else None,
                overrides=_json(args.overrides) if args.overrides else None,
                source_account_id=args.source_account,
                target_account_id=args.target_account,
            )
            path = save_plan(result, _data_root())
            item = result.plan["items"][0]
            print(
                json.dumps(
                    {
                        "plan": str(path),
                        "status": item["validation_status"],
                        "blocking_errors": item["blocking_errors"],
                        "warnings": item["warnings"],
                        "approval_confirmation": result.approval_confirmation,
                        "upload_confirmation": result.upload_confirmation,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0 if item["validation_status"] == "passed" else 3

        if args.command == "approve":
            path = approve_plan(args.plan, args.confirm)
            print(json.dumps({"approval": str(path)}, ensure_ascii=False))
            return 0

        if args.command == "upload":
            plan, expected = verify_approval(args.plan, args.approval)
            if default_ozon_schema().sha256 != plan["official_api_schema_hash"]:
                raise PermissionError(
                    "Official Ozon schema changed after plan creation; rebuild the plan"
                )
            submission_path = args.plan.with_name("submission.json")
            attempt_path = args.plan.with_name("upload-attempt.json")
            if submission_path.exists() or attempt_path.exists():
                raise FileExistsError(
                    "This plan already has an upload attempt. Reconcile it before "
                    "creating a new plan; automatic repeat is blocked."
                )
            client = _client()
            access = client.read("/v1/roles", {})
            if "/v3/product/import" not in role_methods(access.response):
                raise PermissionError(
                    "The configured Ozon key does not permit /v3/product/import"
                )
            offer_ids = [
                str(item.get("offer_id"))
                for item in plan["payload"].get("items") or []
                if item.get("offer_id")
            ]
            existing = client.read(
                "/v3/product/list",
                {
                    "filter": {"offer_id": offer_ids},
                    "last_id": "",
                    "limit": max(1, len(offer_ids)),
                },
            )
            _write_new(
                attempt_path,
                {
                    "schema_version": 1,
                    "plan_id": plan["plan_id"],
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "payload_hash": plan["payload_hash"],
                    "existing_product_check_run_id": existing.artifact.run_id,
                    "existing_products": existing.response,
                    "status": "started",
                },
            )
            call = client.upload_catalog(
                plan["payload"],
                confirmation=args.confirm,
                expected_confirmation=expected,
            )
            task_id = (call.response.get("result") or {}).get("task_id")
            submission = {
                "schema_version": 1,
                "plan_id": plan["plan_id"],
                "submitted_at": datetime.now(timezone.utc).isoformat(),
                "source_run_id": call.artifact.run_id,
                "task_id": task_id,
                "response": call.response,
            }
            _write_new(submission_path, submission)
            print(
                json.dumps(
                    {
                        "submission": str(submission_path),
                        "run_id": call.artifact.run_id,
                        "task_id": task_id,
                        "next": f"ozon-migrate poll --task-id {task_id}",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        if args.command == "poll":
            call = _client().read(
                "/v1/product/import/info", {"task_id": args.task_id}
            )
            items = (call.response.get("result") or {}).get("items") or []
            statuses = sorted({str(item.get("status")) for item in items})
            print(
                json.dumps(
                    {
                        "run_id": call.artifact.run_id,
                        "task_id": args.task_id,
                        "statuses": statuses,
                        "items": items,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2 if "failed" in statuses else 0

        if args.command == "product-check":
            call = _client().read(
                "/v3/product/info/list", {"offer_id": [args.offer_id]}
            )
            print(
                json.dumps(
                    {
                        "run_id": call.artifact.run_id,
                        "offer_id": args.offer_id,
                        "items": call.response.get("items")
                        or (call.response.get("result") or {}).get("items")
                        or [],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

    except (
        FileExistsError,
        FileNotFoundError,
        KeyError,
        ValueError,
        RuntimeError,
        PermissionError,
        OSError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
