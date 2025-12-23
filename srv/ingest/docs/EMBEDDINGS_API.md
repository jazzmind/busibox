# Embeddings API

FastEmbed embedding generation endpoint for external services.

## Endpoint

```
POST http://ingest-lxc:8002/api/embeddings
```

## Authentication

Requires `X-User-Id` header with user ID.

## Request Format

OpenAI-compatible embeddings API:

```json
{
  "input": "Text to embed" | ["Multiple", "texts", "to embed"],
  "model": "bge-large-en-v1.5",
  "encoding_format": "float"
}
```

## Response Format

```json
{
  "object": "list",
  "data": [
    {
      "object": "embedding",
      "embedding": [0.123, -0.456, ...],
      "index": 0
    }
  ],
  "model": "bge-large-en-v1.5",
  "usage": {
    "prompt_tokens": 10,
    "total_tokens": 10
  }
}
```

## Model Info

- **Model**: BAAI/bge-large-en-v1.5
- **Dimension**: 1024
- **Type**: Dense embeddings
- **Language**: English (optimized)
- **Quality**: High (better than OpenAI ada-002)

## Usage from ai-portal (TypeScript/Next.js)

### Option 1: Direct HTTP Client

```typescript
// lib/embeddings/ingest-client.ts

interface EmbeddingRequest {
  input: string | string[];
  model?: string;
  encoding_format?: string;
}

interface EmbeddingResponse {
  object: string;
  data: Array<{
    object: string;
    embedding: number[];
    index: number;
  }>;
  model: string;
  usage: {
    prompt_tokens: number;
    total_tokens: number;
  };
}

export async function generateEmbeddings(
  input: string | string[],
  userId: string
): Promise<number[][]> {
  const response = await fetch('http://ingest-lxc:8002/api/embeddings', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-User-Id': userId,
    },
    body: JSON.stringify({
      input,
      model: 'bge-large-en-v1.5',
    }),
  });

  if (!response.ok) {
    throw new Error(`Embedding generation failed: ${response.statusText}`);
  }

  const data: EmbeddingResponse = await response.json();
  return data.data.map(item => item.embedding);
}

// Usage in API route
export async function POST(request: Request) {
  const { message, userId } = await request.json();
  
  // Generate embedding for semantic search
  const [embedding] = await generateEmbeddings(message, userId);
  
  // Use embedding for search...
  return Response.json({ embedding });
}
```

### Option 2: OpenAI SDK Compatible

The endpoint is OpenAI-compatible, so you can use the OpenAI SDK:

```typescript
// lib/embeddings/client.ts
import OpenAI from 'openai';

const ingestClient = new OpenAI({
  apiKey: 'not-needed', // Auth via X-User-Id header
  baseURL: 'http://ingest-lxc:8002/api',
});

export async function generateEmbeddings(
  input: string | string[],
  userId: string
): Promise<number[][]> {
  const response = await ingestClient.embeddings.create({
    input,
    model: 'bge-large-en-v1.5',
  }, {
    headers: {
      'X-User-Id': userId,
    },
  });

  return response.data.map(item => item.embedding);
}
```

### Option 3: Shared Utility

```typescript
// lib/embeddings/index.ts

export interface EmbeddingOptions {
  userId: string;
  model?: 'bge-large-en-v1.5';
}

export async function embedText(
  text: string,
  options: EmbeddingOptions
): Promise<number[]> {
  const embeddings = await embedTexts([text], options);
  return embeddings[0];
}

export async function embedTexts(
  texts: string[],
  options: EmbeddingOptions
): Promise<number[][]> {
  const response = await fetch('http://ingest-lxc:8002/api/embeddings', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-User-Id': options.userId,
    },
    body: JSON.stringify({
      input: texts,
      model: options.model || 'bge-large-en-v1.5',
    }),
  });

  if (!response.ok) {
    const error = await response.text();
    throw new Error(`Embedding failed: ${error}`);
  }

  const data = await response.json();
  return data.data.map((item: any) => item.embedding);
}

// Usage in chat handler
import { embedText } from '@/lib/embeddings';

export async function POST(request: Request) {
  const { message, userId } = await request.json();
  
  // Generate embedding for RAG
  const queryEmbedding = await embedText(message, { userId });
  
  // Search with embedding
  const results = await searchDocuments(queryEmbedding, userId);
  
  return Response.json({ results });
}
```

