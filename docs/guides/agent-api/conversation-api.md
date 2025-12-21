---
created: 2025-12-12
updated: 2025-12-12
status: active
category: reference
tags: [agent-server, api, conversations, messages, chat]
---

# Agent Server Conversation API Reference

## Overview

The Agent Server provides conversation and message management endpoints to support persistent chat sessions in the agent-client interface. This allows users to maintain conversation history, manage chat settings, and track message metadata including routing decisions and tool calls.

**Base URL**: `http://agent-lxc:8000` (production) or `http://localhost:8000` (local development)

**Authentication**: All endpoints require Bearer token authentication with Busibox JWT.

---

## Endpoints

### Conversation Management

#### List Conversations

Get paginated list of user's conversations with message counts and last message preview.

```http
GET /conversations
Authorization: Bearer <token>
```

**Query Parameters:**
- `limit` (integer, default: 50, max: 100): Number of conversations to return
- `offset` (integer, default: 0): Number of conversations to skip
- `order_by` (string, default: "created_at"): Field to order by (`created_at`, `updated_at`)
- `order` (string, default: "desc"): Sort order (`asc`, `desc`)

**Response 200:**
```json
{
  "conversations": [
    {
      "id": "uuid",
      "title": "string",
      "user_id": "string",
      "message_count": 5,
      "last_message": {
        "role": "assistant",
        "content": "Last message preview (max 100 chars)",
        "created_at": "2025-12-12T10:00:00Z"
      },
      "created_at": "2025-12-12T09:00:00Z",
      "updated_at": "2025-12-12T10:00:00Z"
    }
  ],
  "total": 42,
  "limit": 50,
  "offset": 0
}
```

#### Create Conversation

Create a new conversation for the authenticated user.

```http
POST /conversations
Authorization: Bearer <token>
Content-Type: application/json
```

**Request Body:**
```json
{
  "title": "My Custom Conversation"  // Optional, defaults to "New Conversation"
}
```

**Response 201:**
```json
{
  "id": "uuid",
  "title": "My Custom Conversation",
  "user_id": "string",
  "message_count": 0,
  "last_message": null,
  "created_at": "2025-12-12T10:00:00Z",
  "updated_at": "2025-12-12T10:00:00Z"
}
```

#### Get Conversation

Get conversation details with optional paginated messages.

```http
GET /conversations/{conversation_id}
Authorization: Bearer <token>
```

**Query Parameters:**
- `include_messages` (boolean, default: true): Include messages in response
- `message_limit` (integer, default: 100, max: 500): Max messages to return
- `message_offset` (integer, default: 0): Message offset for pagination

**Response 200:**
```json
{
  "id": "uuid",
  "title": "string",
  "user_id": "string",
  "created_at": "2025-12-12T09:00:00Z",
  "updated_at": "2025-12-12T10:00:00Z",
  "messages": [
    {
      "id": "uuid",
      "conversation_id": "uuid",
      "role": "user",
      "content": "What is the weather today?",
      "attachments": null,
      "run_id": null,
      "routing_decision": null,
      "tool_calls": null,
      "created_at": "2025-12-12T09:30:00Z"
    },
    {
      "id": "uuid",
      "conversation_id": "uuid",
      "role": "assistant",
      "content": "Let me check the weather for you.",
      "attachments": null,
      "run_id": "uuid",
      "routing_decision": {
        "selected_tools": ["weather"],
        "selected_agents": [],
        "confidence": 0.95,
        "reasoning": "User asking about weather, using weather tool"
      },
      "tool_calls": [
        {
          "tool": "weather",
          "result": "..."
        }
      ],
      "created_at": "2025-12-12T09:30:05Z"
    }
  ]
}
```

**Response 403:** Forbidden (not owner of conversation)  
**Response 404:** Conversation not found

#### Update Conversation

Update conversation title.

```http
PATCH /conversations/{conversation_id}
Authorization: Bearer <token>
Content-Type: application/json
```

**Request Body:**
```json
{
  "title": "Updated Title"
}
```

**Response 200:** ConversationRead object  
**Response 403:** Forbidden (not owner)  
**Response 404:** Not found

