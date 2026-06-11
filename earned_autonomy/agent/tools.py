"""Business tools for the BrightLoop support agent.

All functions are plain Python with full type hints. They return JSON-serializable
dicts. Docstrings are what the model sees — keep them crisp and policy-aware.
Side-effecting tools are wrapped with @gated to enforce the autonomy tier.
"""

from __future__ import annotations

import json

from earned_autonomy import db, policy
from earned_autonomy.agent.gate import gated


# ---------------------------------------------------------------------------
# Read-only tools (no gate needed)
# ---------------------------------------------------------------------------


def lookup_account(email: str) -> dict:
    """Look up a BrightLoop customer account by email address.

    Returns the account row with current plan, status, and MRR. Does NOT include
    billing history (use get_billing_history for that). Returns {"status":"not_found"}
    if no account exists for the email.
    """
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT id, email, name, plan, status, mrr_cents, created_at"
            " FROM accounts WHERE email = ?",
            (email,),
        ).fetchone()
        if row is None:
            return {"status": "not_found"}
        return dict(row)
    finally:
        conn.close()


def get_billing_history(account_id: int) -> dict:
    """Retrieve the last 10 billing events for an account, newest first.

    Use this before any billing action to understand the customer's recent charges
    and verify the amount to refund. Returns a dict with key 'events' (list of rows).
    """
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT id, account_id, type, amount_cents, description, created_at"
            " FROM billing_events WHERE account_id = ?"
            " ORDER BY created_at DESC LIMIT 10",
            (account_id,),
        ).fetchall()
        return {"account_id": account_id, "events": db.rows_to_dicts(rows)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Side-effecting tools (gated)
# ---------------------------------------------------------------------------


def _issue_refund_impl(account_id: int, amount_cents: int, reason: str) -> dict:
    """Raw refund implementation — inserts a billing_events row of type 'refund'."""
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO billing_events (account_id, type, amount_cents, description, created_at)"
            " VALUES (?, 'refund', ?, ?, ?)",
            (account_id, -abs(amount_cents), reason, db.now_iso()),
        )
        conn.commit()
        return {"status": "ok", "refunded_cents": amount_cents}
    finally:
        conn.close()


@gated(policy.REFUND)
def issue_refund(account_id: int, amount_cents: int, reason: str) -> dict:
    """Issue a refund to a BrightLoop customer account.

    Policy: refund amount must not exceed the customer's most recent charge and
    never exceed $200 total. One refund per billing event. Requires account_id
    (from lookup_account), amount_cents (positive integer), and a reason string.

    This action is gated — it may return status 'proposed', 'awaiting_confirm',
    'executed', or 'blocked' depending on the current autonomy tier.
    """
    return _issue_refund_impl(account_id, amount_cents, reason)


def _change_plan_impl(account_id: int, new_plan: str) -> dict:
    """Raw plan-change implementation — updates accounts and logs a billing event."""
    if new_plan not in policy.VALID_PLANS:
        return {"status": "error", "detail": f"unknown plan '{new_plan}'. Valid plans: {policy.VALID_PLANS}"}
    new_mrr = policy.PLAN_PRICES_CENTS[new_plan]
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT plan, mrr_cents FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if row is None:
            return {"status": "error", "detail": "account not found"}
        old_plan = row["plan"]
        old_mrr = row["mrr_cents"]
        proration = new_mrr - old_mrr
        event_type = "charge" if proration >= 0 else "credit"
        description = f"Plan change: {old_plan} -> {new_plan} (proration {proration:+d} cents)"
        conn.execute(
            "UPDATE accounts SET plan = ?, mrr_cents = ? WHERE id = ?",
            (new_plan, new_mrr, account_id),
        )
        conn.execute(
            "INSERT INTO billing_events (account_id, type, amount_cents, description, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (account_id, event_type, proration, description, db.now_iso()),
        )
        conn.commit()
        return {
            "status": "ok",
            "old_plan": old_plan,
            "new_plan": new_plan,
            "old_mrr_cents": old_mrr,
            "new_mrr_cents": new_mrr,
        }
    finally:
        conn.close()


@gated(policy.PLAN_CHANGE)
def change_plan(account_id: int, new_plan: str) -> dict:
    """Change a customer's subscription plan.

    Policy: only to one of starter / pro / enterprise. Confirm the price difference
    with the customer before calling this. account_id from lookup_account, new_plan
    must be one of the valid plan names.

    This action is gated — may return 'proposed', 'awaiting_confirm', 'executed',
    or 'blocked'.
    """
    return _change_plan_impl(account_id, new_plan)


def _cancel_subscription_impl(account_id: int, reason: str) -> dict:
    """Raw cancellation implementation — sets accounts.status to 'canceled'."""
    conn = db.get_conn()
    try:
        conn.execute(
            "UPDATE accounts SET status = 'canceled' WHERE id = ?", (account_id,)
        )
        conn.commit()
        return {"status": "ok", "canceled": True, "reason": reason}
    finally:
        conn.close()


@gated(policy.CANCELLATION)
def cancel_subscription(account_id: int, reason: str) -> dict:
    """Cancel a customer's BrightLoop subscription.

    Policy: ALWAYS offer a retention deal (one free month) before calling this.
    account_id from lookup_account, reason is a brief description of why the
    customer wants to cancel.

    This action is gated — at T0 (default) it is blocked and must be escalated.
    At higher tiers may return 'proposed', 'awaiting_confirm', or 'executed'.
    """
    return _cancel_subscription_impl(account_id, reason)


def escalate_to_human(summary: str) -> dict:
    """Escalate this support case to a human agent.

    Use when the request is outside policy, the customer is unhappy with automated
    responses, the action is blocked by the risk gate, or you are uncertain about
    the right action. summary should briefly describe the situation.
    """
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO executions (action_type, payload, decision, tier, trace_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                "escalation",
                json.dumps({"summary": summary}),
                "executed",
                99,
                None,
                db.now_iso(),
            ),
        )
        conn.commit()
        return {"status": "escalated", "summary": summary}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Register raw impls for gated tools so execute_approved can call them.
# The @gated decorator already registers via RAW_IMPLS, but for tools whose
# inner impl is separate we patch the registry after the fact.
# ---------------------------------------------------------------------------
from earned_autonomy.agent.gate import RAW_IMPLS  # noqa: E402

RAW_IMPLS[policy.REFUND] = _issue_refund_impl
RAW_IMPLS[policy.PLAN_CHANGE] = _change_plan_impl
RAW_IMPLS[policy.CANCELLATION] = _cancel_subscription_impl
