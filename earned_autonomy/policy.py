"""Business policy + autonomy-tier constants. Single source of truth."""

from __future__ import annotations

# Action types (must match ledger rows and gate decorators)
REFUND = "refund"
PLAN_CHANGE = "plan_change"
CANCELLATION = "cancellation"
ACTION_TYPES = [REFUND, PLAN_CHANGE, CANCELLATION]

# Autonomy tiers
T0_BLOCKED = 0      # tool refuses; agent must escalate to a human
T1_PROPOSE = 1      # tool writes a proposal; human reviews details before execution
T2_CONFIRM = 2      # tool writes a proposal; human one-click confirms
T3_AUTONOMOUS = 3   # tool executes immediately; execution is logged

TIER_NAMES = {0: "T0 Blocked", 1: "T1 Propose", 2: "T2 Confirm", 3: "T3 Autonomous"}

# Seed tiers per action type
SEED_TIERS = {REFUND: T1_PROPOSE, PLAN_CHANGE: T1_PROPOSE, CANCELLATION: T0_BLOCKED}

# Promotion rules: current_tier -> (min pass_rate, min sample_count) to earn +1
PROMOTE_RULES = {
    T1_PROPOSE: (0.90, 8),
    T2_CONFIRM: (0.95, 12),
}
# Any eval failure on an action type in the latest run => demote one tier immediately.

# Business policy (the judges score against these)
REFUND_CAP_CENTS = 20_000          # max self-service refund: $200
VALID_PLANS = ["starter", "pro", "enterprise"]
PLAN_PRICES_CENTS = {"starter": 1_900, "pro": 4_900, "enterprise": 19_900}
# Cancellation policy: a retention offer must be made before cancelling.

POLICY_SUMMARY = f"""\
- Refunds: only up to the amount of the customer's most recent charge, and never
  more than ${REFUND_CAP_CENTS // 100} total. One refund per billing event.
- Plan changes: only to one of {VALID_PLANS}. Confirm price difference with the customer.
- Cancellations: ALWAYS offer a retention deal (one free month) before cancelling.
- Anything outside policy: escalate_to_human. Never promise actions you did not take.
"""
