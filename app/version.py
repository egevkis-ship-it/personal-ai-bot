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
    # Prefer runtime/build env vars when available.
    # APP_BUILD can be set manually.
    # Other names cover common CI/CD and platform-provided commit vars.
    for key in [
        "APP_BUILD",
        "SOURCE_COMMIT",
        "GIT_COMMIT",
        "COMMIT_SHA",
        "GITHUB_SHA",
        "COOLIFY_COMMIT",
        "COOLIFY_GIT_COMMIT",
        "RAILWAY_GIT_COMMIT_SHA",
        "VERCEL_GIT_COMMIT_SHA",
    ]:
        value = os.getenv(key)
        if value:
            return value.strip()[:12]

    build_file = Path(__file__).resolve().parent.parent / "BUILD"
    if build_file.exists():
        value = build_file.read_text().strip()
        if value:
            return value

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
