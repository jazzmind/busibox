---
title: "Chat Detached Turns — responses that survive disconnects"
category: "developer"
order: 42
description: "How a chat request runs as a server-side job with a replayable event log, so a sleeping laptop or closed tab neither cancels the work nor loses the answer, and how the user is emailed when it finishes without them"
published: true
---

# Chat Detached Turns

> **Status (2026-09-15):** implemented in agent-api
> (`app/services/chat_turns.py`, `turn_events.py`, `turn_notifications.py`)
> and in the chat app (`useChatStream`, `ChatShell`). Tests:
> `srv/agent/tests/unit/test_chat_turns.py`, `test_turn_events.py`,
> `test_turn_notifications.py`.

## The problem

Until this change a chat request ran *inside* its own HTTP response. The
agentic dispatcher, every tool call, and the final database commit all
lived in the SSE generator of `POST /chat/message/stream/agentic`, in one
transaction. When the browser went away — laptop sleep, closed tab,
navigation, flaky Wi-Fi — Starlette cancelled the generator, SQLAlchemy
rolled the transaction back, and the whole turn disappeared: the assistant
answer, the **user's own question**, and for a brand-new chat the
conversation itself. A deep-research turn could lose eight minutes of
work and 80 fetched sources that way, and the log said only
`Agentic chat cancelled by client`.

## The model

A request now **starts a turn** and returns a **subscription** to it.

```
POST /chat/message/stream/agentic
  │
  ├─ start_turn()          commit conversation + user message
  │                        insert chat_turns row (status=running)   ← durable before the agent runs
  │                        emit turn_started (+ conversation_created / title_update)
  │                        asyncio.create_task(_run_turn(...))       ← detached from the request
  │
  └─ subscribe(turn_id)    replay the turn's event log, then follow it live
                           (this is the SSE body the browser reads)

_run_turn()                own DB session, runs run_agentic_dispatcher()
  each event ──▶ event log (Redis Stream chat:turn:{id}) ──▶ every subscriber
  on finish   ──▶ commit assistant message + run record, mark turn, emit turn_finished
              ──▶ maybe_notify(): email the user if nobody was attached
```

Three properties fall out of this:

- **Disconnecting only unsubscribes.** The `StreamingResponse` generator
  is a reader of the log. Its cancellation touches nothing the turn owns.
  The explicit **Stop** button (`POST /chat/turns/{id}/stop`) is the one
  thing that cancels.
- **Reattaching is a replay.** Every SSE frame carries an `id:` line. A
  client that comes back asks `GET /chat/turns/{id}/stream?after=<id>` and
  receives the backlog followed by live events, ending with
  `turn_finished`. The browser never re-sends the question, so nothing is
  re-run and no credits are spent twice.
- **The record is written whether or not anyone is watching.** The
  assistant message, the `RunRecord`, thoughts, citations and the turn
  status are committed from the background task. A page loaded a day later
  shows the answer in the conversation like any other.

## Components (agent-api)

### `chat_turns` table — `app/models/domain.py::ChatTurn`

| Column | Purpose |
|--------|---------|
| `id` | Turn id; also the event-log key |
| `conversation_id`, `user_id` | Ownership (`user_id` is checked on every turn endpoint) |
| `status` | `running` → `completed` / `failed` / `cancelled` / `interrupted` |
| `query`, `user_message_id`, `assistant_message_id` | What was asked and what was written |
| `error` | Exception text for `failed`; restart notice for `interrupted` |
| `event_count`, `last_event_id` | Size of the event log at finish (diagnostics) |
| `notified_at` | Set when a completion email went out |
| `started_at`, `finished_at` | Timing |

Created by `app/schema.py` (idempotent DDL, used at deploy) and Alembic
revision `chat_turns_009`. The same revision adds
`chat_settings.notify_email_on_completion BOOLEAN NOT NULL DEFAULT true`.

### Event log — `app/services/turn_events.py`

`EventLog` is a tiny append/replay/follow interface with two backends:

- **`RedisEventLog`** — one Redis Stream per turn, key `chat:turn:{id}`,
  `XADD ... MAXLEN ~ chat_turn_event_maxlen`, sliding `EXPIRE` of
  `chat_turn_event_ttl_seconds` (default 24 h). Readers use `XRANGE` for
  the backlog and `XREAD BLOCK 500` to follow. Stream ids are the SSE
  event ids. This survives an agent-api restart (the log does; the running
  task does not — see *Restarts*) and would work unchanged with several
  agent-api processes.
- **`MemoryEventLog`** — per-process fallback chosen automatically when
  Redis cannot be reached at first use (logged at WARNING), and the backend
  unit tests use. Same semantics, ids are integer counters.

`get_event_log()` picks the backend once per process from `REDIS_URL` /
`settings.redis_url`; `set_event_log()` is the test hook.

