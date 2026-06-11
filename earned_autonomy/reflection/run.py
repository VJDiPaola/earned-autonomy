"""Run one reflection pass: ``python -m earned_autonomy.reflection.run``."""

from __future__ import annotations

import asyncio
import secrets

from dotenv import load_dotenv

from earned_autonomy.db import REPO_ROOT

load_dotenv(REPO_ROOT / ".env")

TASK = (
    "Run your reflection pass now over the latest eval run for this project. "
    "Apply demotions, file promotion proposals, and preserve every failure as a "
    "regression case in the 'regression-evals' Phoenix dataset."
)


async def run_reflection() -> str:
    from google.adk.runners import InMemoryRunner
    from google.genai import types
    from opentelemetry import trace

    from earned_autonomy.instrumentation import setup_tracing
    from earned_autonomy.reflection.agent import get_reflection_agent

    setup_tracing()
    agent = get_reflection_agent()
    runner = InMemoryRunner(agent=agent, app_name="earned_autonomy_reflection")
    session_id = secrets.token_hex(8)
    await runner.session_service.create_session(
        app_name="earned_autonomy_reflection", user_id="reflector", session_id=session_id
    )
    tracer = trace.get_tracer("earned_autonomy")
    final_text = ""
    with tracer.start_as_current_span("reflection_turn") as span:
        span.set_attribute("openinference.span.kind", "CHAIN")
        span.set_attribute("input.value", TASK)
        async for event in runner.run_async(
            user_id="reflector",
            session_id=session_id,
            new_message=types.Content(role="user", parts=[types.Part(text=TASK)]),
        ):
            if event.is_final_response() and event.content and event.content.parts:
                final_text = "".join(p.text or "" for p in event.content.parts)
        span.set_attribute("output.value", final_text)
    return final_text


def main() -> None:
    report = asyncio.run(run_reflection())
    print("\n=== REFLECTION REPORT ===\n")
    print(report)


if __name__ == "__main__":
    main()
