---
title: Agent Server API Reference
category: reference
created: 2025-12-12
updated: 2025-12-12
status: active
tags: [agent-server, api, rest, endpoints]
---

# Agent Server API Reference

## Overview

The agent server provides a REST API for AI agent execution, tool orchestration, workflow management, and intelligent query routing.

**Base URL**: `http://agent-lxc:4111`  
**Authentication**: JWT Bearer token  
**Content-Type**: `application/json`

## Authentication

All endpoints require a JWT Bearer token in the Authorization header:

```bash
Authorization: Bearer <jwt-token>
```

### Required Scopes

- `agent.execute` - Execute agents and workflows
- `agent.read` - Read agent/tool/workflow definitions
- `agent.write` - Create/update/delete definitions
- `admin.read` - Admin read access
- `admin.write` - Admin write access

## Endpoints

### Health Check

#### GET /health

Check service health.

**Authentication**: Not required

**Response**:
```json
{
  "status": "ok"
}
```

**Status Codes**:
- `200 OK` - Service healthy
- `503 Service Unavailable` - Service unhealthy

---

### Agent Management

#### GET /agents

List all agents (built-in + personal).

**Authentication**: Required  
**Scopes**: `agent.read`

**Query Parameters**:
- `is_builtin` (boolean, optional) - Filter by built-in status
- `is_active` (boolean, optional) - Filter by active status

**Response**:
```json
[
  {
    "id": "uuid",
    "name": "chat_agent",
    "display_name": "Chat Agent",
    "instructions": "You are a helpful assistant",
    "model": "agent",
    "tools": {"names": ["search_tool", "ingest_tool"]},
    "workflows": {"names": []},
    "scopes": ["search.read", "ingest.write"],
    "is_builtin": true,
    "created_by": null,
    "version": 1,
    "is_active": true,
    "created_at": "2025-12-12T10:00:00Z",
    "updated_at": "2025-12-12T10:00:00Z"
  }
]
```

**Status Codes**:
- `200 OK` - Success
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Insufficient permissions

#### POST /agents/definitions

Create a new personal agent.

**Authentication**: Required  
**Scopes**: `agent.write`

**Request Body**:
```json
{
  "name": "my_agent",
  "display_name": "My Personal Agent",
  "instructions": "You are my personal assistant",
  "model": "agent",
  "tools": {"names": ["search_tool"]},
  "workflows": {"names": []},
  "scopes": ["search.read"]
}
```

**Response**:
```json
{
  "id": "uuid",
  "name": "my_agent",
  "display_name": "My Personal Agent",
  "instructions": "You are my personal assistant",
  "model": "agent",
  "tools": {"names": ["search_tool"]},
  "workflows": {"names": []},
  "scopes": ["search.read"],
  "is_builtin": false,
  "created_by": "user-123",
  "version": 1,
  "is_active": true,
  "created_at": "2025-12-12T10:00:00Z",
  "updated_at": "2025-12-12T10:00:00Z"
}
```

**Status Codes**:
- `201 Created` - Agent created
- `400 Bad Request` - Invalid request body
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Insufficient permissions
- `409 Conflict` - Agent name already exists

#### GET /agents/{agent_id}

Get agent by ID.

**Authentication**: Required  
**Scopes**: `agent.read`

**Path Parameters**:
- `agent_id` (UUID) - Agent ID

**Response**: Same as POST /agents/definitions

**Status Codes**:
- `200 OK` - Success
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Insufficient permissions
- `404 Not Found` - Agent not found or not accessible

---

### Run Execution

#### POST /runs

Execute an agent.

**Authentication**: Required  
**Scopes**: `agent.execute`

**Request Body**:
```json
{
  "agent_id": "uuid",
  "input": {
    "prompt": "What is the weather in London?"
  },
  "agent_tier": "complex"
}
```

**Fields**:
- `agent_id` (UUID, required) - Agent to execute
- `input` (object, required) - Input data with `prompt` field
- `agent_tier` (string, optional) - Execution tier: `simple`, `complex`, `batch` (default: `simple`)

