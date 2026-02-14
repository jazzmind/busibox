---
title: "Agent Service Overview"
category: "developer"
order: 40
description: "Agent API overview - chat orchestration, tools, and workflows"
published: true
---

# Agent Service Overview

The Agent API (`srv/agent`) provides AI agent execution, tool orchestration, workflow management, and intelligent query routing. It runs on `agent-lxc` (port 8000) and integrates with Search API for RAG, LiteLLM for synthesis, and Data API for attachments.

## Key Capabilities

- **Chat orchestration** — RAG, web search, and attachment agents with streaming SSE responses
- **Agent definitions** — Built-in and custom agents with configurable models and tools
- **Workflows** — Multi-step processes with conditional logic
- **Tools** — Document search, web search, and custom tools

## Documentation

| Doc | Content |
|-----|---------|
| [02-architecture](02-architecture.md) | System design, components, Pydantic AI integration |
| [03-api](03-api.md) | REST API reference, endpoints, authentication |
| [04-testing](04-testing.md) | How to run and debug agent tests |

## Quick Reference

- **Base URL**: `http://agent-lxc:8000`
- **Auth**: JWT Bearer (audience `agent-api`)
- **Key paths**: `/chat/message`, `/agents`, `/conversations`, `/runs`, `/agents/workflows`
