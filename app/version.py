from __future__ import annotations

import os
import subprocess
from pathlib import Path


def get_version() -> str:
    env_version = os.getenv("APP_VERSION")
    if env_version:
        return env_version.strip()

    version_file = Path(__file__).resolve().parent.parent / "VERSION"
    if version_file.exists():
        return version_file.read_text().strip()

    return "unknown"


def get_build_hash() -> str:
    env_build = os.getenv("APP_BUILD")
    if env_build:
        return env_build.strip()

    try:
        repo_root = Path(__file__).resolve().parent.parent
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


VERSION = get_version()
BUILD_HASH = get_build_hash()
