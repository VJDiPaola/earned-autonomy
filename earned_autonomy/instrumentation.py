"""Phoenix tracing: ``register(..., auto_instrument=True)`` per the ADK integration doc.

https://arize.com/docs/phoenix/integrations/python/google-adk/google-adk-tracing

Environment: ``PHOENIX_API_KEY``, ``PHOENIX_COLLECTOR_ENDPOINT``, optional ``PHOENIX_PROJECT_NAME``.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from phoenix.otel import register

_provider: Optional[Any] = None


def setup_tracing() -> Optional[Any]:
    """Returns the tracer provider when Phoenix auth is configured, else ``None``."""
    global _provider
    if _provider is not None:
        return _provider
    if not (os.environ.get("PHOENIX_API_KEY") or "").strip():
        return None
    _provider = register(
        project_name=os.environ.get("PHOENIX_PROJECT_NAME", "earned-autonomy"),
        batch=False,
        auto_instrument=True,
        verbose=False,
    )
    return _provider
