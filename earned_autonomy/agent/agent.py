"""BrightLoop support agent definition."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from earned_autonomy.db import REPO_ROOT
from earned_autonomy.instrumentation import setup_tracing

# Load env and tracing at import time (mirrors the starter agent.py pattern).
load_dotenv(REPO_ROOT / ".env")
setup_tracing()

from google.adk.agents import Agent  # noqa: E402
from google.adk.tools import FunctionTool  # noqa: E402

from earned_autonomy.agent.prompt import SUPPORT_AGENT_INSTRUCTION  # noqa: E402
from earned_autonomy.agent.tools import (  # noqa: E402
    cancel_subscription,
    change_plan,
    escalate_to_human,
    get_billing_history,
    issue_refund,
    lookup_account,
)

_model = os.environ.get("GEMINI_MODEL", "gemini-3.1-pro-preview")


def get_root_agent() -> Agent:
    """Build and return the support agent. Safe to call multiple times (cheap)."""
    return Agent(
        model=_model,
        name="support_agent",
        instruction=SUPPORT_AGENT_INSTRUCTION,
        tools=[
            FunctionTool(func=lookup_account),
            FunctionTool(func=get_billing_history),
            FunctionTool(func=issue_refund),
            FunctionTool(func=change_plan),
            FunctionTool(func=cancel_subscription),
            FunctionTool(func=escalate_to_human),
        ],
    )


# Module-level alias for importers that reference root_agent directly.
# Built lazily to avoid requiring GOOGLE_API_KEY at import time — but Agent
# construction in ADK 2.x does not validate the key until the first run, so
# this is safe.
try:
    root_agent = get_root_agent()
except Exception:  # pragma: no cover
    root_agent = None  # type: ignore[assignment]
