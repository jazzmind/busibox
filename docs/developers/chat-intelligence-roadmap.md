---
title: "Chat Intelligence Roadmap — Implementation Report"
category: "developer"
order: 40
description: "Three implementation pathways for the chat agent: frontier-grade reasoning, Office document analysis and generation, and app-data connectors (Marine dashboard, fleet tracker, bid finder)"
published: true
---

# Chat Intelligence Roadmap — Implementation Report

Companion to `chat-qa-findings-2026-08.md`. That document says what is broken; this one
says what to build. Three pathways, each self-contained, each grounded in code that
already exists in this repo.

| Pathway | Plugs into | Effort | Value |
|---|---|---|---|
| 1. Frontier-grade reasoning | Agent pipeline (`chat_agent.py`, `base_agent.py`) | 2–3 weeks | Highest — what users feel every message |
| 2. Office documents in and out | Tools layer (`app/tools/`) + Data API | 1–2 weeks | High — most-requested missing feature |
| 3. App data connectors | Tools layer via MCP (`services/mcp_client.py`) | 3–5 days per app | High for operations staff |

Recommended order: 1 (repair items first) → 3 (fleet tracker) → 2 (Excel output first).
Underneath all three: health monitoring (Phase 0 in the QA doc) — the two-day
embedding-api outage proved the platform cannot currently tell when it is degraded.

---

## Pathway 1 — Frontier-grade reasoning

### Goal
Make a chat turn behave like Claude/ChatGPT: understand the question, plan real
multi-step work, retrieve well, answer helpfully even when documents are silent, never
show internals, and remember what it has been told.

### Current state (what the pipeline actually does today)
`ChatAgent.run_with_streaming` → `_route_intent()` (semantic router, then the 0.8B
fast-ack classifier) → `_generate_plan()` → `_execute_plan()` (parallel tool steps via
`BaseStreamingAgent._execute_step`) → `_synthesize()` (streams the answer from the
`chat` model).

Known defects, all with evidence in the QA doc:

