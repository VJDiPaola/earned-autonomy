"""Autonomy ledger: per-action-type tier tracking backed by SQLite."""

from __future__ import annotations

import json
from typing import Optional

from earned_autonomy import db, policy


def get_ledger() -> list[dict]:
    """Return all ledger rows as a list of dicts."""
    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT * FROM ledger").fetchall()
        return db.rows_to_dicts(rows)
    finally:
        conn.close()


def get_tier(action_type: str) -> int:
    """Return the current tier for action_type. Falls back to SEED_TIERS if row missing."""
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT tier FROM ledger WHERE action_type = ?", (action_type,)
        ).fetchone()
        if row is None:
            return policy.SEED_TIERS.get(action_type, policy.T1_PROPOSE)
        return row["tier"]
    finally:
        conn.close()


def set_tier(
    action_type: str,
    tier: int,
    reason: str,
    evidence_trace_ids: list[str],
) -> dict:
    """Upsert ledger row for action_type with new tier, reason, and evidence trace ids.

    Returns the updated row as a dict.
    """
    conn = db.get_conn()
    try:
        now = db.now_iso()
        evidence_json = json.dumps(evidence_trace_ids)
        conn.execute(
            """
            INSERT INTO ledger (action_type, tier, last_change_at, last_change_reason, evidence_trace_ids)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(action_type) DO UPDATE SET
                tier = excluded.tier,
                last_change_at = excluded.last_change_at,
                last_change_reason = excluded.last_change_reason,
                evidence_trace_ids = excluded.evidence_trace_ids
            """,
            (action_type, tier, now, reason, evidence_json),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM ledger WHERE action_type = ?", (action_type,)
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


def update_pass_stats(
    action_type: str, pass_rate: float, sample_count: int
) -> None:
    """Update pass_rate and sample_count for the given action_type row.

    Creates the row with seed tier if it doesn't exist.
    """
    conn = db.get_conn()
    try:
        now = db.now_iso()
        seed_tier = policy.SEED_TIERS.get(action_type, policy.T1_PROPOSE)
        conn.execute(
            """
            INSERT INTO ledger (action_type, tier, pass_rate, sample_count)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(action_type) DO UPDATE SET
                pass_rate = excluded.pass_rate,
                sample_count = excluded.sample_count
            """,
            (action_type, seed_tier, pass_rate, sample_count),
        )
        conn.commit()
    finally:
        conn.close()
