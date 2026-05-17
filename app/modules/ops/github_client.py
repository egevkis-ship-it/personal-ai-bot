import base64
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_BASE = "https://api.github.com"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _repo() -> str:
    return settings.github_repo  # "owner/repo"


async def get_file(path: str) -> tuple[str, str]:
    """Returns (content, sha). Raises if not found."""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{_BASE}/repos/{_repo()}/contents/{path}",
            headers=_headers(),
        )
        r.raise_for_status()
        data = r.json()
        content = base64.b64decode(data["content"]).decode()
        return content, data["sha"]


async def list_directory(path: str = "") -> list[str]:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{_BASE}/repos/{_repo()}/contents/{path}",
            headers=_headers(),
        )
        r.raise_for_status()
        items = r.json()
        return [f"{item['type']}: {item['path']}" for item in items]


async def commit_files(files: dict[str, str], message: str) -> str:
    """Batch-commit multiple files in a single commit. Returns new commit SHA."""
    async with httpx.AsyncClient() as client:
        # 1. Get HEAD commit SHA
        r = await client.get(
            f"{_BASE}/repos/{_repo()}/git/ref/heads/main",
            headers=_headers(),
        )
        r.raise_for_status()
        head_sha = r.json()["object"]["sha"]

        # 2. Get base tree SHA
        r = await client.get(
            f"{_BASE}/repos/{_repo()}/git/commits/{head_sha}",
            headers=_headers(),
        )
        r.raise_for_status()
        base_tree_sha = r.json()["tree"]["sha"]

        # 3. Create blobs
        tree_items = []
        for path, content in files.items():
            r = await client.post(
                f"{_BASE}/repos/{_repo()}/git/blobs",
                headers=_headers(),
                json={"content": content, "encoding": "utf-8"},
            )
            r.raise_for_status()
            blob_sha = r.json()["sha"]
            tree_items.append({
                "path": path,
                "mode": "100644",
                "type": "blob",
                "sha": blob_sha,
            })

        # 4. Create tree
        r = await client.post(
            f"{_BASE}/repos/{_repo()}/git/trees",
            headers=_headers(),
            json={"base_tree": base_tree_sha, "tree": tree_items},
        )
        r.raise_for_status()
        new_tree_sha = r.json()["sha"]

        # 5. Create commit
        r = await client.post(
            f"{_BASE}/repos/{_repo()}/git/commits",
            headers=_headers(),
            json={
                "message": message,
                "tree": new_tree_sha,
                "parents": [head_sha],
            },
        )
        r.raise_for_status()
        new_commit_sha = r.json()["sha"]

        # 6. Update branch ref
        r = await client.patch(
            f"{_BASE}/repos/{_repo()}/git/refs/heads/main",
            headers=_headers(),
            json={"sha": new_commit_sha},
        )
        r.raise_for_status()

        logger.info(f"Committed {len(files)} file(s): {new_commit_sha[:7]}")
        return new_commit_sha