#### Delete Conversation

Delete conversation and all its messages (cascade delete).

```http
DELETE /conversations/{conversation_id}
Authorization: Bearer <token>
```

**Response 204:** No Content (success)  
**Response 403:** Forbidden (not owner)  
**Response 404:** Not found

---

### Message Management

#### List Messages

Get paginated messages in a conversation.

```http
GET /conversations/{conversation_id}/messages
Authorization: Bearer <token>
```

**Query Parameters:**
- `limit` (integer, default: 100, max: 500): Number of messages to return
- `offset` (integer, default: 0): Number of messages to skip
- `order` (string, default: "asc"): Sort order by created_at (`asc`, `desc`)

**Response 200:**
```json
{
  "messages": [...],
  "total": 25,
  "limit": 100,
  "offset": 0
}
```

**Response 403:** Forbidden (not owner of conversation)

#### Create Message

Create a new message in a conversation.

```http
POST /conversations/{conversation_id}/messages
Authorization: Bearer <token>
Content-Type: application/json
```

**Request Body:**
```json
{
  "role": "user",  // "user", "assistant", or "system"
  "content": "What is the capital of France?",
  "attachments": [  // Optional
    {
      "name": "document.pdf",
      "type": "application/pdf",
      "url": "s3://bucket/document.pdf",
      "size": 1024,
      "knowledge_base_id": "kb-123"
    }
  ],
  "run_id": "uuid",  // Optional: Link to agent run
  "routing_decision": {},  // Optional: Dispatcher decision
  "tool_calls": []  // Optional: Tool call results
}
```

**Response 201:** MessageRead object  
**Response 403:** Forbidden (not owner)  
**Response 422:** Validation error (invalid role, etc.)

**Note:** Creating a message automatically updates the conversation's `updated_at` timestamp.

#### Get Message

Get a single message by ID.

```http
GET /messages/{message_id}
Authorization: Bearer <token>
```

**Response 200:** MessageRead object  
**Response 403:** Forbidden (not owner via conversation)  
**Response 404:** Not found

---

### Chat Settings

#### Get Chat Settings

Get user's chat settings, creating defaults if not found.

```http
GET /users/me/chat-settings
Authorization: Bearer <token>
```

**Response 200:**
```json
{
  "id": "uuid",
  "user_id": "string",
  "enabled_tools": ["search", "rag"],
  "enabled_agents": ["uuid1", "uuid2"],
  "model": "gpt-4",
  "temperature": 0.7,
  "max_tokens": 2000,
  "created_at": "2025-12-12T10:00:00Z",
  "updated_at": "2025-12-12T10:00:00Z"
}
```

**Default Values:**
- `enabled_tools`: `[]`
- `enabled_agents`: `[]`
- `model`: `null`
- `temperature`: `0.7`
- `max_tokens`: `2000`

#### Update Chat Settings

Update user's chat settings (upsert operation).

```http
PUT /users/me/chat-settings
Authorization: Bearer <token>
Content-Type: application/json
```

**Request Body:**
```json
{
  "enabled_tools": ["search", "rag"],
  "enabled_agents": ["uuid1"],
  "model": "gpt-4",
  "temperature": 0.5,
  "max_tokens": 1500
}
```

**Validation Rules:**
- `temperature`: 0.0 - 2.0
- `max_tokens`: 1 - 32000

**Response 200:** ChatSettingsRead object  
**Response 422:** Validation error

---

## Database Schema

### Tables

#### conversations
- `id` (UUID, primary key)
- `title` (VARCHAR(255))
- `user_id` (VARCHAR(255), indexed)
- `created_at` (TIMESTAMP)
- `updated_at` (TIMESTAMP)

**Indexes:**
- `idx_conversations_user_id` on `user_id`
- `idx_conversations_created_at` on `created_at`

#### messages
- `id` (UUID, primary key)
- `conversation_id` (UUID, foreign key to conversations, CASCADE DELETE)
- `role` (VARCHAR(50): 'user', 'assistant', 'system')
- `content` (TEXT)
- `attachments` (JSON, nullable)
- `run_id` (UUID, foreign key to run_records, nullable)
- `routing_decision` (JSON, nullable)
- `tool_calls` (JSON, nullable)
- `created_at` (TIMESTAMP)