## Performance

- **Latency**: ~50-200ms per request (depending on text length)
- **Throughput**: ~100 requests/second (single worker)
- **Batching**: Supports batch embedding (recommended for >1 text)

## Example Batch Request

```typescript
// Batch multiple texts for efficiency
const texts = [
  "What is machine learning?",
  "Explain neural networks",
  "Deep learning tutorial"
];

const embeddings = await generateEmbeddings(texts, userId);
// Returns: [[...1024 dims], [...1024 dims], [...1024 dims]]
```

## Error Handling

```typescript
try {
  const embedding = await embedText(query, { userId });
} catch (error) {
  if (error instanceof Error) {
    console.error('Embedding failed:', error.message);
    // Fallback: use keyword search instead of semantic
  }
}
```

## Caching Recommendations

For frequently used queries, cache embeddings:

```typescript
// Simple in-memory cache
const embeddingCache = new Map<string, number[]>();

export async function embedTextCached(
  text: string,
  options: EmbeddingOptions
): Promise<number[]> {
  const cacheKey = `${text}:${options.model || 'default'}`;
  
  if (embeddingCache.has(cacheKey)) {
    return embeddingCache.get(cacheKey)!;
  }
  
  const embedding = await embedText(text, options);
  embeddingCache.set(cacheKey, embedding);
  
  return embedding;
}
```

## List Available Models

```bash
curl -X GET http://ingest-lxc:8002/api/embeddings/models \
  -H "X-User-Id: user-123"
```

Response:
```json
{
  "object": "list",
  "data": [
    {
      "id": "bge-large-en-v1.5",
      "object": "model",
      "owned_by": "BAAI",
      "dimension": 1024,
      "description": "High-quality English embeddings (1024-d)"
    }
  ]
}
```

## Deployment

The embeddings endpoint is automatically available when the ingest service is running:

```bash
# Check if API is running
curl http://ingest-lxc:8002/health

# Test embeddings endpoint
curl -X POST http://ingest-lxc:8002/api/embeddings \
  -H "Content-Type: application/json" \
  -H "X-User-Id: test-user" \
  -d '{"input": "Hello world"}'
```

## Environment Variables

No additional configuration needed - uses existing FastEmbed setup:

```bash
FASTEMBED_MODEL=BAAI/bge-large-en-v1.5  # Default
EMBEDDING_BATCH_SIZE=32                  # Default
```

## Migration from External Embedding Service

If you're currently using OpenAI or another embedding service:

1. Replace base URL: `https://api.openai.com/v1` → `http://ingest-lxc:8002/api`
2. Change model: `text-embedding-3-small` → `bge-large-en-v1.5`
3. Update auth: `Authorization: Bearer sk-xxx` → `X-User-Id: user-123`
4. Note dimension change: 1536-d → 1024-d (may need to update vector DB schemas)

## Advantages

✅ **Local**: No external API calls, lower latency  
✅ **Free**: No per-token costs  
✅ **Private**: Data stays in your infrastructure  
✅ **Fast**: CPU-optimized, ~100ms latency  
✅ **Quality**: bge-large outperforms OpenAI ada-002  
✅ **Compatible**: OpenAI-compatible API format  

## Limitations

❌ **English only**: Model is English-optimized (use liteLLM for other languages)  
❌ **Fixed dimension**: 1024-d only (no variable dimensions)  
❌ **Single model**: Only bge-large-en-v1.5 available  
❌ **CPU-bound**: May be slower than GPU-based services for large batches  

## Support

For issues or questions:
- API docs: http://ingest-lxc:8002/docs
- Health check: http://ingest-lxc:8002/health
- Logs: `journalctl -u ingest-api -f`

