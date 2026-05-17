import logging
from dataclasses import dataclass

from anthropic import AsyncAnthropic
from anthropic.types import ToolUseBlock

from app.config import settings
from app.modules.ops.tools import TOOL_DEFINITIONS, ToolContext, execute_tool

logger = logging.getLogger(__name__)

_claude = AsyncAnthropic(api_key=settings.anthropic_api_key)

_SYSTEM = """\
Ты ops-агент персонального Telegram-бота Егора.
Ты можешь читать файлы проекта, устанавливать пакеты, создавать миграции БД и изменять код.

Проект — Python-бот на python-telegram-bot + SQLAlchemy async + Redis + PostgreSQL.
Структура: app/modules/{fitness,finance,tasks,nutrition,ops}/, app/db/migrations/, requirements.txt

Правила:
- Сначала прочти нужные файлы, потом делай изменения.
- write_file, add_package, create_migration — не применяются сразу, ставятся в очередь для подтверждения.
- Объясняй коротко что делаешь и зачем.
- Commit message пиши на английском, кратко.
- Если запрос непонятен или опасен — уточни или откажись.
"""


@dataclass
class AgentResult:
    message: str
    pending_files: dict[str, str]
    commit_message: str


async def run_agent(request: str) -> AgentResult:
    ctx = ToolContext()
    messages = [{"role": "user", "content": request}]
    commit_message = "bot: apply changes"

    for _ in range(10):  # max iterations
        response = await _claude.messages.create(
            model="claude-opus-4-7",
            max_tokens=4096,
            system=_SYSTEM,
            tools=TOOL_DEFINITIONS,
            messages=messages,
        )

        # Append assistant turn
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            text = next(
                (b.text for b in response.content if hasattr(b, "text")),
                "Готово.",
            )
            # Extract commit message from last tool context
            if ctx.pending_files:
                commit_message = _extract_commit_message(text)
            return AgentResult(
                message=text,
                pending_files=ctx.pending_files,
                commit_message=commit_message,
            )

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if isinstance(block, ToolUseBlock):
                    result = await execute_tool(block.name, block.input, ctx)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            messages.append({"role": "user", "content": tool_results})

    return AgentResult(
        message="Достигнут лимит итераций. Попробуй переформулировать задачу.",
        pending_files=ctx.pending_files,
        commit_message=commit_message,
    )


def _extract_commit_message(text: str) -> str:
    """Try to pull a short commit message from agent's response."""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("bot:") or line.startswith("feat:") or line.startswith("fix:"):
            return line[:72]
    # Fallback: first non-empty line, truncated
    first = next((l.strip() for l in text.splitlines() if l.strip()), "bot: apply changes")
    return f"bot: {first[:60]}"