### Turn runner — `app/services/chat_turns.py`

- **`start_turn(payload, principal)`** — refuses with `TurnLimitError`
  (→ 429) when the user already has `chat_max_running_turns_per_user`
  running turns, or `ConversationBusyError` (→ 409) when a turn is already
  running in that conversation; `LookupError` (→ 404) for a conversation
  the user does not own. Otherwise commits the request and spawns the
  task. Nothing is written when it refuses.
- **`_run_turn`** — the old handler body, moved: history loading
  (`_load_history`, unchanged), dispatcher call, event bookkeeping, then
  `_persist_result` in a **fresh transaction** (`rollback()` first, so a
  half-done dispatcher cannot poison the commit) and `_after_success`
  (insights follow-up question, online-eval sampling). Every event is
  appended to the log *before* any client sees it. Bridge requests still
  have thinking events filtered (`BRIDGE_FILTERED_AGENTIC_EVENTS`).
- **`TurnRegistry`** — in-process table of running turns
  (`task`, `cancel` event, `subscribers` count). The subscriber count is
  what the email policy reads at the moment a turn ends.
- **`subscribe(turn_id, after)`** — the SSE body: increments
  `subscribers`, yields `id/event/data` frames from the log, decrements on
  exit (including client disconnect).
- **`stop_turn(turn_id)`** — sets the cooperative `cancel` event the
  dispatcher already honoured, then after `chat_turn_stop_grace_seconds`
  hard-cancels the task if it is still running. Either way the partial
  answer is committed with a `*[Response stopped]*` marker and status
  `cancelled`.
- **`shutdown(timeout)`** — called from the FastAPI lifespan on SIGTERM:
  cancels running tasks so each records itself as `interrupted` (partial
  text plus `*[Response interrupted by a server restart after N min — ask
  again to rerun]*`) and, being unattended, emails the user.
- **`sweep_orphans()`** — at startup, any `running` row belongs to a
  process that died without a clean shutdown (OOM, `kill -9`); it is
  marked `interrupted`.

### Terminal statuses and what the user sees

| Status | Assistant message | Email? |
|--------|-------------------|--------|
| `completed` | The answer | If nobody attached, or turn ≥ `chat_notify_min_seconds` |
| `failed` | Partial text + `**Error:** …` | Same rule |
| `cancelled` | Partial text + `*[Response stopped]*` | Never — the user pressed Stop |
| `interrupted` | Partial text + restart notice | Same rule as completed |

### Completion email — `app/services/turn_notifications.py`

Policy ("only if nobody is watching"): `should_notify()` returns true when
the platform switch `chat_notify_email_enabled` is on, the status is not
`cancelled`, and either no subscriber was attached when the turn ended or
it ran at least `chat_notify_min_seconds` (default 120 s — the user has
almost certainly moved on). Then `maybe_notify()` checks the JWT has an
`email` claim and the user's `chat_settings.notify_email_on_completion`
(default on, bell icon in the chat header), builds a text + HTML email
(subject `Busibox: your answer is ready — <conversation title>`, a plain
preview of the answer, direct download links for any files the answer
produced, and a deep link `{PORTAL_BASE_URL}/chat?conversation=<id>`) and
sends it through `email_service.send_email` — the Bridge API
(`BRIDGE_API_URL`) when configured, else SMTP/SendGrid/SES, the same path
agent tasks use. `notified_at` is recorded on success. Email failures are
logged and never affect the turn.

### Endpoints (`app/api/chat.py`)

| Method & path | Purpose | Notes |
|---------------|---------|-------|
| `POST /chat/message/stream/agentic` | Start a turn and stream it | Unchanged contract for existing clients, plus `id:` lines and two new events (`turn_started` first, `turn_finished` last). Returns JSON 404 / 409 / 429 instead of a stream when refused. |
| `GET /chat/turns/{id}/stream?after=<event id>` | Reattach | Backlog after `after` (or from the start), then live, ends at `turn_finished`. 404 unless the caller owns the turn. |
| `GET /chat/turns/{id}` | Status | `id, conversation_id, status, query, user_message_id, assistant_message_id, error, event_count, last_event_id, started_at, finished_at` |
| `POST /chat/turns/{id}/stop` | Stop | `{"turn_id", "stopping": bool, "status"}` |
| `GET /chat/{conversation_id}/active-turn` | The running turn, if any | `{"turn": {...} \| null}` — what a reloaded page reattaches to |
| `PUT /users/me/chat-settings` | `notify_email_on_completion` | Existing settings endpoint, one new field |

SSE frame format, both endpoints:

```
id: 1726400000000-3
event: content
data: {"type":"content","source":"chat","message":"…","data":null,"timestamp":"…"}

id: 1726400000000-9
event: turn_finished
data: {"turn_id":"…","status":"completed","error":null,"assistant_message_id":"…","elapsed_ms":494272}
```

