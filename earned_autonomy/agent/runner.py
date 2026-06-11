"""Async runner: one support-agent turn with Phoenix tracing."""

from __future__ import annotations

import secrets
from typing import Optional

from opentelemetry import trace

from earned_autonomy.instrumentation import setup_tracing


async def run_turn(
    user_text: str,
    session_id: Optional[str] = None,
    scenario: Optional[str] = None,
) -> dict:
    """Run one user turn through the support agent.

    Args:
        user_text: The customer's message.
        session_id: Reuse an existing session if provided; else a new one is created.
        scenario: Optional label attached to the root span (used by seed/eval scripts).

    Returns:
        {"reply": str, "session_id": str, "trace_id": str}
    """
    setup_tracing()

    # Import here to avoid triggering Agent construction before env is loaded.
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    from earned_autonomy.agent.agent import get_root_agent

    app_name = "earned_autonomy"
    user_id = "local_user"
    if session_id is None:
        session_id = secrets.token_hex(8)

    runner = InMemoryRunner(agent=get_root_agent(), app_name=app_name)
    await runner.session_service.create_session(
        app_name=app_name, user_id=user_id, session_id=session_id
    )

    tracer = trace.get_tracer("earned_autonomy")
    with tracer.start_as_current_span("support_turn") as span:
        span.set_attribute("openinference.span.kind", "CHAIN")
        span.set_attribute("input.value", user_text)
        span.set_attribute("scenario", scenario or "")

        final_text = ""
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=types.Content(
                role="user", parts=[types.Part(text=user_text)]
            ),
        ):
            # Collect text from the final model response event.
            if hasattr(event, "content") and event.content:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        final_text = part.text

        span.set_attribute("output.value", final_text)
        ctx = span.get_span_context()
        trace_id_hex = format(ctx.trace_id, "032x") if ctx.is_valid else "0" * 32

    return {
        "reply": final_text,
        "session_id": session_id,
        "trace_id": trace_id_hex,
    }
