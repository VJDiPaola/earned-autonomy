"""CLI entry point: run one support-agent turn from the command line."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

from earned_autonomy.agent.runner import run_turn  # noqa: E402


def main() -> None:
    msg = (
        sys.argv[1]
        if len(sys.argv) > 1
        else (
            "Hi, I'm dana@brightloop.io. I was charged twice this month — "
            "can you refund the duplicate $49 charge?"
        )
    )
    result = asyncio.run(run_turn(msg))
    print("=== Agent Reply ===")
    print(result["reply"])
    print()
    print(f"session_id : {result['session_id']}")
    print(f"trace_id   : {result['trace_id']}")


if __name__ == "__main__":
    main()
