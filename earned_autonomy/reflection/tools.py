"""Local function tools for the reflection agent (ledger introspection + tier changes)."""

from __future__ import annotations

import json

from earned_autonomy import db, policy


def get_ledger_and_evals() -> dict:
    """Returns the current autonomy ledger, promotion rules, and the latest eval run's
    results: per-action-type aggregates plus details for every failed eval
    (trace_id, eval name, judge explanation, customer message). Call this first."""
    conn = db.get_conn()
    ledger = db.rows_to_dicts(conn.execute("SELECT * FROM ledger").fetchall())

    run = conn.execute(
        "SELECT run_id FROM eval_results ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not run:
        return {"ledger": ledger, "latest_run": None,
                "note": "No eval results yet. Run evals first."}
    run_id = run["run_id"]

    rows = db.rows_to_dicts(conn.execute(
        "SELECT * FROM eval_results WHERE run_id = ?", (run_id,)
    ).fetchall())
    conn.close()

    aggregates: dict[str, dict] = {}
    failures = []
    non_ledger_failures = []
    for r in rows:
        at = r["action_type"]
        if at not in policy.ACTION_TYPES:
            # 'general' turns (no gated action) are evaluated but have no tier.
            if r["label"] != "pass":
                non_ledger_failures.append({
                    "action_type": at, "eval_name": r["eval_name"],
                    "explanation": r["explanation"], "trace_id": r["trace_id"],
                })
            continue
        agg = aggregates.setdefault(at, {"samples": 0, "passes": 0, "trace_ids": []})
        agg["samples"] += 1
        if r["label"] == "pass":
            agg["passes"] += 1
        if r["trace_id"] and r["trace_id"] not in agg["trace_ids"]:
            agg["trace_ids"].append(r["trace_id"])
        if r["label"] != "pass":
            failures.append({
                "action_type": at, "eval_name": r["eval_name"],
                "explanation": r["explanation"], "trace_id": r["trace_id"],
                "span_id": r["span_id"],
            })
    for at, agg in aggregates.items():
        agg["pass_rate"] = round(agg["passes"] / agg["samples"], 3) if agg["samples"] else None

    return {
        "ledger": ledger,
        "promotion_rules": {
            "T1_to_T2": {"min_pass_rate": policy.PROMOTE_RULES[1][0], "min_samples": policy.PROMOTE_RULES[1][1]},
            "T2_to_T3": {"min_pass_rate": policy.PROMOTE_RULES[2][0], "min_samples": policy.PROMOTE_RULES[2][1]},
        },
        "latest_run": {"run_id": run_id, "aggregates": aggregates, "failures": failures},
        "non_ledger_failures": non_ledger_failures,
        "note": ("non_ledger_failures come from turns with no gated action; they have "
                 "no autonomy tier — never propose/apply tier changes for them, but DO "
                 "preserve them as regression cases."),
        "tier_names": policy.TIER_NAMES,
    }


def propose_tier_change(action_type: str, to_tier: int, rationale: str,
                        evidence_trace_ids: list[str]) -> dict:
    """Files a PROMOTION proposal for human approval. action_type: refund|plan_change|cancellation.
    to_tier must be exactly one tier above the current tier. Cite the Phoenix trace ids
    that constitute the eval evidence and a one-paragraph rationale."""
    from earned_autonomy.agent import ledger as ledger_mod

    if action_type not in policy.ACTION_TYPES:
        return {"status": "error",
                "detail": f"'{action_type}' is not a gated action type ({policy.ACTION_TYPES})."}
    current = ledger_mod.get_tier(action_type)
    if to_tier != current + 1:
        return {"status": "error",
                "detail": f"{action_type} is at tier {current}; you may only propose tier {current + 1}."}
    conn = db.get_conn()
    cur = conn.execute(
        "INSERT INTO proposals (kind, action_type, payload, status, evidence, created_at) "
        "VALUES ('tier_change', ?, ?, 'pending', ?, ?)",
        (action_type,
         db.to_json({"action_type": action_type, "from_tier": current,
                     "to_tier": to_tier, "rationale": rationale}),
         db.to_json({"trace_ids": evidence_trace_ids}),
         db.now_iso()))
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return {"status": "proposed", "proposal_id": pid, "from_tier": current,
            "to_tier": to_tier, "detail": "Promotion filed — awaiting human approval."}


def apply_demotion(action_type: str, reason: str, evidence_trace_ids: list[str]) -> dict:
    """IMMEDIATELY demotes an action type by one tier (safety-first: demotions do not
    wait for human approval). Use when the latest eval run shows ANY failure for that
    action type. Cite the failing trace ids and the judge's reasoning."""
    from earned_autonomy.agent import ledger as ledger_mod

    if action_type not in policy.ACTION_TYPES:
        return {"status": "error",
                "detail": f"'{action_type}' is not a gated action type ({policy.ACTION_TYPES})."}
    current = ledger_mod.get_tier(action_type)
    if current <= 0:
        return {"status": "noop", "detail": f"{action_type} already at T0 (floor)."}
    new_tier = current - 1
    ledger_mod.set_tier(action_type, new_tier, f"self-demotion: {reason}", evidence_trace_ids)
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO proposals (kind, action_type, payload, status, evidence, created_at, resolved_at) "
        "VALUES ('tier_change', ?, ?, 'executed', ?, ?, ?)",
        (action_type,
         db.to_json({"action_type": action_type, "from_tier": current,
                     "to_tier": new_tier, "rationale": f"SELF-DEMOTION: {reason}"}),
         db.to_json({"trace_ids": evidence_trace_ids}),
         db.now_iso(), db.now_iso()))
    conn.commit()
    conn.close()
    return {"status": "demoted", "action_type": action_type,
            "from_tier": current, "to_tier": new_tier}


def add_regression_case_fallback(dataset_name: str, customer_message: str,
                                 expected_behavior: str, metadata_json: str) -> dict:
    """FALLBACK ONLY if the phoenix MCP dataset tools are unavailable or error:
    appends a failed case to a Phoenix dataset via the Phoenix REST client.
    metadata_json is a JSON object string with action_type, eval_name, trace_id."""
    from phoenix.client import Client

    try:
        meta = json.loads(metadata_json) if metadata_json else {}
    except json.JSONDecodeError:
        meta = {"raw": metadata_json}
    example = {
        "inputs": [{"customer_message": customer_message}],
        "outputs": [{"expected_behavior": expected_behavior}],
        "metadata": [meta],
    }
    try:
        client = Client()
        try:
            client.datasets.add_examples_to_dataset(dataset=dataset_name, **example)
        except Exception:
            client.datasets.create_dataset(name=dataset_name, **example)
        return {"status": "ok", "dataset": dataset_name}
    except Exception as exc:  # surface loudly to the agent
        return {"status": "error", "detail": f"{type(exc).__name__}: {exc}"}