**Response**:
```json
{
  "id": "uuid",
  "agent_id": "uuid",
  "workflow_id": null,
  "status": "running",
  "input": {
    "prompt": "What is the weather in London?"
  },
  "output": null,
  "events": [
    {
      "type": "created",
      "timestamp": "2025-12-12T10:00:00Z",
      "data": {}
    }
  ],
  "created_by": "user-123",
  "created_at": "2025-12-12T10:00:00Z",
  "updated_at": "2025-12-12T10:00:00Z",
  "completed_at": null
}
```

**Status Codes**:
- `202 Accepted` - Run created and executing
- `400 Bad Request` - Invalid request body
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Insufficient permissions
- `404 Not Found` - Agent not found

#### GET /runs/{run_id}

Get run details.

**Authentication**: Required  
**Scopes**: `agent.read`

**Path Parameters**:
- `run_id` (UUID) - Run ID

**Response**: Same as POST /runs, with completed fields

**Status Codes**:
- `200 OK` - Success
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Not owner or admin
- `404 Not Found` - Run not found

#### GET /runs

List runs with filtering.

**Authentication**: Required  
**Scopes**: `agent.read`

**Query Parameters**:
- `agent_id` (UUID, optional) - Filter by agent
- `status` (string, optional) - Filter by status: `pending`, `running`, `succeeded`, `failed`, `timeout`
- `created_by` (string, optional) - Filter by user (auto-set for non-admin)
- `limit` (integer, optional) - Max results (1-100, default: 20)
- `offset` (integer, optional) - Skip results (default: 0)

**Response**:
```json
[
  {
    "id": "uuid",
    "agent_id": "uuid",
    "status": "succeeded",
    "input": {...},
    "output": {...},
    "created_by": "user-123",
    "created_at": "2025-12-12T10:00:00Z",
    "completed_at": "2025-12-12T10:00:10Z"
  }
]
```

**Status Codes**:
- `200 OK` - Success
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Insufficient permissions

---

### SSE Streaming

#### GET /streams/runs/{run_id}

Stream run updates via Server-Sent Events.

**Authentication**: Required  
**Scopes**: `agent.read`

**Path Parameters**:
- `run_id` (UUID) - Run ID

**Event Types**:

**status** - Status change:
```
event: status
data: {"status": "running"}
```

**event** - New event:
```
event: event
data: {"type": "tool_call", "timestamp": "...", "data": {...}}
```

**output** - Run completed:
```
event: output
data: {"message": "The weather in London is..."}
```

**complete** - Stream closing:
```
event: complete
data: {"status": "succeeded"}
```

**error** - Error occurred:
```
event: error
data: {"error": "Agent execution failed"}
```

**Status Codes**:
- `200 OK` - Stream started
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Not owner or admin
- `404 Not Found` - Run not found

---

### Dispatcher

#### POST /dispatcher/route

Route query to appropriate tools/agents.

**Authentication**: Required  
**Scopes**: `agent.execute`

**Request Body**:
```json
{
  "query": "What does our Q4 report say about revenue?",
  "available_tools": ["doc_search", "web_search"],
  "available_agents": [],
  "attachments": [],
  "user_settings": {
    "enabled_tools": ["doc_search", "web_search"],
    "enabled_agents": []
  }
}
```

**Response**:
```json
{
  "selected_tools": ["doc_search"],
  "selected_agents": [],
  "confidence": 0.95,
  "reasoning": "Query asks about internal Q4 report, which requires document search",
  "alternatives": []
}
```

**Status Codes**:
- `200 OK` - Routing decision returned
- `400 Bad Request` - Invalid request body
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Insufficient permissions

---

### Tool Management

#### GET /agents/tools

List all tools.

**Authentication**: Required  
**Scopes**: `agent.read`

