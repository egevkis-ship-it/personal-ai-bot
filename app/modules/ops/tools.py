import json
import logging
import os
import re
from dataclasses import dataclass, field

from app.modules.ops.github_client import get_file, list_directory

logger = logging.getLogger(__name__)

# Claude tool definitions
TOOL_DEFINITIONS = [
    {
        "name": "read_file",
        "description": (
            "Read the contents of a file from the project repository. "
            "Use this to inspect existing code before making changes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to repo root, e.g. 'app/config.py' or 'requirements.txt'",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_directory",
        "description": "List files and directories at the given path in the repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path, e.g. 'app/modules' or '' for root",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Queue a file to be created or overwritten. "
            "The change is NOT applied immediately — it will be shown to the user for confirmation. "
            "Always read the file first if it exists."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to repo root"},
                "content": {"type": "string", "description": "Full file content"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "add_package",
        "description": (
            "Add a Python package to requirements.txt. "
            "Queued for confirmation, not applied immediately."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "package": {
                    "type": "string",
                    "description": "Package spec, e.g. 'apscheduler>=3.10' or 'httpx'",
                }
            },
            "required": ["package"],
        },
    },
    {
        "name": "create_migration",
        "description": (
            "Create a new numbered SQL migration file in app/db/migrations/. "
            "The file will be applied automatically on next startup. "
            "Queued for confirmation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Short description for filename, e.g. 'add_sleep_tracking'",
                },
                "sql": {"type": "string", "description": "SQL statements for the migration"},
            },
            "required": ["description", "sql"],
        },
    },
]


@dataclass
class ToolContext:
    """Mutable context passed through the agent tool loop."""
    pending_files: dict[str, str] = field(default_factory=dict)
    commit_message: str = "bot: self-improvement"
    _next_migration_num: int | None = None

    async def _get_next_migration_num(self) -> int:
        if self._next_migration_num is not None:
            return self._next_migration_num
        try:
            items = await list_directory("app/db/migrations")
            nums = []
            for item in items:
                m = re.search(r"/(\d{3})_", item)
                if m:
                    nums.append(int(m.group(1)))
            self._next_migration_num = (max(nums) + 1) if nums else 1
        except Exception:
            self._next_migration_num = 10
        return self._next_migration_num


async def execute_tool(name: str, inputs: dict, ctx: ToolContext) -> str:
    try:
        if name == "read_file":
            path = inputs["path"]
            # Check if we already have a pending version
            if path in ctx.pending_files:
                return f"[queued version]\n{ctx.pending_files[path]}"
            content, _ = await get_file(path)
            return content

        if name == "list_directory":
            items = await list_directory(inputs.get("path", ""))
            return "\n".join(items)

        if name == "write_file":
            path = inputs["path"]
            ctx.pending_files[path] = inputs["content"]
            return f"Queued: {path}"

        if name == "add_package":
            package = inputs["package"].strip()
            req_path = "requirements.txt"
            try:
                content, _ = await get_file(req_path)
            except Exception:
                content = ""
            # Avoid duplicates
            pkg_name = re.split(r"[>=<!]", package)[0].strip().lower()
            lines = content.splitlines()
            existing = any(re.split(r"[>=<!]", l)[0].strip().lower() == pkg_name for l in lines)
            if existing:
                return f"{package} already in requirements.txt"
            new_content = content.rstrip("\n") + f"\n{package}\n"
            ctx.pending_files[req_path] = new_content
            return f"Queued: add {package} to requirements.txt"

        if name == "create_migration":
            num = await ctx._get_next_migration_num()
            slug = re.sub(r"[^a-z0-9_]", "_", inputs["description"].lower())
            filename = f"{num:03d}_{slug}.sql"
            path = f"app/db/migrations/{filename}"
            ctx.pending_files[path] = inputs["sql"]
            ctx._next_migration_num = num + 1
            return f"Queued: {path}"

        return f"Unknown tool: {name}"

    except Exception as e:
        logger.exception(f"Tool {name} failed")
        return f"Error in {name}: {e}"
