---
title: "Personal Memory — what the assistant knows about a user, readable only by that user"
category: "developer"
order: 43
description: "Per-user markdown memory files curated by an agent after chat turns: how they are stored (owner-only rows, envelope-encrypted under the user's key), when they are used (that user's own turns only), how they stay out of search, and how to turn on the database-level row policy"
published: true
---

# Personal Memory

> **Status (2026-09-16):** implemented in agent-api
> (`services/user_memory.py`, `memory_reader.py`, `memory_curator.py`,
> `tools/user_memory_tools.py`, `api/memory.py`) and the chat app
> (`MemoryPanel.tsx`). Layers 2 and 3 below are in code; **layer 1 is a
> database policy the administrator applies** — see *Layer 1* for the
> exact steps.

## What it is

A small set of markdown files per user, in the shape Claude's memory uses:

| File | Holds | Read |
|------|-------|------|
| `profile.md` | who they are: role, team, what they work on | every turn |
| `preferences.md` | how they want answers: format, depth, tone | every turn |
| `topics/<domain>.md` | facts about them by subject | on demand |
| `areas/<name>.md` | an ongoing project, bid, rotation, responsibility | on demand |
| `people/<name>.md` | a colleague or contact, as the user describes them | on demand |

Each file opens with a `description:` frontmatter line. The two core files
are placed in the system prompt of every chat turn; the others appear as a
listing (path + description) and the model opens one with `memory_recall`
when it matters. A curator agent keeps the files current after turns; the
user sees and edits everything on the **Memory** panel (brain icon in the
chat header) and can turn it off or wipe it.

This replaces the role the Milvus "insights" played for personal facts.
Insights still exist; nothing new is written to them by this feature.

## Who can read it — the three layers

### Layer 1 — rows: only the owner's queries return them *(administrator applies)*

Application side, already in place: `MemoryStore` can only be constructed
from a `Principal`, every statement filters on `user_id = principal.sub`,
and every transaction begins with

```sql
SELECT set_config('app.user_id', :uid, true);   -- transaction-local
```

That is the same convention data-api uses for documents. What is *not* yet
in place is the PostgreSQL policy that makes the database refuse any query
on `user_memory_files` that does not carry a matching `app.user_id` — the
guarantee that holds even if a future code path forgets the filter. Apply
it as follows.

**Step 1 — add the policy to the agent schema** so every deploy applies it
(the shared `SchemaManager` has `add_rls`; data-api's `schema.py` uses it
the same way). In `srv/agent/app/schema.py`, after the
`user_memory_files` index:

```python
    # Personal memory: the database itself only returns a user's own rows.
    schema.add_rls("ALTER TABLE user_memory_files ENABLE ROW LEVEL SECURITY")
    schema.add_rls("ALTER TABLE user_memory_files FORCE ROW LEVEL SECURITY")
    schema.add_rls("DROP POLICY IF EXISTS user_memory_owner ON user_memory_files")
    schema.add_rls("""
        CREATE POLICY user_memory_owner ON user_memory_files
        USING (user_id = current_setting('app.user_id', true))
        WITH CHECK (user_id = current_setting('app.user_id', true))
    """)
```

`FORCE` matters: `busibox_user` owns the table, and owners bypass RLS
unless it is forced. `USING` governs SELECT/UPDATE/DELETE visibility;
`WITH CHECK` refuses an INSERT or UPDATE whose `user_id` is not the caller.
`current_setting(..., true)` returns NULL rather than erroring when the
variable is unset, and `NULL = x` is never true — an unbound session sees
nothing.

**Step 2 — or apply it by hand once** (staging first), as the database
owner:

```sql
ALTER TABLE user_memory_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_memory_files FORCE ROW LEVEL SECURITY;
CREATE POLICY user_memory_owner ON user_memory_files
  USING (user_id = current_setting('app.user_id', true))
  WITH CHECK (user_id = current_setting('app.user_id', true));
```

**Step 3 — verify.** In `psql` as `busibox_user`:

```sql
SELECT count(*) FROM user_memory_files;                              -- 0, always (no app.user_id)
BEGIN; SELECT set_config('app.user_id', '<a real sub>', true);
SELECT path FROM user_memory_files;                                  -- that user's files
SELECT set_config('app.user_id', 'someone-else', true);
SELECT path FROM user_memory_files;                                  -- none
ROLLBACK;
```

Then run `make test-docker SERVICE=agent ARGS="tests/unit/test_user_memory.py"`
— the store tests bind `app.user_id` on every transaction and pass with the
policy on.

Things to know:

- Nothing else in agent-api touches this table, so forcing RLS cannot break
  another feature. Any new code that does must go through `MemoryStore`.
- `alembic` runs as the owner without `app.user_id`; DDL is unaffected by
  RLS. A hypothetical data migration over rows would need
  `SET LOCAL app.user_id` per user or a temporary `ALTER TABLE … NO FORCE`.
- The policy compares `user_id` (the JWT `sub`, a string) — not a UUID cast
  — because agent-api stores subjects as `VARCHAR(255)`.
- Backups still contain the rows; that is what layer 2 is for.

### Layer 2 — bytes: envelope-encrypted under the user's own key *(implemented)*

`KeystoreCrypto` sends each write to the authz keystore
(`POST /keystore/encrypt`) with `user_id` only and no roles: a fresh data
key per write, wrapped solely by that user's KEK. Reads call
`/keystore/decrypt`, which succeeds only for a bearer token whose subject is
the owner (authz checks the JWT, not a header). The token is the user's own
JWT — exchanged for the `authz-api` audience through the normal Zero Trust
token exchange when needed; no service credential exists. A superseded
blob's wrapped key is dropped (`DELETE /keystore/file/{blob_id}`), so old
ciphertext in a backup has no key.