**Response**:
```json
[
  {
    "id": "uuid",
    "name": "search_tool",
    "description": "Semantic search across documents",
    "schema": {
      "input": {"type": "object", "properties": {...}},
      "output": {"type": "object", "properties": {...}}
    },
    "entrypoint": "app.tools.search_tool:search_tool",
    "scopes": ["search.read"],
    "is_builtin": true,
    "created_by": null,
    "version": 1,
    "is_active": true,
    "created_at": "2025-12-12T10:00:00Z",
    "updated_at": "2025-12-12T10:00:00Z"
  }
]
```

**Status Codes**:
- `200 OK` - Success
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Insufficient permissions

#### POST /agents/tools

Create a custom tool.

**Authentication**: Required  
**Scopes**: `agent.write`

**Request Body**:
```json
{
  "name": "my_tool",
  "description": "My custom tool",
  "schema": {
    "input": {"type": "object"},
    "output": {"type": "object"}
  },
  "entrypoint": "app.tools.my_tool:my_tool",
  "scopes": []
}
```

**Response**: Same as GET /agents/tools (single tool)

**Status Codes**:
- `201 Created` - Tool created
- `400 Bad Request` - Invalid request body
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Insufficient permissions
- `409 Conflict` - Tool name already exists

#### GET /agents/tools/{tool_id}

Get tool by ID.

**Authentication**: Required  
**Scopes**: `agent.read`

**Path Parameters**:
- `tool_id` (UUID) - Tool ID

**Response**: Same as POST /agents/tools

**Status Codes**:
- `200 OK` - Success
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Insufficient permissions
- `404 Not Found` - Tool not found

#### PUT /agents/tools/{tool_id}

Update a custom tool.

**Authentication**: Required  
**Scopes**: `agent.write`

**Path Parameters**:
- `tool_id` (UUID) - Tool ID

**Request Body** (all fields optional):
```json
{
  "description": "Updated description",
  "schema": {...},
  "entrypoint": "app.tools.my_tool:my_tool_v2"
}
```

**Response**: Updated tool (version incremented)

**Status Codes**:
- `200 OK` - Tool updated
- `400 Bad Request` - Invalid request body
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Not owner or built-in tool
- `404 Not Found` - Tool not found

#### DELETE /agents/tools/{tool_id}

Soft-delete a custom tool.

**Authentication**: Required  
**Scopes**: `agent.write`

**Path Parameters**:
- `tool_id` (UUID) - Tool ID

**Response**: 204 No Content

**Status Codes**:
- `204 No Content` - Tool deleted
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Not owner or built-in tool
- `404 Not Found` - Tool not found
- `409 Conflict` - Tool in use by active agents

---

### Workflow Management

#### GET /agents/workflows

List all workflows.

**Authentication**: Required  
**Scopes**: `agent.read`

**Response**:
```json
[
  {
    "id": "uuid",
    "name": "doc_analysis",
    "description": "Analyze document with multiple steps",
    "steps": [
      {
        "name": "search",
        "type": "tool",
        "tool": "search_tool",
        "input": {"query": "$.input.query"}
      },
      {
        "name": "analyze",
        "type": "agent",
        "agent": "rag_agent",
        "input": {"prompt": "Analyze: $.search.output"}
      }
    ],
    "created_by": "user-123",
    "version": 1,
    "is_active": true,
    "created_at": "2025-12-12T10:00:00Z",
    "updated_at": "2025-12-12T10:00:00Z"
  }
]
```

**Status Codes**:
- `200 OK` - Success
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Insufficient permissions

#### POST /agents/workflows

Create a workflow.

**Authentication**: Required  
**Scopes**: `agent.write`

**Request Body**: Same as GET response (without id, version, timestamps)

**Response**: Created workflow

**Status Codes**:
- `201 Created` - Workflow created
- `400 Bad Request` - Invalid request body or steps
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Insufficient permissions
- `409 Conflict` - Workflow name already exists

#### GET /agents/workflows/{workflow_id}

Get workflow by ID.