- **Planner is non-functional in production** (finding #6). `ExecutionPlan` pydantic
  validation rejects the model's output when `estimated_duration` or `step.id` come back
  as ints; every plan falls back to the generic "document_search (+ web_search)" plan.
- **Retrieval is literal.** One query string, no rewriting; policy questions phrased
  differently from the handbook miss.
- **All-or-nothing grounding.** When documents don't answer, the model refuses rather
  than estimating with caveats — the largest felt gap in the ChatGPT side-by-sides.
- **Silent degradation.** Tool errors (search 500) produce a confident answer from
  memory; the fast model sometimes final-answers substantive questions; clarify loops.
- **Tool-syntax leakage** in synthesis (finding #8) — fixed on `feat/fastack-few-shot`
  by the synthesis guard; needs deploy + verification.
- **Memory is a static glossary** (`config/org_glossary.yaml`).

### Design

```
query ─► route ─► rewrite (NEW) ─► plan (FIXED) ─► execute ─► synthesize (GUARDED) ─► answer
              │                        │                          ▲
              └─ semantic router       └─ real multi-step plans   └─ grounding policy + tool-error honesty
                                                                     memory injection (glossary → org memory → user memory)
```

### Work items

**1.1 Planner repair** — `srv/agent/app/schemas/...` (`ExecutionPlan`, `PlanStep`)
Add `field_validator(mode="before")` coercers: `estimated_duration` int → one of
`quick|moderate|long`; `step.id` and `parallel_groups` entries → `str`. Log the raw plan
on validation failure (same pattern as the fast-ack raw-output log). Add a unit test with
the exact failing payload from production logs. *Acceptance:* `journalctl` shows zero
"Plan generation fallback" lines over a day of traffic; plans with two or more distinct
tools appear for compound questions.

**1.2 Query rewriting** — new `app/services/query_rewriter.py`, called from
`_execute_plan` before `document_search`.
Fast model produces 2–3 alternative phrasings plus extracted entities (dates, acronyms
expanded via the glossary). `document_search` runs the variants and merges by
`file_id`/chunk with reciprocal-rank fusion (Milvus hybrid already does RRF internally;
this is a second RRF across variants). Budget: one fast-model call (~150 ms). Reuse the
standalone query-rewrite prompt block removed from the router PR. *Acceptance:* recall
on the 20-question eval set (below) improves; "how do I get reimbursed" finds the
expense-policy chunk.

**1.3 Grounding policy** — `base_agent._build_synthesis_context()` + synthesis prompt.
Replace "answer only from documents" with a tiered rule: (a) documents answer → cite;
(b) documents partially answer → answer, cite, and mark the gap; (c) documents silent →
answer from general knowledge with an explicit "not from company documents" label and a
pointer to who would know. Distinguish *tool error* from *no results* (the tool output's
`error` field) and say "document search was unavailable" in the error case. The
synthesis guard added this week already carries the error-honesty instruction; this item
completes the policy. *Acceptance:* the barge-rate and tax-equity questions get useful,
caveated answers instead of refusals; the search-500 case yields an explicit
unavailability notice.

**1.4 Pipeline guards** — `chat_agent.py`
Anti-clarify-loop: never emit `clarify` twice in a row for one conversation; on the
second pass force `search`. Escalation: the fast model may classify but never
final-answer a question classified `search`/`multi_step`. Pending-question suppression:
profile questions only when the turn is idle, never repeated. *Acceptance:* the payroll
clarify transcript and the "What is your occupation?" loop cannot recur (replay both as
integration tests).

**1.5 Model routing** — `model_registry.yml`
`chat` and `research` on Claude Sonnet (Bedrock); `frontier` on Opus for explicit
escalation and context overflow; `fast`, `tool_calling`, `parsing`, `cleanup`,
`vision` local Qwen. `parsing`/`cleanup` must alias `agent`, not `chat`, or they follow
chat to Bedrock. Commit the mapping so deploys stop reverting it.

**1.6 Memory** — evolve `services/org_glossary.py`
Stage 1 (done): static YAML glossary injected into prompts. Stage 2: org memory in the
Config API (admin-editable in the portal, cached in the agent, reload endpoint). Stage
3: per-user memory — the existing `memory_search`/`memory_save` tools and insights
system, with a nightly extraction job that proposes new org-level terms from
conversations for admin approval. *Acceptance:* "What is PREC?" answers correctly in a
fresh conversation for any user; a new acronym added in the portal is known within a
minute without a deploy.

**1.7 Evaluation harness** — `srv/agent/tests/eval/`
20 canned questions with expected properties (must cite handbook; must not refuse; must
route to search; must not contain `<tool_code>`), run nightly against staging via the
existing `eval_*` tables. This is the regression net for everything above.

### Libraries
Nothing new: pydantic-ai (agents), pydantic (validators), httpx, the embedding API for
the router. Optional: `rank-bm25` is unnecessary — Milvus hybrid covers lexical.

---

## Pathway 2 — Office documents: analyze and generate

### Goal
Attach a Word/Excel/PowerPoint file and ask questions about it (not just search it), and
ask the chat to *produce* a spreadsheet, document or deck as a downloadable file.

### Current state
- The ingest worker (`srv/data`) already parses `.docx` (python-docx), `.xlsx`
  (openpyxl) and `.pptx` (python-pptx) into chunks for RAG. Retrieval works; *analysis*
  ("total column F", "which slides mention Boston Harbor") does not — chunks lose
  structure.
- `AgentContext.resolved_attachments` + `attachment_resolver` inject attached-file
  content into prompts, but as flattened text.
- `data_tool.py` already provides `query_data` / `aggregate_data` / `get_facets` over
  structured "data documents" — a ready-made engine for tabular analysis.
- No generation tools exist; asking for an Excel file returns "I can't create files."

### Design

```
Attachment ──► analyze_document (NEW) ──► structured extract ──► data document ──► query_data / aggregate_data
                 │  docx: headings, paragraphs, tables
                 │  xlsx: sheets → records (header row inferred)
                 └  pptx: slides → title, bullets, notes

"Make me a spreadsheet of X" ──► synthesize spec (JSON) ──► generate_spreadsheet (NEW) ──► MinIO via Data API ──► download link chip
```

### Work items

**2.1 `analyze_document` tool** — `app/tools/office_tool.py`
Input: `file_id` (from attachment metadata) or MinIO path. Pull bytes through the Data
API (user JWT, RLS applies). Extract:
- **xlsx** (openpyxl, `read_only=True`, `data_only=True`): per sheet, infer the header
  row (first row with ≥60% non-empty string cells), emit records; cap 50k cells, note
  truncation. Register the records as a *data document* (`create_data_document`) so the
  planner can follow with `aggregate_data(sum, group_by)` — no new query engine needed.
- **docx** (python-docx): heading tree, paragraphs with style names, tables as records.
- **pptx** (python-pptx): slide index, title, body text, speaker notes, image count.
Output: a compact structured summary (≤4k tokens) plus the data-document id. *Acceptance:*
"attach QCR summary.xlsx — total dredged cubic yards by week" returns the right number
with the sheet cited.

**2.2 `generate_spreadsheet` tool** — same module
Input schema: `{filename, sheets:[{name, columns:[...], rows:[[...]], formulas?:{cell:"=SUM(B2:B10)"}, number_formats?}]}`.
The synthesis model produces this JSON (structured output, schema-validated with the
existing `_run_structured_output` retry loop); the tool renders it with openpyxl (bold
header, freeze panes, autofilter, column widths, native formulas so Excel recalculates),
uploads via the Data API upload endpoint, and returns `{file_id, download_url,
filename}`. The chat frontend already renders attachment chips; add a "generated file"
chip variant. *Acceptance:* "Create an Excel comparing Tavily vs Perplexity costs at 3
volumes" produces a file that opens in Excel with live formulas.

**2.3 `generate_document` / `generate_deck`** — same pattern
python-docx from a `{title, sections:[{heading, paragraphs, bullets, table?}]}` spec;
python-pptx from `{title, slides:[{title, bullets, notes}]}` using a Cashman template
`.potx` stored in MinIO. Keep formatting in the tool, content in the model.

**2.4 Planner and prompt wiring**
Add the tools to the chat agent's tool list; add few-shot lines to the fast-ack
classifier ("create a spreadsheet of…" → `multi_step`, tools=yes); plan step objective
"generate file" → `generate_*`. Guard: generation tools run *after* retrieval steps.

**2.5 Storage and lifecycle**
Generated files land in the user's private library under `generated/`; 30-day
retention job; RLS as for uploads; audit row in `run_records` with the spec.

### Libraries
openpyxl, python-docx, python-pptx (add to `srv/agent/requirements.txt` — already in
`srv/data`), optionally `xlsxwriter` for charts (openpyxl chart support is adequate for
bar/line). No LibreOffice dependency — avoid headless conversions in the agent.

### Scripts to add
- `srv/agent/app/tools/office_tool.py` (analyze + generate)
- `srv/agent/tests/unit/test_office_tool.py` (fixture files: a 3-sheet workbook, a
  docx with tables, a 5-slide deck)
- `docs/users/chat-files.md` (what users can ask for)

---

## Pathway 3 — App data: Marine dashboard, fleet tracker, bid finder

### Goal
Let the chat answer operational questions from the apps people already use — "which
barges are free next month", "open USACE bids in New England due before October",
"status of the Boston Harbor job" — and combine them with documents and web search in
one plan.

### Current state
- The agent already has an MCP client (`app/services/mcp_client.py`: `discover_tools`,
  `call_tool`, `build_mcp_tool_function`) and agent definitions carry an `mcp_servers`
  list (`schemas/definitions.py`). The dispatcher logs "Found 0 MCP servers" — the
  plumbing exists, nothing is plugged in.
- The user apps run on the platform (Deploy API, `user-apps` container) with their own
  Postgres databases on 203 (e.g. `project_analysis`, `innovation`, `foundation`).
- Zero-trust token exchange is the platform rule: tools must carry the user's identity,
  never a service secret.

### Design

```
Chat agent ──► MCP client ──► fleet-mcp     (read-only tools over fleet tracker DB/API)
                          ──► bids-mcp      (read-only tools over bid finder)
                          ──► marine-mcp    (read-only tools over marine dashboard)
                                   │
                          user JWT passed through ─► app API / RLS-scoped DB reads
```

One small MCP server per app (FastMCP, Python), each exposing 3–6 *purpose-built*
tools rather than raw SQL. The planner composes them with existing tools.

### Work items

**3.1 Tool design per app** (decide the questions before writing code)
- **Fleet tracker**: `fleet_list_vessels(type?, status?)`, `fleet_vessel_status(name)`,
  `fleet_availability(start, end)`, `fleet_location(name)` (AIS position if the app has
  it).
- **Bid finder**: `bids_search(region?, agency?, due_before?, keywords?)`,
  `bids_detail(id)`, `bids_watchlist(user)`.
- **Marine dashboard**: `marine_project_summary(job)`, `marine_daily_production(job,
  date_range)`, `marine_open_issues(job)`.
Each returns compact JSON with a `source_url` back to the app page so answers can cite
("Sources" chip links to the fleet tracker record).

**3.2 MCP server skeleton** — `srv/mcp/fleet/` (template for the others)
FastMCP app; each tool validates inputs with pydantic, reads through the app's existing
API where one exists (preferred — reuses its auth and business rules) or via asyncpg
with `SET ROLE`/RLS using the caller's identity. Auth: the agent's MCP client forwards
the exchanged JWT (audience `fleet-mcp`); the server verifies it against AuthZ JWKS like
every other service. Deploy as a systemd unit on the `user-apps` container via a small
Ansible role (`roles/mcp_fleet`), port range 8100+.

**3.3 Registration**
Add the server to the chat agent's `mcp_servers` (admin UI → Agents → Chat → MCP
servers, or the agent definition in the DB). `discover_tools` picks up the tool schemas
at startup; the planner sees them as ordinary tools. Add two few-shot lines to the
classifier ("which barges are available" → tools=yes).

**3.4 Combined plans** (the payoff)
Verify the planner chains tools: "which of our barges are free in October and what open
bids could use them" → `fleet_availability` + `bids_search` in parallel → synthesis
joins on vessel type. This exercises Pathway 1.1 — without the planner fix, MCP tools
only run one at a time.

**3.5 Safety**
Read-only tools only in phase one (no write tools without an explicit confirmation
event in the chat protocol — the `prompt` stream event exists for this). Per-tool
scopes in the token exchange (`fleet.read`, `bids.read`). Rate limits per user.

### Libraries
`mcp` (FastMCP) — already a dependency of the agent; `asyncpg`; `pydantic`; `python-jose`
or the platform's existing JWT verification helper from `busibox_common`.

### Scripts to add
- `srv/mcp/fleet/server.py`, `srv/mcp/bids/server.py`, `srv/mcp/marine/server.py`
- `provision/ansible/roles/mcp_fleet/` (+ bids, marine) — unit file, env template, health
  check
- `srv/agent/tests/integration/test_mcp_fleet.py` — discovery + one call with a test
  token

---

## Cross-cutting: monitoring (Phase 0)

A 40-line script beats every roadmap item above if the services it depends on are
silently down. `scripts/check-health.sh` (cron every 5 min on the Proxmox host): curl
`/health` on embedding-api, search-api, litellm, agent-api, vllm-8000/8001; on failure,
POST to a Slack/email webhook and, for embedding-api, `systemctl reset-failed && start`.
Add `StartLimitIntervalSec=0` + `RestartSec=30` to the embedding-api unit so an OOM
never strands it for days again. Then the nightly eval from 1.7 catches *quality*
regressions the health checks can't.
