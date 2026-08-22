---
title: Semantic Router for Chat Intent Routing
category: developer
order: 22
description: Embedding-based fast path in front of the fast-ack LLM classifier — configuration, shadow/live rollout, and route tuning
published: true
---

# Semantic Router

The semantic router classifies chat queries by embedding similarity against
example utterances, providing a ~100ms deterministic alternative to the
fast-ack LLM call for common intents. Queries it cannot confidently match
fall through to the existing LLM classifier unchanged.

## Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `SEMANTIC_ROUTER_ENABLED` | `false` | Master switch. Off = router never runs; behavior identical to before the feature existed. |
| `SEMANTIC_ROUTER_MODE` | `shadow` | `shadow`: router runs and logs its decision alongside the LLM classifier but is never acted on. `live`: confident matches skip the LLM call. |
| `SEMANTIC_ROUTER_THRESHOLD` | `0.82` | Global cosine-similarity threshold for a match. Routes may override individually. |
| `SEMANTIC_ROUTER_CONFIG_PATH` | *(unset)* | Path to routes YAML. Defaults to `<agent service root>/config/routes.yaml`. |

The router uses the existing embedding-api (`EMBEDDING_API_URL`) as its
encoder. No new services, dependencies, or credentials are introduced.

## Route definitions — `config/routes.yaml`

```yaml
routes:
  hr_policy:
    decision:
      action_type: search        # direct|research|search|analysis|clarify|multi_step
      needs_tools: true
      response: "Let me check the company policies for you."
      complexity: moderate       # simple|moderate|complex
    threshold: 0.85              # optional per-route override
    utterances:
      - "what is the PTO policy"
      - "how do I submit for reimbursement"
```

Editing routes does not require a code deploy: edit the file and restart
the agent service. Utterances are embedded once at startup.

Guidelines: 5–10 utterances per route, phrased the way users actually type;
keep routes semantically well-separated; mine `dispatcher_decision_log`
for real high-confidence queries rather than inventing examples.

## Rollout procedure

1. **Deploy with the flag off** — a no-op.
2. **Shadow mode** (`SEMANTIC_ROUTER_ENABLED=true`, mode `shadow`): the
   router logs a `semantic_router: shadow comparison` line per message
   with its route, score, the LLM's decision, and an `agrees` boolean.
   Run ~2 weeks of real traffic.
3. **Analyze**: agreement rate among matched queries, score distribution
   at disagreements (sets the threshold), and coverage (share of traffic
   matching any route).
4. **Live mode** (mode `live`): confident matches now skip the fast-ack
   LLM call. The config flag reverts instantly if needed.

## Failure behavior

Any router failure — missing config, embedding-api unavailable, malformed
YAML route, below-threshold score — returns no match and falls through to
the fast-ack LLM classifier. The router can only add a fast path, never
remove capability. Routing decisions carry
`routing_source: semantic_router:{route}` in logs and stream events for
observability.
