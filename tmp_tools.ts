/**
 * AI Tools for LLM Function Calling
 * 
 * Defines tools that the LLM can call during conversations
 */

import { searchDocuments } from '@/lib/chat/search';
import type { ChatCompletionTool } from 'openai/resources/chat/completions';

// Agent API configuration - for web search tool execution
const AGENT_API_URL = process.env.AGENT_API_URL || process.env.NEXT_PUBLIC_AGENT_API_URL || 'http://localhost:8000';

// Built-in tool UUIDs (deterministic based on tool name)
// Generated via: uuid.uuid5(uuid.NAMESPACE_DNS, "busibox.builtin.tool.<name>")
const WEB_SEARCH_TOOL_ID = '86b5b33f-046a-56e2-81b2-089fc12de4e6';

/**
 * Web Search Tool
 * Allows the LLM to search the web for current information
 */
export const webSearchTool: ChatCompletionTool = {
  type: 'function',
  function: {
    name: 'web_search',
    description: 'Search the web for current information, news, or facts. Use this when you need up-to-date information that you don\'t have in your training data, or when the user asks about recent events, current news, or real-time information.',
    parameters: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: 'The search query. Be specific and include relevant keywords.',
        },
        maxResults: {
          type: 'number',
          description: 'Maximum number of results to return (default: 5)',
          default: 5,
        },
      },
      required: ['query'],
    },
  },
};

/**
 * Document Search Tool
 * Allows the LLM to search through the user's document library for relevant information
 */
export const documentSearchTool: ChatCompletionTool = {
  type: 'function',
  function: {
    name: 'search_documents',
    description: 'Search through the user\'s document library for relevant information. Use this when you need to find information from documents the user has uploaded or stored in their library. This searches across all documents using semantic search with re-ranking for the most relevant results.',
    parameters: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: 'The search query. Be specific and include relevant keywords to find the most relevant document sections.',
        },
        limit: {
          type: 'number',
          description: 'Maximum number of results to return (default: 5)',
          default: 5,
        },
      },
      required: ['query'],
    },
  },
};

/**
 * Execute web search tool via agent-api
 * 
 * Calls the agent-api's tool test endpoint which supports Perplexity
 * and other web search providers with proper API key management.
 * 
 * Uses the built-in web_search tool via /agents/tools/{tool_id}/test
 */
export async function executeWebSearch(
  query: string,
  userId: string,
  maxResults: number = 5,
  authorization?: string
): Promise<{
  results: Array<{
    title: string;
    url: string;
    snippet: string;
  }>;
  error?: string;
}> {
  try {
    console.log(`[Tool] Executing web search via agent-api: "${query}"`);
    
    // Build headers - require JWT authorization for agent-api
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'X-User-Id': userId,
    };
    
    if (authorization) {
      headers['Authorization'] = authorization;
    } else {
      console.warn('[Tool] No authorization provided for web search - agent-api may reject the request');
    }
    
    // Call agent-api tool test endpoint for web_search
    const response = await fetch(`${AGENT_API_URL}/agents/tools/${WEB_SEARCH_TOOL_ID}/test`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        input: {
          query,
          max_results: maxResults,
        },
      }),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ detail: 'Unknown error' }));
      console.error('[Tool] Web search failed:', response.status, errorData);
      throw new Error(errorData.detail || errorData.error || `Web search failed with status ${response.status}`);
    }

    const data = await response.json();
    
    // Handle test endpoint response format
    if (!data.success) {
      console.error('[Tool] Web search tool execution failed:', data.error);
      return {
        results: [],
        error: data.error || 'Web search execution failed',
      };
    }
    
    const output = data.output || {};
    console.log(`[Tool] Web search completed. Results: ${output.result_count || 0}, Providers: ${data.providers_used?.join(', ') || output.providers_used?.join(', ') || 'unknown'}`);

    // Handle agent-api response format (from output.results)
    if (output.error) {
      return {
        results: [],
        error: output.error,
      };
    }

    // Map agent-api results to expected format
    return {
      results: (output.results || []).map((r: any) => ({
        title: r.title || '',
        url: r.url || '',
        snippet: r.snippet || r.content || '',
      })),
    };
  } catch (error: any) {
    console.error('[Tool] Web search execution failed:', error);
    return {
      results: [],
      error: error.message || 'Web search failed',
    };
  }
}

/**
 * Execute document search tool
 * 
 * @param query - Search query
 * @param userId - User ID
 * @param limit - Max results (default 5)
 * @param authorization - JWT Authorization header for RLS (optional)
 */
export async function executeDocumentSearch(
  query: string,
  userId: string,
  limit: number = 5,
  authorization?: string
): Promise<{
  results: Array<{
    id: string;
    title: string;
    snippet: string;
    source: string;
    url?: string;
    score: number;
  }>;
  error?: string;
}> {
  try {
    const result = await searchDocuments(query, userId, {
      limit,
      mode: 'hybrid',
      authorization, // Pass JWT for RLS enforcement
    });
    
    if (result.error) {
      return {
        results: [],
        error: result.error,
      };
    }
    
    return {
      results: result.results.map(r => ({
        id: r.id,
        title: r.title,
        snippet: r.snippet,
        source: r.source,
        url: r.url,
        score: r.score,
      })),
    };
  } catch (error: any) {
    console.error('[Tool] Document search execution failed:', error);
    return {
      results: [],
      error: error.message || 'Document search failed',
    };
  }
}

/**
 * Get all available tools based on configuration
 */
export async function getAvailableTools(options: {
  webSearchEnabled?: boolean;
  documentSearchEnabled?: boolean;
}): Promise<ChatCompletionTool[]> {
  const tools: ChatCompletionTool[] = [];
  
  if (options.webSearchEnabled) {
    tools.push(webSearchTool);
  }
  
  if (options.documentSearchEnabled) {
    tools.push(documentSearchTool);
  }
  
  return tools;
}

/**
 * Execute a tool call
 * 
 * @param toolName - Name of the tool to execute
 * @param args - Tool arguments
 * @param userId - User ID
 * @param authorization - JWT Authorization header for RLS (optional)
 */
export async function executeTool(
  toolName: string,
  args: any,
  userId: string,
  authorization?: string
): Promise<any> {
  switch (toolName) {
    case 'web_search':
      return executeWebSearch(args.query, userId, args.maxResults, authorization);
    
    case 'search_documents':
      return executeDocumentSearch(args.query, userId, args.limit, authorization);
    
    default:
      throw new Error(`Unknown tool: ${toolName}`);
  }
}
