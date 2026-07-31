#!/usr/bin/env python3
import sys

from wb_assistant.ozon_cli import main


ALLOWED = {
    "schema-status",
    "auth-check",
    "categories-fetch",
    "attributes-fetch",
    "poll",
    "product-check",
}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ALLOWED:
        raise SystemExit(
            "Read-only commands: " + ", ".join(sorted(ALLOWED))
        )
    raise SystemExit(main(sys.argv[1:]))
