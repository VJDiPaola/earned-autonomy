# Earned Autonomy

A support agent whose permissions are earned, not configured.

![Earned Autonomy dashboard — the agent files a promotion request with trace evidence](docs/dashboard.png)

*The moment the loop closes: the reflection agent has filed a T1→T2 promotion for refunds, citing a 100% pass rate across 12 eval samples — with Phoenix trace ids as clickable evidence. A human approves; the risk gate behaves differently on the very next tool call.*

---

## The Problem

Deploying an AI support agent means choosing between two extremes: route everything through a human (approval fatigue, no ROI) or trust the agent with everything (too risky on day one). Nobody ships the middle — where autonomy is a dial that moves based on demonstrated, measured competence. This project operationalizes that middle.

---

## How It Works

Every side-effecting action type starts at a low tier. The agent works tickets, gets evaluated by LLM-as-a-Judge, and a reflection agent reads those results via the Phoenix MCP server to promote or demote tiers — citing trace IDs as evidence. The loop closes: better behavior earns more autonomy; failures immediately cost a tier.

```mermaid
flowchart TD
    A[Customer message] --> B[Support Agent\ngemini-3.1-pro-preview · ADK]
    B -->|OTLP / OpenInference auto-instrumentation| C[Phoenix Cloud\ntraces · evals · datasets]
    B --> D{Risk Gate\nreads ledger}
    D -->|T0| E[Blocked — escalate to human]
    D -->|T1| F[Propose — human reviews details]
    D -->|T2| G[Confirm — one-click approval]
    D -->|T3| H[Execute autonomously]
    C --> I[Eval Runner\ngemini-3.5-flash judges\nresolution · tool accuracy · policy]
    I -->|per-action-type aggregates| J[Autonomy Ledger\nSQLite]
    J --> K[Reflection Agent\ngemini-3.1-pro-preview\nPhoenix MCP server via ADK McpToolset]
    K -->|pass-rate ≥ threshold| L[Promotion proposal\nhuman approves in queue]
    K -->|any failure| M[Instant one-tier demotion\napplied immediately]
    K -->|each failed trace| N[regression-evals dataset\nwritten to Phoenix via MCP]
    L --> J
    M --> J
    J --> D
```

---

## Built With

| Component | Detail |
|---|---|
| Google ADK (Python) | Agent runtime; `McpToolset` wires reflection agent to Phoenix MCP |
| Gemini 3.1 Pro (`gemini-3.1-pro-preview`) | Support agent + reflection agent |
| Gemini 3.5 Flash (`gemini-3.5-flash`) | LLM-as-a-Judge eval runner (resolution, tool accuracy, policy compliance) |
| Arize Phoenix Cloud | Tracing via `openinference` auto-instrumentation; eval storage; datasets |
| Phoenix MCP server | `npx @arizeai/phoenix-mcp@latest` over stdio; reflection agent queries and writes Phoenix at runtime |
| FastAPI + Jinja2 | Dashboard: chat pane, ledger tiers, approval queue |
| SQLite | Accounts, billing history, ledger, proposals — zero config |
| Cloud Run | `gcloud run deploy --source .` |

---

## Quickstart (for judges)

**Zero-setup path:** the hosted instance at **https://earned-autonomy-1083119471577.us-east1.run.app** runs the entire loop server-side (our keys, our Phoenix space) — open it and follow the on-screen DEMO FLOW. The steps below are for running it yourself.

**Prerequisites:** Python 3.10–3.12, `uv`, Node (for `npx`).

```bash
git clone https://github.com/<org>/earned-autonomy
cd earned-autonomy
uv sync
cp .env.example .env
# Fill in: GOOGLE_API_KEY, PHOENIX_API_KEY, PHOENIX_COLLECTOR_ENDPOINT
```

> Free-tier AI Studio keys cap Pro models at ~250 requests/day — one full demo cycle uses ~40.
> For sustained use, route through Vertex AI instead: set `GOOGLE_GENAI_USE_VERTEXAI=1`,
> `GOOGLE_CLOUD_PROJECT=<your-project>`, `GOOGLE_CLOUD_LOCATION=global` (see `.env.example`).

### Demo sequence — clean day then trap day

```bash
make seed          # reset app.db: 12 accounts, ledger seeded at T1/T0
make web           # launch dashboard at http://localhost:8080
```

In the dashboard:

1. **Run clean day** — replays 7 clean support scenarios (refunds within policy, plan changes, billing questions); traces appear in Phoenix.
2. **Run evals** — LLM-as-a-Judge scores the traces; pass-rate aggregates write to the ledger.
3. **Run reflection** — reflection agent queries Phoenix via MCP, finds refund/plan_change pass-rates above threshold, files promotion proposals.
4. **Approve the promotion** in the queue — ledger updates; tier badge changes.
5. **Chat a refund request** (`dana@brightloop.io`, "please refund my last payment") — watch it execute at T2 or T3 instead of proposing.