**Authentication**: Required  
**Scopes**: `agent.read`

**Path Parameters**:
- `workflow_id` (UUID) - Workflow ID

**Response**: Same as POST /agents/workflows

**Status Codes**:
- `200 OK` - Success
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Insufficient permissions
- `404 Not Found` - Workflow not found

#### PUT /agents/workflows/{workflow_id}

Update a workflow.

**Authentication**: Required  
**Scopes**: `agent.write`

**Path Parameters**:
- `workflow_id` (UUID) - Workflow ID

**Request Body** (all fields optional):
```json
{
  "description": "Updated description",
  "steps": [...]
}
```

**Response**: Updated workflow (version incremented)

**Status Codes**:
- `200 OK` - Workflow updated
- `400 Bad Request` - Invalid request body or steps
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Not owner
- `404 Not Found` - Workflow not found

#### DELETE /agents/workflows/{workflow_id}

Soft-delete a workflow.

**Authentication**: Required  
**Scopes**: `agent.write`

**Path Parameters**:
- `workflow_id` (UUID) - Workflow ID

**Response**: 204 No Content

**Status Codes**:
- `204 No Content` - Workflow deleted
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Not owner
- `404 Not Found` - Workflow not found
- `409 Conflict` - Workflow has active schedules

#### POST /runs/workflow

Execute a workflow.

**Authentication**: Required  
**Scopes**: `agent.execute`

**Request Body**:
```json
{
  "workflow_id": "uuid",
  "input": {
    "query": "Analyze Q4 report"
  }
}
```

**Response**: Same as POST /runs

**Status Codes**:
- `202 Accepted` - Workflow execution started
- `400 Bad Request` - Invalid request body
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Insufficient permissions
- `404 Not Found` - Workflow not found

---

### Evaluator Management

#### GET /agents/evals

List all evaluators.

**Authentication**: Required  
**Scopes**: `agent.read`

**Response**:
```json
[
  {
    "id": "uuid",
    "name": "latency_scorer",
    "description": "Score based on execution time",
    "criteria": {
      "type": "latency",
      "threshold_ms": 5000
    },
    "llm_config": null,
    "created_by": "user-123",
    "version": 1,
    "is_active": true,
    "created_at": "2025-12-12T10:00:00Z",
    "updated_at": "2025-12-12T10:00:00Z"
  }
]
```

**Status Codes**:
- `200 OK` - Success
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Insufficient permissions

#### POST /agents/evals

Create an evaluator.

**Authentication**: Required  
**Scopes**: `agent.write`

**Request Body**: Same as GET response (without id, version, timestamps)

**Response**: Created evaluator

**Status Codes**:
- `201 Created` - Evaluator created
- `400 Bad Request` - Invalid request body
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Insufficient permissions
- `409 Conflict` - Evaluator name already exists

#### GET /agents/evals/{eval_id}

Get evaluator by ID.

**Authentication**: Required  
**Scopes**: `agent.read`

**Path Parameters**:
- `eval_id` (UUID) - Evaluator ID

**Response**: Same as POST /agents/evals

**Status Codes**:
- `200 OK` - Success
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Insufficient permissions
- `404 Not Found` - Evaluator not found

#### PUT /agents/evals/{eval_id}

Update an evaluator.

**Authentication**: Required  
**Scopes**: `agent.write`

**Path Parameters**:
- `eval_id` (UUID) - Evaluator ID

**Request Body** (all fields optional):
```json
{
  "description": "Updated description",
  "criteria": {...}
}
```

**Response**: Updated evaluator (version incremented)

**Status Codes**:
- `200 OK` - Evaluator updated
- `400 Bad Request` - Invalid request body
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Not owner
- `404 Not Found` - Evaluator not found

#### DELETE /agents/evals/{eval_id}

Soft-delete an evaluator.

**Authentication**: Required  
**Scopes**: `agent.write`

**Path Parameters**:
- `eval_id` (UUID) - Evaluator ID

**Response**: 204 No Content

