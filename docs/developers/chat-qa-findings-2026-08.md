---
title: Chat QA Findings — August 2026
category: developer
order: 23
description: Production chat defects found via message-log review and live tracing, with root causes, fixes, and status
published: true
---

# Chat QA Findings — August 2026

Source: review of production `messages` table samples (Aug 27–31), live
trace analysis, and journalctl diagnostics. Each finding lists evidence,
root cause, the fix, and its status.

## Status summary

| # | Finding | Severity | Fix status |
|---|---------|----------|------------|
| 1 | Empty responses on short turns ("No response generated.") | High | **Fixed** — commit `b887689e`, pending deploy |
| 2 | Fast-ack speculative wrong answers ("Yes, tomorrow appears to be a holiday.") | High | **Fixed** — commit `401813f9`, pending deploy |
| 3 | Intent misrouting on policy questions | High | **Improved** — few-shot `54dda309` (deployed) + semantic router live |
| 4 | Clarify death-spiral (repeating the same clarifying question) | High | **Open** — anti-loop guard specced, not implemented |
| 5 | Echo-as-answer: question surfaced instead of an answer (C-SAFE, 16:00 Aug 31) | Medium | **Open** — subsumed by escalation fix (#10) |
| 6 | Plan validation rejects every plan (int vs string fields) | Critical | **Open** — coercion validator specced |
| 7 | Company acronyms unknown or wrong (PREC → Canadian real estate) | Medium | **Open** — glossary doc update now; org memory later |
| 8 | Synthesis emitted raw tool-call syntax (`<tool_code>...`) | Medium | **Open** — synthesis prompt + output guard specced |
| 9 | Shadow-router log drops its payload (no measurement data) | Medium | **Open** — embed JSON in message string |
| 10 | Small model final-answers substantive questions | Medium | **Open** — escalation: fast model classifies, never answers |
| 11 | Pending profile questions surfaced uninvited, looped | Medium | **Mitigated** — opt-in flow works; suppression rule open |
| 12 | Web search 401s (expired Perplexity key, provider silently degrading) | High | **Fixed** — Tavily primary (DB tool_configs), Perplexity disabled |
| 13 | Bedrock credentials dead platform-wide (fallbacks silently broken) | High | **Fixed** — new key; vault entry pending for durability |
| 14 | Env-file settings wiped by every deploy (router flags, search keys) | Medium | **Open** — add vars to vault + agent-api.env.j2 |
| 15 | Excel/file-generation requests unsupported | Enhancement | **Open** — roadmap: file-output tool |
| 16 | Chat detected a source-document error (Holiday Schedule July 4th) with no capture mechanism | Enhancement | **Open** — admin notification on detected doc errors |

## Detail per finding

### 1. Empty responses on short conversational turns
Evidence: five occurrences in one 300-message sample ("hi", "Yes",
"boston", "Do it", "lets just continue chatting" → "No response
generated."). Users saw streamed text vanish on re-render.
Root cause: `chat.py` excludes fast_ack-phase content from
`full_content` (correct for tool-path turns); on direct-path turns the
fast-ack IS the answer, so nothing persisted. A `fast_ack_content`
variable was captured but never read — the fix existed half-finished.
Fix: fall back to `fast_ack_content` when `full_content` is empty
(`b887689e`).

### 2. Speculative acknowledgments
Evidence: "Yes, tomorrow appears to be a holiday." streamed instantly,
contradicted by synthesis seconds later.
Root cause: the fast model writes the acknowledgment freeform and
sometimes guesses an answer despite prompt instructions.
Fix: when `needs_tools=true`, replace the model's ack with a canned
neutral phrase, deterministically chosen per query (`401813f9`).
Classification stays with the model; wording does not.

### 3. Intent misrouting
Evidence: "how do I submit for reimbursement" → clarify/direct;
"is tomorrow a holiday" → clarify loop asking "What day is tomorrow?".
Fixes: eight few-shot examples in the fast-ack prompt (`54dda309`,
deployed); semantic router live in production for common intents
(hr_policy, document_lookup, company_info, greeting) with per-route
canned decisions.

### 4. Clarify death-spiral
Evidence: payroll conversation, Aug 27 18:32 — six consecutive clarify
turns, same follow-up question repeated verbatim, user gave up.
Root cause: conversation history containing clarify turns biases the
classifier toward clarify; no rule prevents consecutive clarifies.
Fix (open): if the previous assistant turn was clarify, ban clarify for
the current turn (force search/multi_step). ~5 lines in
`_route_intent`/`run_with_streaming`.

### 5. Echo-as-answer
Evidence: "What is C-SAFE...?" answered sub-second with a different
question, which the user then pasted as their next message; C-SAFE never
answered.
Root cause: fast path surfaced a follow-up/pending question string as
the entire response.
Fix: covered by #10 (escalation); interim guard: never emit a response
that is itself a question when the user asked a definitional question.
Add few-shot: "What is C-SAFE?" → search.

### 6. Plan validation all-or-nothing
Evidence: every production plan rejected — first `estimated_duration: 5`
(int), later step ids and parallel_groups as ints. Planner effectively
disabled; generic fallback plan runs on all queries.
Fix (open): `field_validator(mode="before")` coercing
`estimated_duration`, `PlanStep.id`, and `parallel_groups` entries to
str. Principle: cosmetic fields must never veto structural ones.

### 7. Organizational vocabulary
Evidence: "what is PREC" → PRP injections (retrieval miss), later →
Canadian Personal Real Estate Corporation (confident general-knowledge
wrong answer). PREC = Patriot Renewable Energy Capital.
Fix now: add company acronyms (PREC, C-SAFE, etc.) to the New Employee
Glossary document and re-ingest.
Fix later: org-level shared terminology memory, admin-curated, injected
into synthesis for all users.

### 8. Tool-syntax leakage from synthesis
Evidence: final answer was `<tool_code> print(web_search(...))
</tool_code>` when retrieval was insufficient.
Root cause: synthesis agent uses the general CHAT_SYSTEM_PROMPT which
instructs proactive tool use; synthesis has no tool executor, so the
model's attempted call streamed as text. LiteLLM
`add_function_to_prompt: true` reinforces tool-syntax emission.
Fix (open): dedicated synthesis system prompt ("tools already ran; never
output tool syntax; say what's missing if results are insufficient") +
output guard stripping/retrying on tool-syntax artifacts.

### 9. Shadow log payload
Evidence: `semantic_router: shadow comparison` lines contain no route,
score, or agreement — the JSON formatter drops `extra` fields.
Fix (open): serialize the payload into the message string
(`json.dumps`), same for live-hit and fast-ack decision logs.

### 10. Escalation: small model never final-answers
Evidence: findings 2, 4, 5 are all sub-second fast-path emissions.
Fix (open): when `needs_tools=false` on a substantive query, stream the
ack, then hand the actual response to the main model (without tools).
Pure greetings exempt via heuristics.

### 11. Pending profile questions
Evidence: "What is your occupation and communication_tone?" surfaced
uninvited, repeated after "yes", duplicate rows same second. The opt-in
flow ("Learn about me...") works correctly.
Fix (open): pending questions only surface in the opt-in flow; never as
a reply to unrelated/short turns; never repeat once asked; never expose
raw schema field names.

### 12–13. Provider credentials
Perplexity key expired (in `tool_configs` DB, which outranks env vars —
undocumented precedence); replacement key also invalid (no API credits).
Resolution: Tavily primary (free tier, key in tool_configs), Perplexity
disabled until funded. Bedrock API key was dead platform-wide, meaning
frontier fallbacks silently failed for an unknown period; new key works
(chat now on Sonnet). Durability: key belongs in vault
(`bedrock.api_key`) so litellm redeploys regenerate a working config.

### 14. Config persistence
Deploys regenerate `.env` files, wiping manually added settings
(SEMANTIC_ROUTER_*, formerly search keys). Fix: add these to the vault
schema and `agent-api.env.j2`. Ends the post-deploy re-append ritual.

### 15–16. Enhancements
File generation (xlsx/docx) as a tool — common user expectation from
ChatGPT. Document-error capture: the assistant correctly identified a
factual error in the Holiday Schedule; route such detections to an admin
notification so they become document fixes.

## Lessons

1. **Silent fallbacks become permanent behavior.** Nearly every finding
   was invisible in the UI. Fallback paths must log loudly and be
   counted (fallback-rate metrics, alert on spikes).
2. **Credentials live in five places** (env files, vault, LiteLLM DB,
   tool_configs, admin UI stores) with undocumented precedence.
   Consolidate toward vault + tool_configs and document the hierarchy.
3. **Schema strictness loses to real LLM output.** Validate structure
   strictly, coerce cosmetics leniently.
4. **The fast model is a classifier, not an answerer.** Every place its
   text reached users directly produced a defect.
