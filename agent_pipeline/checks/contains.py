#!/usr/bin/env python3
"""Generic pattern check — assert an artifact contains (or lacks) something.

The escape hatch that keeps most pipelines from needing custom Python. A
surprising share of real gates are "this file must mention at least three
sources" or "this file must not contain the word guarantee", and those should
not each cost a script.

    python3 checks/contains.py <file> --pattern 'https?://' --min 3
    python3 checks/contains.py <file> --pattern '(?i)\\bguarantee\\b' --max 0
    python3 checks/contains.py <file> --pattern '^## Thesis' --min 1 --multiline

Exit 0 = within bounds. Exit 1 = outside them, with the count and examples.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Assert a pattern's occurrence count.")
    ap.add_argument("path")
    ap.add_argument("--pattern", required=True, help="Python regex")
    ap.add_argument("--min", type=int, default=1, help="minimum matches (default: 1)")
    ap.add_argument("--max", type=int, default=None, help="maximum matches (default: unbounded)")
    ap.add_argument("--multiline", action="store_true", help="^ and $ match line boundaries")
    ap.add_argument("--label", help="human name for this check, used in output")
    args = ap.parse_args(argv)

    path = Path(args.path)
    if not path.is_file():
        print(f"no such file: {path}", file=sys.stderr)
        return 2

    try:
        flags = re.MULTILINE if args.multiline else 0
        pattern = re.compile(args.pattern, flags)
    except re.error as exc:
        print(f"bad --pattern {args.pattern!r}: {exc}", file=sys.stderr)
        return 2

    text = path.read_text(encoding="utf-8", errors="replace")
    matches = pattern.findall(text)
    count = len(matches)
    label = args.label or args.pattern

    if count < args.min:
        print(f"{path.name}: found {count} match(es) for {label}, need at least {args.min}")
        return 1
    if args.max is not None and count > args.max:
        print(f"{path.name}: found {count} match(es) for {label}, allowed at most {args.max}")
        for m in matches[: args.max + 3]:
            print(f"  · {str(m)[:100]}")
        return 1

    print(f"{path.name}: {count} match(es) for {label} — within bounds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
