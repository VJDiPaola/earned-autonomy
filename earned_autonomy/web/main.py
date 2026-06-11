"""FastAPI dashboard for Earned Autonomy — BrightLoop Support Ops."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

load_dotenv(".env")

PHOENIX_BASE_URL = os.environ.get("PHOENIX_BASE_URL", "http://localhost:6006").rstrip("/")
PHOENIX_PROJECT_NAME = os.environ.get("PHOENIX_PROJECT_NAME", "earned-autonomy")
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Job state: job -> {status, started_at, finished_at, output}
_JOBS: dict[str, dict[str, Any]] = {
    "seed":    {"status": "idle", "started_at": None, "finished_at": None, "output": ""},
    "day":     {"status": "idle", "started_at": None, "finished_at": None, "output": ""},
    "traps":   {"status": "idle", "started_at": None, "finished_at": None, "output": ""},
    "evals":   {"status": "idle", "started_at": None, "finished_at": None, "output": ""},
    "reflect": {"status": "idle", "started_at": None, "finished_at": None, "output": ""},
}

_JOB_COMMANDS: dict[str, list[str]] = {
    "seed":    ["uv", "run", "python", "-m", "earned_autonomy.seed.data"],
    "day":     ["uv", "run", "python", "-m", "earned_autonomy.seed.scenarios", "clean"],
    "traps":   ["uv", "run", "python", "-m", "earned_autonomy.seed.scenarios", "traps"],
    "evals":   ["uv", "run", "python", "-m", "earned_autonomy.evals.runner"],
    "reflect": ["uv", "run", "python", "-m", "earned_autonomy.reflection.run"],
}

app = FastAPI(title="Earned Autonomy Dashboard")

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


# ---------------------------------------------------------------------------
# Helper: run a subprocess job in the background
# ---------------------------------------------------------------------------

async def _run_job(job: str, cmd: list[str]) -> None:
    state = _JOBS[job]
    state["status"] = "running"
    state["started_at"] = datetime.now(timezone.utc).isoformat()
    state["output"] = ""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(REPO_ROOT),
        )
        stdout, _ = await proc.communicate()
        raw = stdout.decode(errors="replace")
        state["output"] = raw[:100_000]  # cap at 100 KB
        state["status"] = "done" if proc.returncode == 0 else "error"
    except Exception as exc:
        state["output"] = str(exc)
        state["status"] = "error"
    finally:
        state["finished_at"] = datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html")


@app.post("/api/chat")
async def chat(payload: dict) -> JSONResponse:
    message: str = payload.get("message", "")
    session_id: str | None = payload.get("session_id")
    scenario: str | None = payload.get("scenario")

    # Lazy import — teammate module may not exist yet
    try:
        from earned_autonomy.agent.runner import run_turn  # type: ignore
        result = await run_turn(message, session_id=session_id, scenario=scenario)
    except ImportError:
        result = {
            "reply": "[agent.runner not yet available]",
            "session_id": session_id or "stub",
            "trace_id": None,
        }
    except Exception as exc:
        result = {
            "reply": f"[error: {exc}]",
            "session_id": session_id or "stub",
            "trace_id": None,
        }

    phoenix_url = f"{PHOENIX_BASE_URL}/projects"
    return JSONResponse({
        "reply": result.get("reply", ""),
        "session_id": result.get("session_id"),
        "trace_id": result.get("trace_id"),
        "phoenix_url": phoenix_url,
    })


@app.post("/api/run/{job}")
async def run_job(job: str) -> JSONResponse:
    if job not in _JOBS:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job}")
    state = _JOBS[job]
    if state["status"] == "running":
        raise HTTPException(status_code=409, detail=f"Job '{job}' is already running")
    cmd = _JOB_COMMANDS[job]
    asyncio.create_task(_run_job(job, cmd))
    return JSONResponse({"status": "started"})


@app.get("/api/jobs")
async def get_jobs() -> JSONResponse:
    return JSONResponse(_JOBS)


@app.get("/api/state")
async def get_state() -> JSONResponse:
    from earned_autonomy import db, policy  # always available

    conn = db.get_conn()
    try:
        # --- Ledger with merged TIER_NAMES + PROMOTE_RULES ---
        ledger_rows = db.rows_to_dicts(
            conn.execute("SELECT * FROM ledger").fetchall()
        )
        # Ensure all action types are represented even if table is empty
        ledger_by_type: dict[str, dict] = {r["action_type"]: r for r in ledger_rows}
        for at in policy.ACTION_TYPES:
            if at not in ledger_by_type:
                ledger_by_type[at] = {
                    "action_type": at,
                    "tier": policy.SEED_TIERS.get(at, policy.T1_PROPOSE),
                    "pass_rate": None,
                    "sample_count": 0,
                    "last_change_at": None,
                    "last_change_reason": None,
                    "evidence_trace_ids": None,
                }
        enriched_ledger = []
        for at, row in ledger_by_type.items():
            tier = row["tier"]
            promote = policy.PROMOTE_RULES.get(tier)
            enriched_ledger.append({
                **row,
                "tier_name": policy.TIER_NAMES.get(tier, str(tier)),
                "promote_min_pass_rate": promote[0] if promote else None,
                "promote_min_samples": promote[1] if promote else None,
            })

        # --- Proposals: pending first, then last 10 resolved ---
        pending = db.rows_to_dicts(
            conn.execute(
                "SELECT * FROM proposals WHERE status = 'pending' ORDER BY created_at DESC"
            ).fetchall()
        )
        resolved = db.rows_to_dicts(
            conn.execute(
                "SELECT * FROM proposals WHERE status != 'pending'"
                " ORDER BY resolved_at DESC LIMIT 10"
            ).fetchall()
        )
        proposals = pending + resolved

        # --- Executions: last 15 ---
        executions = db.rows_to_dicts(
            conn.execute(
                "SELECT * FROM executions ORDER BY created_at DESC LIMIT 15"
            ).fetchall()
        )

        # --- Eval summary: per action_type+eval_name pass/total from latest run_id ---
        eval_summary: list[dict] = []
        latest_run = conn.execute(
            "SELECT run_id FROM eval_results ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if latest_run:
            run_id = latest_run["run_id"]
            rows = db.rows_to_dicts(
                conn.execute(
                    """
                    SELECT action_type, eval_name,
                           SUM(CASE WHEN label='pass' THEN 1 ELSE 0 END) AS passes,
                           COUNT(*) AS total
                    FROM eval_results
                    WHERE run_id = ?
                    GROUP BY action_type, eval_name
                    """,
                    (run_id,),
                ).fetchall()
            )
            eval_summary = rows

    finally:
        conn.close()

    return JSONResponse({
        "ledger": enriched_ledger,
        "proposals": proposals,
        "executions": executions,
        "eval_summary": eval_summary,
    })


@app.post("/api/proposals/{proposal_id}/approve")
async def approve_proposal(proposal_id: int) -> JSONResponse:
    from earned_autonomy import db
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Proposal not found")
        row_dict = dict(row)
    finally:
        conn.close()

    kind = row_dict["kind"]
    now = db.now_iso()

    if kind == "action":
        # Lazy import gate
        try:
            from earned_autonomy.agent.gate import execute_approved  # type: ignore
            result = execute_approved(proposal_id)
        except ImportError:
            # Gate not yet available — just mark approved
            conn2 = db.get_conn()
            conn2.execute(
                "UPDATE proposals SET status='approved', resolved_at=? WHERE id=?",
                (now, proposal_id),
            )
            conn2.commit()
            conn2.close()
            result = {"status": "approved", "detail": "gate module not available"}
        return JSONResponse(result)

    elif kind == "tier_change":
        payload = json.loads(row_dict.get("payload") or "{}")
        action_type = row_dict["action_type"]
        to_tier = payload.get("to_tier")
        rationale = payload.get("rationale", "")
        evidence_raw = row_dict.get("evidence") or "{}"
        evidence = json.loads(evidence_raw) if isinstance(evidence_raw, str) else evidence_raw
        trace_ids = evidence.get("trace_ids", []) if isinstance(evidence, dict) else []

        # Lazy import ledger
        try:
            from earned_autonomy.agent.ledger import set_tier  # type: ignore
            updated = set_tier(
                action_type,
                to_tier,
                f"human-approved: {rationale}",
                trace_ids,
            )
        except ImportError:
            updated = {}

        # Mark proposal approved
        conn3 = db.get_conn()
        conn3.execute(
            "UPDATE proposals SET status='approved', resolved_at=? WHERE id=?",
            (now, proposal_id),
        )
        conn3.commit()
        conn3.close()
        return JSONResponse({"status": "approved", "ledger_row": updated})

    else:
        raise HTTPException(status_code=400, detail=f"Unknown kind: {kind}")


@app.post("/api/proposals/{proposal_id}/reject")
async def reject_proposal(proposal_id: int) -> JSONResponse:
    from earned_autonomy import db
    now = db.now_iso()
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Proposal not found")
        conn.execute(
            "UPDATE proposals SET status='rejected', resolved_at=? WHERE id=?",
            (now, proposal_id),
        )
        conn.commit()
    finally:
        conn.close()
    return JSONResponse({"status": "rejected"})