## Frontend (`busibox-frontend`)

- `packages/app/src/lib/agent/chat-client.ts` — `readSseEvents()` parses
  `id:`/`event:`/`data:`; `streamTurn(turnId, afterId)`, `getActiveTurn`,
  `getTurn`, `stopTurn`.
- `packages/app/src/lib/hooks/useChatStream.ts` — records `turn_id` and
  the last event id; when the stream drops after `turn_started` it
  reattaches with backoff (1 s → 30 s, about a minute in total) from the
  last id; `resumeTurn()` does the same for a page that loads while a turn
  is running; `cancel()` calls the stop endpoint and then drops the
  stream.
- `apps/chat/.../marine/ChatShell.tsx` — on conversation open and on
  `visibilitychange` it asks `/active-turn` and resumes; shows a
  "Reconnecting…" banner; if reattaching gives up it reloads the
  conversation, where the answer (or its marker) will already be, and
  keeps the user's message on screen. `MarineNotifyToggle` (bell) writes
  the email preference.

The agents-app proxy (`apps/agents/src/app/api/agent/[...path]/route.ts`)
already forwards query strings and streams bodies, and nginx's `/agents`
location is an SSE route with a 24 h read timeout, so no gateway changes
were needed.

## Operational notes

- **Restarts.** A deploy or `systemctl restart` interrupts running turns
  cleanly (status `interrupted`, user emailed). The event log in Redis
  outlives the restart so a client that reattaches sees everything up to
  the interruption. Work is not resumed — the user is told to ask again.
- **Concurrency.** Turns are asyncio tasks in the single agent-api process,
  the same place they ran before; only their lifetime changed. Limits:
  `chat_max_running_turns_per_user` (default 3) and one running turn per
  conversation.
- **Memory.** A turn's events live in Redis, not in the process; the
  registry holds only a handful of fields per running turn.
- **Without Redis.** Everything works within one process (memory log);
  replay is lost on restart. The startup log line
  `chat turn event log: Redis unavailable …` says which mode is active.
- **Diagnostics.** `journalctl -u agent-api | grep "chat turn"` shows
  `chat turn started` / `chat turn finished` with `turn_id`, `status`,
  `elapsed_ms`, `subscribers` (attached at the end) and `events`. The
  `chat_turns` table is the durable record; `event_count` and
  `last_event_id` tie a row to its stream.

## Settings

Agent-api (`app/config/settings.py`, override via the agent role's env):

| Variable | Default | Meaning |
|----------|---------|---------|
| `CHAT_TURN_EVENT_TTL_SECONDS` | `86400` | How long a finished turn stays replayable in Redis |
| `CHAT_TURN_EVENT_MAXLEN` | `5000` | Events kept per turn (stream trimming) |
| `CHAT_MAX_RUNNING_TURNS_PER_USER` | `3` | Concurrent turns per user before 429 |
| `CHAT_TURN_STOP_GRACE_SECONDS` | `5.0` | Cooperative-stop window before the task is cancelled |
| `CHAT_NOTIFY_EMAIL_ENABLED` | `true` | Platform switch for completion emails |
| `CHAT_NOTIFY_MIN_SECONDS` | `120` | Turns at least this long email even if the user is attached |
| `PORTAL_BASE_URL` | — | Base for the "Open the conversation" link and file links (existing) |
| `BRIDGE_API_URL` / SMTP vars | — | Email transport (existing, see `email_service.py`) |

## Design choices

- **In-process tasks, not a worker queue.** The dispatcher already ran in
  this process and needs its DB session, principal token and tool
  registry; moving it to a worker would have meant serialising all of
  that for no gain in the single-node deployment. The event log is the
  only piece that had to leave the process, and Redis Streams was already
  in the stack.
- **Commit the question first.** The user's message and the `chat_turns`
  row are committed before the agent starts. Whatever happens next, the
  conversation shows what was asked and a marker saying what became of it.
- **Email only when it adds something.** A user watching a 20-second
  answer should not get an email about it; a user whose laptop slept
  through a research run should. The subscriber count at the moment of
  completion is the signal, with a duration floor for long turns.
- **Stop is the only cancel.** Everything else — sleep, reload, closing
  the tab, switching conversations — is treated as "I'll be back".

## Follow-ups

- Bridge channels (Telegram/Signal/etc.) still receive the answer through
  their own delivery path; the email policy is for the web client.
- Resuming an *interrupted* turn from its last tool step (rather than
  asking the user to rerun) would need the loop-first agent to checkpoint
  its `tool_calls` record; the append-only record from the research work
  is the natural place.
- A `GET /chat/turns?status=running` admin view over the `chat_turns`
  table.
