"""Earned Autonomy eval runner.

Usage:
    python -m earned_autonomy.evals.runner [max_age_minutes]

Pulls support_turn traces from Arize Phoenix, scores them with three
LLM-as-a-Judge evals (gemini), writes results to SQLite, logs labels back to
Phoenix, and updates the autonomy ledger.

Everything that touches the network/APIs is gated behind main() so that
`py_compile` and `import` succeed without credentials.
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv not installed — no-op
    def load_dotenv(*_a: object, **_kw: object) -> None:  # type: ignore[misc]
        pass


# ---------------------------------------------------------------------------
# Span-fetching helpers
# ---------------------------------------------------------------------------

def _safe_get(obj: Any, *keys: str, default: Any = None) -> Any:
    """Drill into a dict/pandas-Row with fallback at every level."""
    for k in keys:
        try:
            obj = obj[k]
            if obj is None:
                return default
        except (KeyError, TypeError, IndexError):
            return default
    return obj


def _attr(row: Any, *attr_path: str) -> Any:
    """Read a Phoenix span attribute column (handles both dot-key and nested)."""
    # Phoenix dataframes expose attributes as column names like
    # "attributes.input.value" — try that first, then fall back to nested dict.
    col = "attributes." + ".".join(attr_path)
    try:
        val = row[col]
        if val is not None:
            return val
    except (KeyError, TypeError):
        pass
    # Try nested dict under "attributes"
    try:
        d = row["attributes"]
        if isinstance(d, dict):
            return _safe_get(d, *attr_path)
    except (KeyError, TypeError):
        pass
    return None


def fetch_turns(max_age_minutes: int = 240) -> list[dict]:
    """Pull traces from Phoenix and return a list of turn dicts.

    Each turn dict has:
        trace_id, span_id, customer_message, final_reply,
        tool_calls: [{name, args, result_status, gate_decision}],
        scenario

    Grouping logic:
        - Group all spans by context.trace_id.
        - Root span = span whose name == "support_turn"; fallback: parent_id is
          null / NaN.
        - Tool spans = rows where span_kind == "TOOL" (auto-instrumented by
          openinference-instrumentation-google-adk).
    """
    import pandas as pd
    import phoenix as px

    client = px.Client()

    # Try modern API first; fall back to filter-string variant.
    try:
        df = client.get_spans_dataframe(project_name=os.environ.get(
            "PHOENIX_PROJECT_NAME", "earned-autonomy"))
    except TypeError:
        df = client.get_spans_dataframe(
            "span_kind == 'CHAIN'",
            project_name=os.environ.get("PHOENIX_PROJECT_NAME", "earned-autonomy"),
        )

    if df is None or len(df) == 0:
        print("[runner] No spans returned from Phoenix.")
        return []

    # ---- Filter to latest batch by start_time --------------------------------
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)

    # start_time column may be timezone-aware or naive; normalise.
    if "start_time" in df.columns:
        st = pd.to_datetime(df["start_time"], utc=True, errors="coerce")
        df = df[st >= cutoff].copy()

    if len(df) == 0:
        print(f"[runner] No spans younger than {max_age_minutes} min.")
        return []

    # ---- Resolve column names ------------------------------------------------
    # context.trace_id / context.span_id may be top-level or nested.
    turns: list[dict] = []

    # Determine trace-id column
    trace_col = next(
        (c for c in ("context.trace_id", "trace_id") if c in df.columns), None
    )
    span_col = next(
        (c for c in ("context.span_id", "span_id") if c in df.columns), None
    )
    parent_col = next(
        (c for c in ("parent_id", "attributes.parent_id") if c in df.columns), None
    )
    kind_col = next(
        (c for c in ("span_kind", "attributes.span_kind") if c in df.columns), None
    )

    if trace_col is None:
        print("[runner] Cannot find trace_id column in dataframe. Columns:", list(df.columns))
        return []

    for trace_id, trace_df in df.groupby(trace_col):
        # Find root span: prefer name == "support_turn", fallback null parent.
        root_mask = trace_df["name"] == "support_turn" if "name" in trace_df.columns else None
        if root_mask is not None and root_mask.any():
            root_row = trace_df[root_mask].iloc[0]
        elif parent_col is not None:
            null_parent = trace_df[parent_col].isna() | (trace_df[parent_col] == "")
            if null_parent.any():
                root_row = trace_df[null_parent].iloc[0]
            else:
                root_row = trace_df.iloc[0]
        else:
            root_row = trace_df.iloc[0]

        span_id = root_row[span_col] if span_col else str(trace_id)
        customer_message = str(_attr(root_row, "input", "value") or "")
        final_reply = str(_attr(root_row, "output", "value") or "")
        scenario = str(_attr(root_row, "scenario") or "")

        # Collect tool spans.
        tool_calls: list[dict] = []
        if kind_col is not None:
            tool_df = trace_df[trace_df[kind_col].str.upper() == "TOOL"]
        else:
            tool_df = trace_df.iloc[0:0]  # empty

        for _, trow in tool_df.iterrows():
            # Tool name: try attributes.tool.name, then span name.
            tool_name = (
                _attr(trow, "tool", "name")
                or (trow["name"] if "name" in trow.index else "unknown")
            )
            raw_args = _attr(trow, "input", "value") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except (json.JSONDecodeError, TypeError):
                args = {"raw": str(raw_args)}

            raw_result = _attr(trow, "output", "value") or "{}"
            try:
                result_obj = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
            except (json.JSONDecodeError, TypeError):
                result_obj = {"raw": str(raw_result)}

            # result_status: prefer explicit field, else infer from result dict.
            result_status = str(
                _attr(trow, "output", "status")
                or (result_obj.get("status") if isinstance(result_obj, dict) else None)
                or "unknown"
            )
            gate_decision = str(
                _attr(trow, "gate", "decision")
                or (result_obj.get("gate_decision") if isinstance(result_obj, dict) else None)
                or ""
            )

            tool_calls.append(
                {
                    "name": str(tool_name),
                    "args": args,
                    "result_status": result_status,
                    "gate_decision": gate_decision,
                }
            )

        turns.append(
            {
                "trace_id": str(trace_id),
                "span_id": str(span_id),
                "customer_message": customer_message,
                "final_reply": final_reply,
                "tool_calls": tool_calls,
                "scenario": scenario,
            }
        )

    print(f"[runner] Fetched {len(turns)} turn(s) from {len(df)} spans.")
    return turns


# ---------------------------------------------------------------------------
# Judge helpers
# ---------------------------------------------------------------------------

def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences that some models wrap JSON in."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_judge_response(text: str) -> dict:
    """Parse judge JSON; raise ValueError on failure."""
    cleaned = _strip_code_fences(text)
    data = json.loads(cleaned)
    if "label" not in data:
        raise ValueError(f"Missing 'label' key in judge response: {cleaned!r}")
    if data["label"] not in ("pass", "fail"):
        raise ValueError(f"Invalid label value: {data['label']!r}")
    return data


def call_judge(gclient: Any, model: str, prompt: str) -> dict:
    """Call the judge model; retry once on parse failure; fallback to fail."""
    config = {"response_mime_type": "application/json"}
    for attempt in range(2):
        try:
            resp = gclient.models.generate_content(
                model=model, contents=prompt, config=config
            )
            text = resp.text if hasattr(resp, "text") else str(resp)
            return _parse_judge_response(text)
        except Exception as exc:  # noqa: BLE001
            if attempt == 0:
                print(f"[judge] Parse/call error (attempt 1), retrying: {exc}")
            else:
                print(f"[judge] Parse/call error (attempt 2), labelling fail: {exc}")
                return {
                    "label": "fail",
                    "explanation": f"judge_error: {exc}",
                }
    # Should not reach here.
    return {"label": "fail", "explanation": "judge_error: exhausted retries"}


# ---------------------------------------------------------------------------
# Action-type mapping
# ---------------------------------------------------------------------------

_TOOL_TO_ACTION = {
    "issue_refund": "refund",
    "change_plan": "plan_change",
    "cancel_subscription": "cancellation",
}


def action_type_for_turn(turn: dict) -> str:
    """Map tool calls to an action_type string."""
    for tc in turn.get("tool_calls", []):
        action = _TOOL_TO_ACTION.get(tc.get("name", ""))
        if action:
            return action
    return "general"


# ---------------------------------------------------------------------------
# Aggregation & ledger update
# ---------------------------------------------------------------------------

def aggregate_and_update(
    results: list[dict],
    run_id: str,
) -> dict[str, dict]:
    """Aggregate pass rates per action_type and update the ledger.

    Only non-'general' action types feed the ledger.

    Aggregation is simple: this run's pass rate (passes / samples for this run).
    The ledger's sample_count is updated cumulatively: new = old + this_run_samples.
    (For the hackathon demo we keep pass_rate as this run's rate for simplicity;
    a production version would use an exponentially-weighted rolling average.)
    """
    from collections import defaultdict

    # Group by action_type
    buckets: dict[str, dict[str, list]] = defaultdict(lambda: {"pass": [], "fail": []})
    for r in results:
        at = r["action_type"]
        buckets[at][r["label"]].append(r)

    summary: dict[str, dict] = {}
    for at, counts in buckets.items():
        passes = len(counts["pass"])
        fails = len(counts["fail"])
        total = passes + fails
        rate = passes / total if total > 0 else 0.0
        summary[at] = {"pass": passes, "fail": fails, "total": total, "pass_rate": rate}

        if at == "general":
            continue

        # Lazy import so py_compile doesn't depend on ledger existing at import time.
        try:
            from earned_autonomy.agent.ledger import update_pass_stats  # noqa: PLC0415

            # Retrieve existing sample_count for cumulative total.
            from earned_autonomy import db  # noqa: PLC0415

            conn = db.get_conn()
            try:
                row = conn.execute(
                    "SELECT sample_count FROM ledger WHERE action_type = ?", (at,)
                ).fetchone()
                old_count = row["sample_count"] if row else 0
            finally:
                conn.close()

            new_count = old_count + total
            update_pass_stats(at, rate, new_count)
            print(f"[ledger] {at}: pass_rate={rate:.2%}, cumulative_samples={new_count}")
        except Exception as exc:  # noqa: BLE001
            print(f"[ledger] ERROR updating ledger for {at}: {exc}")

    return summary


# ---------------------------------------------------------------------------
# Phoenix eval logging
# ---------------------------------------------------------------------------

def log_to_phoenix(
    client: Any,
    eval_name: str,
    rows: list[dict],
) -> None:
    """Log eval labels back to Phoenix so they appear on trace spans."""
    try:
        import pandas as pd
        from phoenix.trace import SpanEvaluations  # type: ignore[import]

        span_ids = [r["span_id"] for r in rows]
        labels = [r["label"] for r in rows]
        explanations = [r.get("explanation", "") for r in rows]
        scores = [1 if lbl == "pass" else 0 for lbl in labels]

        eval_df = pd.DataFrame(
            {"label": labels, "explanation": explanations, "score": scores},
            index=pd.Index(span_ids, name="context.span_id"),
        )
        client.log_evaluations(SpanEvaluations(eval_name=eval_name, dataframe=eval_df))
        print(f"[phoenix] Logged {len(rows)} '{eval_name}' evaluations to Phoenix.")
    except Exception as exc:  # noqa: BLE001
        print(
            f"[phoenix] ERROR: Failed to log '{eval_name}' evaluations to Phoenix: {exc}. "
            "Local DB results are still saved."
        )


# ---------------------------------------------------------------------------
# DB persistence
# ---------------------------------------------------------------------------

def save_results(results: list[dict]) -> None:
    """Persist eval results to SQLite."""
    from earned_autonomy import db  # noqa: PLC0415

    conn = db.get_conn()
    try:
        for r in results:
            conn.execute(
                """
                INSERT INTO eval_results
                    (run_id, span_id, trace_id, action_type, eval_name, label, explanation, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    r["run_id"],
                    r.get("span_id"),
                    r.get("trace_id"),
                    r["action_type"],
                    r["eval_name"],
                    r["label"],
                    r.get("explanation", ""),
                    db.now_iso(),
                ),
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Pretty-print summary
# ---------------------------------------------------------------------------

