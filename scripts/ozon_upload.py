#!/usr/bin/env python3
import sys

from wb_assistant.ozon_cli import main


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] != "upload":
        raise SystemExit("Write command: upload")
    raise SystemExit(main(sys.argv[1:]))