Then:

6. **Run trap day** — simulates the most common real-world regression: **version drift**. A "cost-cutting deploy" swaps the agent model to `gemini-3.5-flash` (override with `TRAP_MODEL`) — same prompt, same tools, weaker judgment — and replays 2 deliberately confusing tickets (e.g. a double-charged customer asking for *both* charges back, when one was legitimate service).
7. **Run evals** — the judges catch the policy-violating tool call on the trace.
8. **Run reflection** — the reflection agent applies an **instant self-demotion** (demotions don't wait for human approval) and writes every failed case to the `regression-evals` Phoenix dataset via MCP — the mistake becomes a permanent eval.

> **Why the demo is designed this way — and what we tried first:** (1) The agent's prompt embeds the v1 policy summary while the judges enforce the current v2 policy doc (`policy.py` documents the split) — recreating prompt/policy skew. (2) Trap day swaps the model — recreating model-version drift. We originally tried to bait `gemini-3.1-pro-preview` into policy violations with four generations of social-engineering traps; it escalated every single one. The realistic failure modes for well-aligned agents are environmental — stale prompts and model swaps — which is precisely what eval-gated autonomy is for.

### CLI equivalents

```bash
make seed          # (re)create app.db
make day           # 7 clean scripted scenarios
make traps         # 2 trap scenarios under model drift (TRAP_MODEL, default gemini-3.5-flash)
make evals         # run LLM-as-a-Judge evals → Phoenix + ledger
make reflect       # reflection agent: query via Phoenix MCP, file proposals/demotions
make web           # dashboard at http://localhost:8080
make run MESSAGE="I was double-charged — refund my last payment. My email is dana@brightloop.io"
```

---

## Autonomy Tiers

| Tier | Behavior |
|---|---|
| T0 Blocked | Tool refuses; agent must escalate to a human |
| T1 Propose | Tool writes a proposal; human reviews full details before execution |
| T2 Confirm | Tool writes a proposal; human one-click confirms |
| T3 Autonomous | Tool executes immediately; logged |

**Seed tiers:** `refund` = T1, `plan_change` = T1, `cancellation` = T0.

### Promotion rules (from `policy.py`)

| Transition | Minimum pass-rate | Minimum samples |
|---|---|---|
| T1 → T2 | 90% | 8 |
| T2 → T3 | 95% | 12 |

Any eval failure on an action type in the latest run triggers an **immediate one-tier demotion** — no human approval required. (Every conversation is scored by all three judges, so e.g. 4 refund conversations → 12 samples.)

---

## Rubric Mapping

| Judging criterion | Where it lives |
|---|---|
| Self-improvement loop | The promotion/demotion cycle is the product: evals → reflection agent → ledger → gate behavior changes |
| Meaningful MCP use | The reflection agent calls Phoenix MCP tools at runtime (list datasets, get eval results, add dataset examples) via ADK `McpToolset` over stdio |
| Meaningful tracing | Every tool call is an OpenInference span; the gate sets `gate.action`, `gate.tier`, `gate.decision` attributes on each span; eval labels are posted back to the same spans |

---

## Repo Layout

```
earned-autonomy/
├── LICENSE                          # Apache-2.0
├── Makefile                         # seed / day / traps / evals / reflect / web / run
├── .env.example
├── pyproject.toml                   # uv
├── run.py                           # single traced agent turn (CLI)
└── earned_autonomy/
    ├── policy.py                    # tier constants, promotion rules, business policy
    ├── db.py                        # SQLite helpers
    ├── instrumentation.py           # phoenix.otel.register(auto_instrument=True)
    ├── agent/
    │   ├── agent.py                 # ADK Agent definition + McpToolset wiring
    │   ├── gate.py                  # @gated decorator — T0/T1/T2/T3 dispatch + span attrs
    │   ├── ledger.py                # read/write autonomy ledger
    │   ├── tools.py                 # business tools (lookup, refund, plan_change, cancel)
    │   ├── prompt.py
    │   └── runner.py
    ├── evals/
    │   ├── runner.py                # LLM-as-a-Judge over Phoenix traces → ledger inputs
    │   └── templates.py             # resolution_correctness / tool_call_accuracy / policy_compliance
    ├── reflection/
    │   ├── agent.py                 # reflection ADK Agent + Phoenix MCP over stdio
    │   ├── tools.py                 # propose_tier_change / apply_demotion / add_regression_case_fallback
    │   └── run.py
    ├── seed/
    │   ├── data.py                  # 12 accounts, 3 plans, billing history
    │   └── scenarios.py             # 7 clean + 2 trap scripted scenarios
    └── web/
        ├── main.py                  # FastAPI: chat + ledger dashboard + approval queue
        └── templates/index.html
```

---

## Credits

Adapted from [Arize-ai/gemini-hackathon](https://github.com/Arize-ai/gemini-hackathon) starter (Apache-2.0).

License: Apache-2.0 — see [LICENSE](LICENSE).