The database row therefore holds ciphertext plus the clear-text
`description` line (needed for listings without a decrypt round-trip;
descriptions are one line and are written by the same rules as content).
With `MEMORY_ENCRYPTION_REQUIRED=true` (default) a write is *refused* when
the keystore is unreachable rather than stored in clear; reads of
previously stored rows still work once it is back.

What this does not do: the operator who holds the Ansible vault (and so the
keystore master key) can decrypt anything. That is the ceiling for a
self-hosted box without per-user passkey-derived keys, and the trade-off is
deliberate — the curator must be able to write after the user has walked
away.

### Layer 3 — use: only in the owner's own chat turn *(implemented)*

- `load_memory_context()` runs in the dispatcher with the turn's
  `Principal`; the result lives on `AgentContext.memory` for that turn and
  is rendered into the system prompt, never into `messages`, `run_records`,
  `routing_decision` or the event stream. The stream gets one thought,
  `Using your memory (N files)`, so the user can see it was consulted.
- `memory_recall` returns file content to the model only; on the event
  stream its payload is redacted to `{success, path, redacted: true}`
  (`BaseAgent._stream_safe_result_data`).
- Nothing logs content: the curator logs counts of operations; the store
  logs nothing about content at all.
- Shared conversations show messages, not the memory that shaped them.
  Custom agents built by another user run under *their* principal and get
  their own memory. Bridge channels (Telegram etc.) are excluded from
  curation (`_is_bridge_request`) and the chat agent tools still apply the
  mapped user's principal.
- The curator runs under the same principal as the turn, in a background
  task started from `_after_success`, so the answer is not delayed.

### Not discoverable

Memory files are not in the Documents library, not chunked, not in Milvus,
not in the graph. "Which topic file is relevant" is decided from the
listing (descriptions), not by a vector index — the curator's
`_relevant_paths` and the model's own choice through `memory_recall`. There
is no `/users/{id}/memory`, no admin listing, and no search endpoint.

## Components

| Piece | File | Role |
|-------|------|------|
| Model + DDL | `app/models/domain.py::UserMemoryFile`, `app/schema.py`, `alembic/versions/20260916_0000_010_user_memory.py` | `user_memory_files`; `chat_settings.memory_enabled` |
| Store | `app/services/user_memory.py` | path rules, versions (optimistic), size/file caps, never-store guard (card/SSN shapes), `KeystoreCrypto`, `memory_enabled_for()` |
| Reader | `app/services/memory_reader.py` | core files + listing → `MemoryContext.prompt_block()`; hooked in `agentic_dispatcher` and `BaseAgent` prompt builders |
| Tools | `app/tools/user_memory_tools.py` | `memory_recall`, `memory_remember`, `memory_forget` (chat agent; aliases `remember`/`forget`/`recall`) |
| Curator | `app/services/memory_curator.py` | gate (fast model) → curate (agent model) → `write/str_replace/append/delete` ops; scheduled from `chat_turns._after_success` |
| API | `app/api/memory.py` | `/users/me/memory` list/read/write/delete/delete-all/export; on/off via `PUT /users/me/chat-settings {memory_enabled}` |
| UI | `busibox-frontend/apps/chat/.../marine/MemoryPanel.tsx`, `MemoryToggle.tsx` | brain icon → panel: files, edit, delete, forget everything, export, on/off |

## The curator's rules (summary of `CURATOR_SYSTEM`)

Only what the user *said*; update rather than accumulate; one fact per
line in the user's words; never store health, finances, identity or card
numbers, protected attributes, immigration status, another person's private
life, or secrets — and when the durable fact *is* one of those, write
nothing, not a placeholder. "Forget X" removes the line. Most turns end
with `{"ops": []}`. The store enforces the number patterns mechanically
whoever the writer is.

The gate costs one short call on the `fast` model per substantive turn;
the curator runs only when the gate says a durable fact is present, or
when the user said "remember"/"forget". Default curator model is the
local `agent` purpose so conversations are not sent to a cloud provider
for this; `MEMORY_CURATOR_PURPOSE=chat` files more accurately at that
cost.

## Settings

| Variable | Default | Meaning |
|----------|---------|---------|
| `MEMORY_ENABLED` | `true` | Platform switch |
| `MEMORY_ENCRYPTION_REQUIRED` | `true` | Refuse clear-text storage when the keystore is unavailable |
| `MEMORY_CURATOR_PURPOSE` | `agent` | Model purpose for curation |
| `MEMORY_GATE_PURPOSE` | `fast` | Model purpose for the durable-fact check |
| `MEMORY_MAX_FILES` | `40` | Files per user |
| `MEMORY_MAX_FILE_BYTES` | `8000` | Per-file size cap |
| `MEMORY_CORE_MAX_CHARS` | `3000` | Cap on profile + preferences injected per turn |
| `MEMORY_CURATE_MAX_TURN_CHARS` | `6000` | How much of the exchange the curator sees |

## Follow-ups

- Nightly consolidation task (dedupe, condense long files) via the
  scheduler, under each user's principal — needs a stored delegation token
  or a run-at-next-login trigger; not started.
- Opt-in activity signals from Documents (library names, upload subjects)
  as user-token events on a Redis stream; phase 2.
- Migrate existing personal insights (Milvus) into `profile.md` /
  `preferences.md` once, then retire `PROFILE_FIELDS`.