def print_summary(
    summary: dict[str, dict],
    results: list[dict],
    eval_names: list[str],
) -> None:
    """Print a clean summary table + per-failure one-liners."""
    print("\n" + "=" * 70)
    print(f"{'ACTION TYPE':<20} {'EVAL':<30} {'PASS/TOTAL':>12}")
    print("-" * 70)

    from collections import defaultdict

    # Per (action_type, eval_name) counts
    cell: dict[tuple[str, str], dict] = defaultdict(lambda: {"pass": 0, "total": 0})
    for r in results:
        key = (r["action_type"], r["eval_name"])
        cell[key]["total"] += 1
        if r["label"] == "pass":
            cell[key]["pass"] += 1

    action_types = sorted(summary.keys())
    for at in action_types:
        for ev in eval_names:
            c = cell.get((at, ev), {"pass": 0, "total": 0})
            print(f"{at:<20} {ev:<30} {c['pass']:>5}/{c['total']:<6}")

    print("=" * 70)

    # Per-failure one-liners
    failures = [r for r in results if r["label"] == "fail"]
    if failures:
        print(f"\nFAILURES ({len(failures)}):")
        for r in failures:
            scenario = r.get("scenario", "")
            expl = r.get("explanation", "")[:120]
            print(f"  [{r['action_type']}] {r['eval_name']} | scenario={scenario!r} | {expl}")
    else:
        print("\nAll evals passed.")


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    load_dotenv(".env")

    max_age_minutes = 240
    if len(sys.argv) > 1:
        try:
            max_age_minutes = int(sys.argv[1])
        except ValueError:
            print(f"[runner] Warning: invalid max_age_minutes {sys.argv[1]!r}, using 240.")

    project_name = os.environ.get("PHOENIX_PROJECT_NAME", "earned-autonomy")
    judge_model = os.environ.get("JUDGE_MODEL", "gemini-3.5-flash")

    print(f"[runner] project={project_name}  judge={judge_model}  max_age={max_age_minutes}min")

    # ---- Fetch turns ---------------------------------------------------------
    turns = fetch_turns(max_age_minutes)
    if not turns:
        print("[runner] No turns to evaluate. Exiting.")
        sys.exit(0)

    # ---- Set up judge clients ------------------------------------------------
    from google import genai  # type: ignore[import]
    import phoenix as px

    gclient = genai.Client()
    px_client = px.Client()

    # ---- Import templates and policy -----------------------------------------
    from earned_autonomy.evals.templates import (
        POLICY_COMPLIANCE,
        RESOLUTION_CORRECTNESS,
        TOOL_CALL_ACCURACY,
    )
    from earned_autonomy.policy import POLICY_SUMMARY

    EVAL_TEMPLATES = {
        "RESOLUTION_CORRECTNESS": RESOLUTION_CORRECTNESS,
        "TOOL_CALL_ACCURACY": TOOL_CALL_ACCURACY,
        "POLICY_COMPLIANCE": POLICY_COMPLIANCE,
    }
    eval_names = list(EVAL_TEMPLATES.keys())

    run_id = uuid.uuid4().hex
    all_results: list[dict] = []

    # ---- Score every turn with every judge -----------------------------------
    for turn in turns:
        at = action_type_for_turn(turn)
        tool_calls_json = json.dumps(turn["tool_calls"], indent=2)

        for eval_name, template in EVAL_TEMPLATES.items():
            prompt = template.format(
                customer_message=turn["customer_message"],
                final_reply=turn["final_reply"],
                tool_calls=tool_calls_json,
                policy=POLICY_SUMMARY,
            )
            verdict = call_judge(gclient, judge_model, prompt)
            result = {
                "run_id": run_id,
                "span_id": turn["span_id"],
                "trace_id": turn["trace_id"],
                "action_type": at,
                "eval_name": eval_name,
                "label": verdict["label"],
                "explanation": verdict.get("explanation", ""),
                "scenario": turn.get("scenario", ""),
            }
            all_results.append(result)
            status_icon = "PASS" if verdict["label"] == "pass" else "FAIL"
            print(
                f"  [{status_icon}] trace={turn['trace_id'][:8]}  "
                f"at={at:<16} eval={eval_name}"
            )

    # ---- Persist to SQLite ---------------------------------------------------
    save_results(all_results)
    print(f"[runner] Saved {len(all_results)} eval result(s) to DB (run_id={run_id}).")

    # ---- Log back to Phoenix (one SpanEvaluations per judge) -----------------
    for ev_name in eval_names:
        ev_rows = [r for r in all_results if r["eval_name"] == ev_name]
        log_to_phoenix(px_client, ev_name, ev_rows)

    # ---- Aggregate and update ledger -----------------------------------------
    summary = aggregate_and_update(all_results, run_id)

    # ---- Print summary -------------------------------------------------------
    print_summary(summary, all_results, eval_names)


if __name__ == "__main__":
    main()
