#!/usr/bin/env python3
"""Fail if an artifact still contains placeholder text.

This is the cheapest gate in the engine and it closes the most common hole: a
phase that "completed" because a file exists, where the file is a skeleton with
TODOs in it. Artifact-existence alone has never been evidence of work, and this
is the check that says so out loud.

    python3 checks/no_placeholders.py <file> [--allow TODO] [--extra FIXME]

Exit 0 = clean. Exit 1 = placeholders found, listed with line numbers.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_MARKERS = (
    "TODO", "TBD", "FIXME", "XXX", "PLACEHOLDER",
    "lorem ipsum", "<insert", "[insert", "your text here",
    "coming soon", "...tbc", "WIP:",
)


def scan(text: str, markers: tuple[str, ...]) -> list[tuple[int, str, str]]:
    hits: list[tuple[int, str, str]] = []
    patterns = [(m, re.compile(re.escape(m), re.IGNORECASE)) for m in markers]
    for lineno, line in enumerate(text.splitlines(), start=1):
        for marker, pattern in patterns:
            if pattern.search(line):
                hits.append((lineno, marker, line.strip()[:120]))
                break
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fail if placeholder text remains.")
    ap.add_argument("path")
    ap.add_argument("--allow", action="append", default=[],
                    help="marker to ignore (repeatable), e.g. --allow XXX")
    ap.add_argument("--extra", action="append", default=[],
                    help="additional marker to catch (repeatable)")
    args = ap.parse_args(argv)

    path = Path(args.path)
    if not path.is_file():
        print(f"no such file: {path}", file=sys.stderr)
        return 2

    allowed = {a.upper() for a in args.allow}
    markers = tuple(
        m for m in (*DEFAULT_MARKERS, *args.extra) if m.upper() not in allowed
    )

    hits = scan(path.read_text(encoding="utf-8", errors="replace"), markers)
    if not hits:
        print(f"clean — no placeholders in {path.name}")
        return 0

    print(f"{len(hits)} placeholder(s) still in {path.name}:")
    for lineno, marker, line in hits[:20]:
        print(f"  line {lineno}: {marker} — {line}")
    if len(hits) > 20:
        print(f"  … and {len(hits) - 20} more")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
