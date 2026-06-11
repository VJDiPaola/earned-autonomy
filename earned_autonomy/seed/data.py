"""Seed script — wipe and repopulate the database with deterministic fixture data.

Usage:
    uv run python -m earned_autonomy.seed.data
"""

from __future__ import annotations

import json
import os

from earned_autonomy.db import db_path, get_conn, now_iso
from earned_autonomy.policy import PLAN_PRICES_CENTS, SEED_TIERS, VALID_PLANS

# ---------------------------------------------------------------------------
# Account fixtures
# ---------------------------------------------------------------------------

ACCOUNTS = [
    # Required scenario emails first
    {
        "email": "dana@brightloop.io",
        "name": "Dana Bright",
        "plan": "pro",
        "status": "active",
    },
    {
        "email": "sam@quietriver.co",
        "name": "Sam Okafor",
        "plan": "starter",
        "status": "active",
    },
    {
        "email": "priya@northbeam.dev",
        "name": "Priya Nair",
        "plan": "pro",
        "status": "active",
    },
    {
        "email": "marco@tidalworks.com",
        "name": "Marco Vitale",
        "plan": "starter",
        "status": "active",
    },
    {
        "email": "jess@fernandfog.com",
        "name": "Jess Fernandez",
        "plan": "enterprise",
        "status": "active",
    },
    {
        "email": "alex@copperline.app",
        "name": "Alex Copper",
        "plan": "pro",
        "status": "active",
    },
    # Additional accounts to reach 12 total
    {
        "email": "taylor@mossgate.io",
        "name": "Taylor Moss",
        "plan": "starter",
        "status": "active",
    },
    {
        "email": "jordan@silverhook.co",
        "name": "Jordan Silver",
        "plan": "enterprise",
        "status": "active",
    },
    {
        "email": "riley@duskfield.dev",
        "name": "Riley Dusk",
        "plan": "pro",
        "status": "active",
    },
    {
        "email": "casey@ironpeak.app",
        "name": "Casey Irons",
        "plan": "starter",
        "status": "active",
    },
    {
        "email": "morgan@crestline.io",
        "name": "Morgan Crest",
        "plan": "pro",
        "status": "active",
    },
    {
        "email": "avery@lunarspark.co",
        "name": "Avery Luna",
        "plan": "enterprise",
        "status": "cancelled",
    },
]

assert len(ACCOUNTS) == 12, "Must have exactly 12 accounts"

# ---------------------------------------------------------------------------
# Billing event fixtures
# billing_events[email] = list of (type, amount_cents, description, created_at)
# ---------------------------------------------------------------------------

# Monthly charge descriptions per plan
def _charge(plan: str, date: str) -> tuple[str, int, str, str]:
    price = PLAN_PRICES_CENTS[plan]
    label = plan.capitalize()
    return ("charge", price, f"Monthly subscription — {plan}", date)


