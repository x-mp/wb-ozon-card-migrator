#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from wb_assistant.catalog_executor import SafeCatalogExecutor
from wb_assistant.database import MetadataDatabase
from wb_assistant.env import load_dotenv
from wb_assistant.rate_limit import PersistentRateLimiter
from wb_assistant.storage import ArtifactStore
from wb_assistant.wb_cards import WbCardCollector


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Archive one seller-owned WB product card through the official API"
    )
    parser.add_argument("--nm-id", type=int, required=True)
    parser.add_argument("--account", default=os.getenv("WB_ACCOUNT_ID", "main"))
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    load_dotenv()
    args = build_parser().parse_args()
    data_root = Path(os.getenv("WB_DATA_DIR", "data"))
    database = MetadataDatabase(data_root / "catalog.sqlite")
    database.initialize()
    database.upsert_account(args.account)
    collector = WbCardCollector(
        executor=SafeCatalogExecutor(
            timeout=float(os.getenv("WB_HTTP_TIMEOUT_SECONDS", "30")),
            max_retries=int(os.getenv("WB_HTTP_MAX_RETRIES", "3")),
            rate_limiter=PersistentRateLimiter(database.path),
            account_id=args.account,
        ),
        store=ArtifactStore(data_root),
    )
    result = collector.collect_nm_id(args.nm_id)
    card = result.exact_card
    if card is None:
        print(
            json.dumps(
                {
                    "nm_id": args.nm_id,
                    "found": False,
                    "run_ids": [item.run_id for item in result.artifacts],
                },
                ensure_ascii=False,
            )
        )
        return 4
    if args.output:
        if args.output.exists():
            raise FileExistsError(f"Refusing to overwrite {args.output}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "source": "seller_api",
                    "source_run_ids": [item.run_id for item in result.artifacts],
                    "source_artifacts": [
                        {
                            "run_id": item.run_id,
                            "requested_at": item.requested_at,
                            "sha256": item.sha256,
                            "data_file": item.data_file,
                            "method_id": item.method_id,
                            "docs_url": item.docs_url,
                        }
                        for item in result.artifacts
                    ],
                    "cards": [card],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "nm_id": args.nm_id,
                "found": True,
                "run_ids": [item.run_id for item in result.artifacts],
                "output": str(args.output) if args.output else None,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
