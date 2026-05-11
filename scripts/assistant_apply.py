#!/usr/bin/env python3
import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path("/app")
SNAP_ROOT = Path("/tmp/botctl/snapshots")
LAST_SNAPSHOT_FILE = Path("/tmp/botctl/last_snapshot.txt")

TRACKED_DIRS = [
    "app",
    "scripts",
    "tests",
]

TRACKED_FILES = [
    "botctl",
    "VERSION",
    "BUILD",
    "requirements.txt",
]


def run(cmd, *, input_text=None, check=True):
    print(f"\n$ {' '.join(cmd)}")
    p = subprocess.run(
        cmd,
        cwd=ROOT,
        input=input_text,
        text=True,
    )
    if check and p.returncode != 0:
        raise SystemExit(p.returncode)
    return p.returncode


def snapshot(name: str) -> Path:
    SNAP_ROOT.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)[:80] or "snapshot"
    dst = SNAP_ROOT / f"{ts}_{safe_name}"
    dst.mkdir(parents=True, exist_ok=True)

    for d in TRACKED_DIRS:
        src = ROOT / d
        if src.exists():
            shutil.copytree(src, dst / d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    for f in TRACKED_FILES:
        src = ROOT / f
        if src.exists():
            shutil.copy2(src, dst / f)

    LAST_SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_SNAPSHOT_FILE.write_text(str(dst), encoding="utf-8")

    print(f"SNAPSHOT: {dst}")
    return dst


def restore(path: Path):
    if not path.exists():
        raise SystemExit(f"Snapshot not found: {path}")

    for d in TRACKED_DIRS:
        target = ROOT / d
        src = path / d
        if src.exists():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(src, target)

    for f in TRACKED_FILES:
        target = ROOT / f
        src = path / f
        if src.exists():
            shutil.copy2(src, target)

    print(f"RESTORED: {path}")


def apply_patch(name: str):
    patch_code = sys.stdin.read()
    if not patch_code.strip():
        raise SystemExit("No patch code received on stdin.")

    snap = snapshot(name)

    patch_file = Path("/tmp/botctl/current_patch.py")
    patch_file.parent.mkdir(parents=True, exist_ok=True)
    patch_file.write_text(patch_code, encoding="utf-8")

    print(f"PATCH FILE: {patch_file}")

    try:
        run(["python3", str(patch_file)])
    except SystemExit as e:
        print("\nPATCH FAILED.")
        print(f"Rollback command: ./botctl restore {snap}")
        raise

    code = run(["./botctl", "report"], check=False)

    if code == 0:
        print("\nAPPLY OK")
        print(f"Snapshot before patch: {snap}")
        return

    print("\nAPPLY FAILED AFTER CHECKS")
    print(f"Snapshot before patch: {snap}")
    print(f"Rollback command: ./botctl restore {snap}")
    print("Failure log: /tmp/botctl/last_report.log")
    raise SystemExit(code)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_snap = sub.add_parser("snapshot")
    p_snap.add_argument("name", nargs="?", default="manual")

    p_restore = sub.add_parser("restore")
    p_restore.add_argument("path", nargs="?")

    p_apply = sub.add_parser("apply")
    p_apply.add_argument("name", nargs="?", default="assistant_patch")

    args = parser.parse_args()

    if args.cmd == "snapshot":
        snapshot(args.name)
        return

    if args.cmd == "restore":
        if args.path:
            path = Path(args.path)
        else:
            if not LAST_SNAPSHOT_FILE.exists():
                raise SystemExit("No last snapshot found.")
            path = Path(LAST_SNAPSHOT_FILE.read_text(encoding="utf-8").strip())
        restore(path)
        return

    if args.cmd == "apply":
        apply_patch(args.name)
        return


if __name__ == "__main__":
    main()
