#!/usr/bin/env python3
import sys

from wb_assistant.ozon_cli import main


ALLOWED = {
    "extract-wb",
    "categories-suggest",
    "media-prepare",
    "media-upload-autosupply",
    "prepare",
    "approve",
}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ALLOWED:
        raise SystemExit(
            "Local preparation commands: " + ", ".join(sorted(ALLOWED))
        )
    raise SystemExit(main(sys.argv[1:]))