**Indexes:**
- `idx_messages_conversation_id` on `conversation_id`
- `idx_messages_created_at` on `created_at`
- `idx_messages_run_id` on `run_id`

#### chat_settings
- `id` (UUID, primary key)
- `user_id` (VARCHAR(255), unique, indexed)
- `enabled_tools` (ARRAY of TEXT)
- `enabled_agents` (ARRAY of UUID)
- `model` (VARCHAR(255), nullable)
- `temperature` (FLOAT, default: 0.7)
- `max_tokens` (INTEGER, default: 2000)
- `created_at` (TIMESTAMP)
- `updated_at` (TIMESTAMP)

**Indexes:**
- `idx_chat_settings_user_id` on `user_id` (unique)

---

## Authorization

### Row-Level Security

All conversation and message endpoints enforce row-level security:

1. **Conversations**: Users can only access their own conversations (where `user_id` matches authenticated user)
2. **Messages**: Users can only access messages in their own conversations
3. **Chat Settings**: Users can only access their own settings

### Error Responses

- **401 Unauthorized**: Missing or invalid JWT token
- **403 Forbidden**: User attempting to access another user's resources
- **404 Not Found**: Resource does not exist

---

## Usage Examples

### Example 1: Starting a New Chat Session

```bash
# 1. Create conversation
curl -X POST http://agent-lxc:8000/conversations \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title": "Weather Questions"}'

# Response: {"id": "conv-123", "title": "Weather Questions", ...}

# 2. Send first message
curl -X POST http://agent-lxc:8000/conversations/conv-123/messages \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "role": "user",
    "content": "What is the weather in Paris?"
  }'

# 3. Store assistant response with routing info
curl -X POST http://agent-lxc:8000/conversations/conv-123/messages \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "role": "assistant",
    "content": "The weather in Paris is sunny, 22°C.",
    "run_id": "run-456",
    "routing_decision": {
      "selected_tools": ["weather"],
      "confidence": 0.95,
      "reasoning": "User asking about weather"
    }
  }'
```

### Example 2: Loading Conversation History

```bash
# Get conversation with messages
curl -X GET "http://agent-lxc:8000/conversations/conv-123?include_messages=true" \
  -H "Authorization: Bearer $TOKEN"
```

### Example 3: Managing Chat Settings

```bash
# Get current settings (creates defaults if not found)
curl -X GET http://agent-lxc:8000/users/me/chat-settings \
  -H "Authorization: Bearer $TOKEN"

# Update settings
curl -X PUT http://agent-lxc:8000/users/me/chat-settings \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "enabled_tools": ["search", "rag", "weather"],
    "temperature": 0.8,
    "max_tokens": 3000
  }'
```

---

## Migration

The conversation management system was added in migration `003_add_conversations.py`.

**To apply migration:**
```bash
# On agent-lxc container
cd /srv/agent
source venv/bin/activate
alembic upgrade head
```

**To rollback:**
```bash
alembic downgrade -1
```

---

## Testing

Comprehensive integration tests are available in:
- `tests/integration/test_api_conversations.py`

**Run tests:**
```bash
cd /path/to/busibox/srv/agent
make test-integration
```

**Test coverage includes:**
- Conversation CRUD operations
- Message CRUD operations
- Chat settings management
- Authorization checks (403 Forbidden)
- Pagination
- Cascade deletion
- Timestamp updates

---

## Related Documentation

- [Agent Server API](./agent-server-api.md) - Complete API reference
- [Agent Server Implementation Status](./agent-server-implementation-status.md) - Feature status
- [OpenAPI Specification](../../openapi/agent-api.yaml) - Full OpenAPI spec
- [Agent Client Specs](/Users/wessonnenreich/Code/sonnenreich/agent-client/specs/001-agent-management-rebuild/) - Client requirements

---

## Changelog

### 2025-12-12
- Initial implementation of conversation management
- Added message CRUD endpoints
- Added chat settings management
- Created database migration
- Added comprehensive tests
- Updated OpenAPI specification









