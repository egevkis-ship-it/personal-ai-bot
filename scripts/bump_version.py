from __future__ import annotations

import re
import sys
from pathlib import Path


VERSION_FILE = Path("VERSION")


def parse_version(raw: str) -> tuple[int, int, int, str]:
    raw = raw.strip()
    m = re.match(r"^v(\d+)\.(\d+)\.(\d+)(-.+)?$", raw)
    if not m:
        raise SystemExit(f"Unsupported VERSION format: {raw}")

    major = int(m.group(1))
    minor = int(m.group(2))
    patch = int(m.group(3))
    suffix = m.group(4) or ""

    return major, minor, patch, suffix


def main() -> None:
    bump = sys.argv[1] if len(sys.argv) > 1 else "patch"

    raw = VERSION_FILE.read_text().strip()
    major, minor, patch, suffix = parse_version(raw)

    if bump == "major":
        major += 1
        minor = 0
        patch = 0
    elif bump == "minor":
        minor += 1
        patch = 0
    elif bump == "patch":
        patch += 1
    elif bump == "none":
        print(raw)
        return
    else:
        raise SystemExit("Usage: python3 scripts/bump_version.py [major|minor|patch|none]")

    new_version = f"v{major}.{minor}.{patch}{suffix}"
    VERSION_FILE.write_text(new_version + "\n")
    print(new_version)


if __name__ == "__main__":
    main()
