#!/usr/bin/env python3
"""Fail if an artifact is too thin to be the work it claims to be.

The companion to ``no_placeholders``. Together they cover the two ways a stub
passes an existence check: a file full of TODOs, and a file with a heading and
nothing under it.

The threshold is deliberately a flag, not a constant. A hook is eight words and
an article is two thousand; the engine has no business having an opinion about
which yours is. Set it per phase.

    python3 checks/min_substance.py <file> --min-words 40 [--min-lines 3]

Exit 0 = substantial enough. Exit 1 = too thin, with the actual counts.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

WORD = re.compile(r"[^\s]+")
# Front-matter, fence markers and bare headings are scaffolding, not substance.
SCAFFOLD = re.compile(r"^\s*(#{1,6}\s|```|---\s*$|\|[-:\s|]+\|\s*$)")


def measure(text: str) -> tuple[int, int]:
    body: list[str] = []
    in_frontmatter = False
    for i, line in enumerate(text.splitlines()):
        if i == 0 and line.strip() == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if line.strip() == "---":
                in_frontmatter = False
            continue
        if not line.strip() or SCAFFOLD.match(line):
            continue
        body.append(line)
    words = sum(len(WORD.findall(line)) for line in body)
    return words, len(body)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fail if an artifact is too thin.")
    ap.add_argument("path")
    ap.add_argument("--min-words", type=int, default=40)
    ap.add_argument("--min-lines", type=int, default=1)
    args = ap.parse_args(argv)

    path = Path(args.path)
    if not path.is_file():
        print(f"no such file: {path}", file=sys.stderr)
        return 2

    words, lines = measure(path.read_text(encoding="utf-8", errors="replace"))
    if words >= args.min_words and lines >= args.min_lines:
        print(f"{path.name}: {words} words across {lines} substantive lines")
        return 0

    print(
        f"{path.name} is too thin: {words} words / {lines} substantive lines "
        f"(need {args.min_words} / {args.min_lines}). "
        f"Headings, fences and front-matter don't count."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