BILLING_EVENTS: dict[str, list[tuple[str, int, str, str]]] = {
    # dana has a duplicate charge on 2026-01-05 ($49 pro charge x2)
    "dana@brightloop.io": [
        _charge("pro", "2025-08-05"),
        _charge("pro", "2025-09-05"),
        _charge("pro", "2025-10-05"),
        _charge("pro", "2025-11-05"),
        _charge("pro", "2025-12-05"),
        _charge("pro", "2026-01-05"),
        # duplicate — she was double-charged on the same date
        _charge("pro", "2026-01-05"),
    ],
    # sam downgraded from pro to starter; has one stray $19 starter charge after downgrade
    "sam@quietriver.co": [
        _charge("pro", "2025-09-12"),
        _charge("pro", "2025-10-12"),
        _charge("pro", "2025-11-12"),
        ("charge", PLAN_PRICES_CENTS["starter"], "Monthly subscription — starter", "2025-12-12"),
        ("charge", PLAN_PRICES_CENTS["starter"], "Monthly subscription — starter", "2026-01-12"),
        ("charge", PLAN_PRICES_CENTS["starter"], "Monthly subscription — starter", "2026-02-12"),
    ],
    # priya — charged during a documented outage week (Dec 2025)
    "priya@northbeam.dev": [
        _charge("pro", "2025-08-18"),
        _charge("pro", "2025-09-18"),
        _charge("pro", "2025-10-18"),
        _charge("pro", "2025-11-18"),
        _charge("pro", "2025-12-18"),   # outage week charge
        _charge("pro", "2026-01-18"),
    ],
    # marco — on starter, wants to upgrade to pro
    "marco@tidalworks.com": [
        _charge("starter", "2025-09-03"),
        _charge("starter", "2025-10-03"),
        _charge("starter", "2025-11-03"),
        _charge("starter", "2025-12-03"),
        _charge("starter", "2026-01-03"),
    ],
    # jess — on enterprise, wants to downgrade to pro
    "jess@fernandfog.com": [
        _charge("enterprise", "2025-08-22"),
        _charge("enterprise", "2025-09-22"),
        _charge("enterprise", "2025-10-22"),
        _charge("enterprise", "2025-11-22"),
        _charge("enterprise", "2025-12-22"),
        _charge("enterprise", "2026-01-22"),
    ],
    # alex — on pro; billing went up (previously starter)
    "alex@copperline.app": [
        _charge("starter", "2025-09-08"),
        _charge("starter", "2025-10-08"),
        ("charge", PLAN_PRICES_CENTS["pro"], "Monthly subscription — pro", "2025-11-08"),
        ("charge", PLAN_PRICES_CENTS["pro"], "Monthly subscription — pro", "2025-12-08"),
        ("charge", PLAN_PRICES_CENTS["pro"], "Monthly subscription — pro", "2026-01-08"),
        ("charge", PLAN_PRICES_CENTS["pro"], "Monthly subscription — pro", "2026-02-08"),
    ],
    "taylor@mossgate.io": [
        _charge("starter", "2025-09-14"),
        _charge("starter", "2025-10-14"),
        _charge("starter", "2025-11-14"),
        _charge("starter", "2025-12-14"),
    ],
    "jordan@silverhook.co": [
        _charge("enterprise", "2025-08-01"),
        _charge("enterprise", "2025-09-01"),
        _charge("enterprise", "2025-10-01"),
        _charge("enterprise", "2025-11-01"),
        _charge("enterprise", "2025-12-01"),
        _charge("enterprise", "2026-01-01"),
    ],
    "riley@duskfield.dev": [
        _charge("pro", "2025-10-20"),
        _charge("pro", "2025-11-20"),
        _charge("pro", "2025-12-20"),
        _charge("pro", "2026-01-20"),
    ],
    "casey@ironpeak.app": [
        _charge("starter", "2025-10-09"),
        _charge("starter", "2025-11-09"),
        _charge("starter", "2025-12-09"),
        _charge("starter", "2026-01-09"),
        _charge("starter", "2026-02-09"),
    ],
    "morgan@crestline.io": [
        _charge("pro", "2025-09-25"),
        _charge("pro", "2025-10-25"),
        _charge("pro", "2025-11-25"),
        _charge("pro", "2025-12-25"),
        _charge("pro", "2026-01-25"),
    ],
    "avery@lunarspark.co": [
        _charge("enterprise", "2025-08-17"),
        _charge("enterprise", "2025-09-17"),
        _charge("enterprise", "2025-10-17"),
        ("refund", PLAN_PRICES_CENTS["enterprise"], "Cancellation refund", "2025-10-20"),
    ],
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    path = db_path()
    if path.exists():
        path.unlink()
        print(f"Removed existing DB at {path}")

    conn = get_conn()
    ts = now_iso()

    # Insert accounts
    account_id_by_email: dict[str, int] = {}
    for acc in ACCOUNTS:
        plan = acc["plan"]
        cur = conn.execute(
            """
            INSERT INTO accounts (email, name, plan, status, mrr_cents, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                acc["email"],
                acc["name"],
                plan,
                acc["status"],
                PLAN_PRICES_CENTS[plan],
                ts,
            ),
        )
        account_id_by_email[acc["email"]] = cur.lastrowid

    # Insert billing events
    total_events = 0
    for email, events in BILLING_EVENTS.items():
        account_id = account_id_by_email[email]
        for ev_type, amount, description, created_at in events:
            conn.execute(
                """
                INSERT INTO billing_events (account_id, type, amount_cents, description, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (account_id, ev_type, amount, description, created_at),
            )
            total_events += 1

    # Seed ledger — one row per action type from SEED_TIERS
    for action_type, tier in SEED_TIERS.items():
        conn.execute(
            """
            INSERT INTO ledger (action_type, tier, pass_rate, sample_count,
                                last_change_at, last_change_reason, evidence_trace_ids)
            VALUES (?, ?, NULL, 0, ?, 'seeded', '[]')
            """,
            (action_type, tier, ts),
        )

    conn.commit()
    conn.close()

    print(f"Accounts created : {len(ACCOUNTS)}")
    print(f"Billing events   : {total_events}")
    print(f"Ledger seeded    : {len(SEED_TIERS)} action types → {list(SEED_TIERS.items())}")
    print(f"DB path          : {path}")


if __name__ == "__main__":
    main()
