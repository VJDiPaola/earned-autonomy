"""SQLite access layer. One file DB (``app.db``), schema auto-applied on connect."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  plan TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  mrr_cents INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS billing_events (
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  type TEXT NOT NULL,              -- charge | refund | credit
  amount_cents INTEGER NOT NULL,
  description TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger (
  action_type TEXT PRIMARY KEY,
  tier INTEGER NOT NULL,
  pass_rate REAL,
  sample_count INTEGER NOT NULL DEFAULT 0,
  last_change_at TEXT,
  last_change_reason TEXT,
  evidence_trace_ids TEXT          -- JSON array of Phoenix trace ids
);
CREATE TABLE IF NOT EXISTS proposals (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,              -- 'action' | 'tier_change'
  action_type TEXT NOT NULL,
  payload TEXT NOT NULL,           -- JSON: tool args, or {from_tier,to_tier,rationale}
  status TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected | executed
  trace_id TEXT,
  evidence TEXT,                   -- JSON: {trace_ids:[], pass_rate, sample_count}
  created_at TEXT NOT NULL,
  resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS eval_results (
  id INTEGER PRIMARY KEY,
  run_id TEXT NOT NULL,
  span_id TEXT,
  trace_id TEXT,
  action_type TEXT NOT NULL,
  eval_name TEXT NOT NULL,
  label TEXT NOT NULL,             -- 'pass' | 'fail'
  explanation TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS executions (
  id INTEGER PRIMARY KEY,
  action_type TEXT NOT NULL,
  payload TEXT NOT NULL,
  decision TEXT NOT NULL,          -- blocked | proposed | confirm | executed
  tier INTEGER NOT NULL,
  trace_id TEXT,
  created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_path() -> Path:
    p = os.environ.get("APP_DB_PATH", "").strip()
    return Path(p) if p else REPO_ROOT / "app.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]


def to_json(obj) -> str:
    return json.dumps(obj, default=str)
