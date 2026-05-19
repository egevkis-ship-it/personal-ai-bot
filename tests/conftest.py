"""
Shared pytest fixtures.

Strategy:
- Unit tests run without DB / LLM (mock everything).
- DB tests need POSTGRES_TEST_URL env var pointing to a clean test database.
- Regression tests mock the LLM client.
"""
import os
import sys

import pytest

# Ensure project root on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Sane defaults for env vars so app.config / app.ai don't blow up at import time
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("OPENAI_API_KEY", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost/0")


class MockClaudeResponse:
    def __init__(self, text: str):
        from types import SimpleNamespace
        self.content = [SimpleNamespace(text=text)]


class MockClaudeClient:
    """Drop-in replacement for AsyncAnthropic. Returns canned JSON by prompt-substring match."""

    def __init__(self, responses: dict[str, str] | None = None):
        self.responses = responses or {}
        self.calls: list[dict] = []

        class Messages:
            def __init__(self, parent):
                self.parent = parent

            async def create(self, *, model, max_tokens, system, messages, **kw):
                self.parent.calls.append({
                    "model": model,
                    "system": system,
                    "messages": messages,
                })
                user_text = ""
                if messages:
                    content = messages[-1].get("content", "")
                    user_text = content if isinstance(content, str) else str(content)

                # Match by substring
                for key, response in self.parent.responses.items():
                    if key in user_text:
                        return MockClaudeResponse(response)
                return MockClaudeResponse('{"intent": "unknown", "confidence": 0.0}')

        self.messages = Messages(self)


@pytest.fixture
def mock_claude():
    return MockClaudeClient()


@pytest.fixture
def mock_claude_factory():
    def make(responses):
        return MockClaudeClient(responses=responses)
    return make
