"""Risk gate: @gated decorator + execute_approved helper."""

from __future__ import annotations

import functools
import json
from typing import Any, Callable

from opentelemetry import trace

from earned_autonomy import db, policy
from earned_autonomy.agent import ledger

# Registry of undecorated implementations, keyed by action_type.
RAW_IMPLS: dict[str, Callable[..., dict]] = {}


def _capture_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


def _set_span_attrs(action: str, tier: int, decision: str) -> None:
    span = trace.get_current_span()
    span.set_attribute("gate.action", action)
    span.set_attribute("gate.tier", tier)
    span.set_attribute("gate.decision", decision)


def gated(action_type: str) -> Callable:
    """Decorator factory that wraps a tool function with tier-based gate logic.

    The decorated function is registered in RAW_IMPLS[action_type] so that
    execute_approved can call the raw implementation after a proposal is approved.
    """

    def decorator(fn: Callable[..., dict]) -> Callable[..., dict]:
        # Store the raw undecorated impl.
        RAW_IMPLS[action_type] = fn

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> dict:
            tier = ledger.get_tier(action_type)
            trace_id = _capture_trace_id()

            # Build payload from kwargs (positional args not expected for tool calls).
            payload_dict = dict(kwargs)
            # If called with positional args, fold them in by fn signature.
            if args:
                import inspect
                sig = inspect.signature(fn)
                param_names = list(sig.parameters.keys())
                for i, val in enumerate(args):
                    if i < len(param_names):
                        payload_dict[param_names[i]] = val
            payload_json = json.dumps(payload_dict, default=str)

            conn = db.get_conn()

            if tier == policy.T0_BLOCKED:
                _set_span_attrs(action_type, tier, "blocked")
                conn.execute(
                    "INSERT INTO executions (action_type, payload, decision, tier, trace_id, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (action_type, payload_json, "blocked", tier, trace_id, db.now_iso()),
                )
                conn.commit()
                conn.close()
                return {
                    "status": "blocked",
                    "action_type": action_type,
                    "detail": "This action type is blocked at T0. Escalate to a human instead.",
                    "tier": 0,
                }

            elif tier == policy.T1_PROPOSE:
                _set_span_attrs(action_type, tier, "proposed")
                cur = conn.execute(
                    "INSERT INTO proposals (kind, action_type, payload, status, trace_id, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    ("action", action_type, payload_json, "pending", trace_id, db.now_iso()),
                )
                proposal_id = cur.lastrowid
                conn.execute(
                    "INSERT INTO executions (action_type, payload, decision, tier, trace_id, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (action_type, payload_json, "proposed", tier, trace_id, db.now_iso()),
                )
                conn.commit()
                conn.close()
                return {
                    "status": "proposed",
                    "proposal_id": proposal_id,
                    "detail": "Action proposed — a human will review the full details before it executes.",
                    "tier": 1,
                }

            elif tier == policy.T2_CONFIRM:
                _set_span_attrs(action_type, tier, "confirm")
                cur = conn.execute(
                    "INSERT INTO proposals (kind, action_type, payload, status, trace_id, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    ("action", action_type, payload_json, "pending", trace_id, db.now_iso()),
                )
                proposal_id = cur.lastrowid
                conn.execute(
                    "INSERT INTO executions (action_type, payload, decision, tier, trace_id, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (action_type, payload_json, "confirm", tier, trace_id, db.now_iso()),
                )
                conn.commit()
                conn.close()
                return {
                    "status": "awaiting_confirm",
                    "proposal_id": proposal_id,
                    "detail": "Action staged — awaiting one-click human confirmation.",
                    "tier": 2,
                }

            else:  # T3_AUTONOMOUS
                _set_span_attrs(action_type, tier, "executed")
                conn.close()
                result = fn(*args, **kwargs)
                # Re-open connection after fn executes (fn may use its own conn).
                conn2 = db.get_conn()
                conn2.execute(
                    "INSERT INTO executions (action_type, payload, decision, tier, trace_id, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (action_type, payload_json, "executed", tier, trace_id, db.now_iso()),
                )
                conn2.commit()
                conn2.close()
                result.update({"status": "executed", "tier": 3, "autonomous": True})
                return result

        return wrapper

    return decorator


def execute_approved(proposal_id: int) -> dict:
    """Load a pending/awaiting_confirm action proposal, execute its raw implementation,
    mark the proposal as executed, log in executions, and return the result.
    """
    conn = db.get_conn()
    row = conn.execute(
        "SELECT * FROM proposals WHERE id = ? AND kind = 'action' AND status IN ('pending', 'awaiting_confirm')",
        (proposal_id,),
    ).fetchone()
    if row is None:
        conn.close()
        return {"status": "error", "detail": f"No pending proposal with id={proposal_id}"}

    action_type = row["action_type"]
    kwargs = json.loads(row["payload"])
    trace_id = _capture_trace_id()
    tier = ledger.get_tier(action_type)

    raw_fn = RAW_IMPLS.get(action_type)
    if raw_fn is None:
        conn.close()
        return {"status": "error", "detail": f"No raw implementation registered for {action_type}"}

    now = db.now_iso()
    conn.execute(
        "UPDATE proposals SET status = 'executed', resolved_at = ? WHERE id = ?",
        (now, proposal_id),
    )
    conn.commit()
    conn.close()

    result = raw_fn(**kwargs)

    conn2 = db.get_conn()
    conn2.execute(
        "INSERT INTO executions (action_type, payload, decision, tier, trace_id, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (action_type, json.dumps(kwargs, default=str), "executed", tier, trace_id, now),
    )
    conn2.commit()
    conn2.close()

    return result
