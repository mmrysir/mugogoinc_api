"""Upsert ``KEY=VALUE`` into an env file without shell-escaping pitfalls.

Usage::

    python3 scripts/upsert_env.py KEY VALUE .env

Secret values can contain characters that break ``sed`` (``|``, ``&``, ``\\``,
quotes, …).  Doing the rewrite in Python avoids all of that and never prints
the value.
"""

from __future__ import annotations

import sys
from pathlib import Path


def upsert(key: str, value: str, path: Path) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    prefix = f"{key}="
    out: list[str] = []
    found = False
    for line in lines:
        if line.startswith(prefix):
            out.append(f"{prefix}{value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{prefix}{value}")
    path.write_text("\n".join(out) + "\n")


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: upsert_env.py KEY VALUE FILE", file=sys.stderr)
        return 2
    upsert(argv[1], argv[2], Path(argv[3]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
