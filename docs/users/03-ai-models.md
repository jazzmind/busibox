---
title: "AI Models"
category: "platform"
order: 3
description: "How Busibox uses AI models to power your experience"
published: true
---

# AI Models

AI models are the brains behind Busibox — they power chat responses, document processing, and search. You don't need to configure anything; your administrator sets this up. Here's what matters for you as a user.

## What AI Models Do in Busibox

Models are used in three main ways:

- **Chat responses** — When you ask an agent a question, a model generates the answer. It may search your documents first, then synthesize a response.
- **Document processing** — Some documents (especially complex PDFs or images) use AI to extract text and structure. This helps make them searchable.
- **Search** — Your documents are turned into "embeddings" — numerical representations that capture meaning. When you search, the system finds documents whose meaning is closest to your question.

## Local vs Cloud Models

Busibox can use models in two ways:

- **Local models** — Run on your organization's own hardware. Your data never leaves your network. Responses can be fast, and there are no external API costs. Best for simple questions and sensitive content.
- **Cloud models** — Run on services like OpenAI or AWS Bedrock. They're often more capable for complex reasoning, summarization, or creative tasks. Your admin decides when these are used and what data (if any) is sent.

Your admin configures which models are available. You just use the platform; the right model is chosen for each task.

## You Don't Configure Anything

As a user, you don't pick models or change settings. Your administrator:

- Chooses which local and cloud models to enable
- Configures when each is used (e.g., fast local for simple queries, powerful cloud for complex ones)
- Manages API keys and access

You simply chat, search, and upload. The system handles the rest.

## Different Tasks, Different Models

Behind the scenes, different tasks may use different models:

- **Quick answers** — A faster, lighter model might handle simple questions.
- **Complex reasoning** — A more powerful model might be used for summaries, analysis, or multi-step questions.
- **Document extraction** — Specialized models may process images or complex layouts.

This is all automatic. You get the best available option for each task without having to think about it.

## Embeddings Explained Simply

When you upload a document, the system doesn't just store the text. It creates "embeddings" — mathematical representations of the meaning of each chunk. Think of it like a map: similar ideas end up close together.

When you search or ask a question, your query is also turned into an embedding. The system finds the document chunks whose embeddings are closest to your query. That's why you can ask "What are the main risks?" and get results even if the document says "key risk factors" — the meaning matches.

You never see embeddings directly. They just make search and chat more accurate and natural.