**Status Codes**:
- `204 No Content` - Evaluator deleted
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Not owner
- `404 Not Found` - Evaluator not found

---

### Performance Scoring

#### POST /scores/execute

Execute a scorer against a run.

**Authentication**: Required  
**Scopes**: `agent.execute`

**Request Body**:
```json
{
  "eval_id": "uuid",
  "run_id": "uuid"
}
```

**Response**:
```json
{
  "eval_id": "uuid",
  "run_id": "uuid",
  "score": 0.85,
  "passed": true,
  "details": {
    "latency_ms": 3500,
    "threshold_ms": 5000
  }
}
```

**Status Codes**:
- `200 OK` - Score calculated
- `400 Bad Request` - Invalid request body
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Insufficient permissions
- `404 Not Found` - Evaluator or run not found

#### GET /scores/aggregates

Get aggregated score statistics.

**Authentication**: Required  
**Scopes**: `agent.read`

**Query Parameters**:
- `eval_id` (UUID, optional) - Filter by evaluator
- `agent_id` (UUID, optional) - Filter by agent

**Response**:
```json
{
  "total_runs": 100,
  "avg_score": 0.85,
  "min_score": 0.45,
  "max_score": 1.0,
  "pass_rate": 0.92
}
```

**Status Codes**:
- `200 OK` - Statistics returned
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Insufficient permissions

---

### Schedule Management

#### POST /runs/schedule

Schedule a recurring agent run.

**Authentication**: Required  
**Scopes**: `agent.write`

**Request Body**:
```json
{
  "agent_id": "uuid",
  "input": {
    "prompt": "Daily summary"
  },
  "cron_expression": "0 9 * * *",
  "timezone": "UTC"
}
```

**Response**:
```json
{
  "id": "uuid",
  "agent_id": "uuid",
  "workflow_id": null,
  "input": {...},
  "cron_expression": "0 9 * * *",
  "timezone": "UTC",
  "next_run_time": "2025-12-13T09:00:00Z",
  "is_active": true,
  "created_by": "user-123",
  "created_at": "2025-12-12T10:00:00Z"
}
```

**Status Codes**:
- `201 Created` - Schedule created
- `400 Bad Request` - Invalid cron expression
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Insufficient permissions
- `404 Not Found` - Agent not found

#### GET /runs/schedule

List schedules.

**Authentication**: Required  
**Scopes**: `agent.read`

**Query Parameters**:
- `agent_id` (UUID, optional) - Filter by agent
- `is_active` (boolean, optional) - Filter by active status

**Response**: Array of schedules (same as POST response)

**Status Codes**:
- `200 OK` - Success
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Insufficient permissions

#### DELETE /runs/schedule/{schedule_id}

Cancel a schedule.

**Authentication**: Required  
**Scopes**: `agent.write`

**Path Parameters**:
- `schedule_id` (UUID) - Schedule ID

**Response**: 204 No Content

**Status Codes**:
- `204 No Content` - Schedule cancelled
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Not owner or admin
- `404 Not Found` - Schedule not found

---

## Error Responses

All errors follow this format:

```json
{
  "detail": "Error message"
}
```

### Common Error Codes

- `400 Bad Request` - Invalid request body or parameters
- `401 Unauthorized` - Missing or invalid authentication token
- `403 Forbidden` - Insufficient permissions or not resource owner
- `404 Not Found` - Resource not found or not accessible
- `409 Conflict` - Resource conflict (name exists, in use, etc.)
- `500 Internal Server Error` - Server error

## Rate Limits

**Not yet implemented**

Planned limits:
- 100 requests/minute per user
- 1000 requests/hour per user
- 10 concurrent runs per user

## Related Documentation

- **Architecture**: `docs/architecture/agent-server-architecture.md`
- **Deployment**: `docs/deployment/agent-server-deployment.md`
- **Testing**: `docs/guides/agent-server-testing.md`
- **Integration**: `docs/architecture/agent-manager-integration.md`









