/**
 * Chat Web Search API
 * 
 * POST /api/chat/search/web - Search the web for chat context
 * 
 * Uses agent-api's web_search tool via executeWebSearch for Perplexity support.
 */

import { NextRequest } from 'next/server';
import { requireAuth } from '@/lib/middleware';
import {
  errorResponse,
  successResponse,
  validateRequired,
  withErrorHandling,
  ChatErrorCodes,
} from '@/lib/chat/middleware';
import { executeWebSearch } from '@/lib/ai/tools';

// Simple in-memory cache for search results (5 minutes TTL)
const searchCache = new Map<string, { data: any; expires: number }>();
const CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes

function getCacheKey(query: string, maxResults?: number): string {
  return `web:${query}:${maxResults || 5}`;
}

function getCachedResult(key: string): any | null {
  const cached = searchCache.get(key);
  if (!cached) return null;
  
  if (Date.now() > cached.expires) {
    searchCache.delete(key);
    return null;
  }
  
  return cached.data;
}

function setCachedResult(key: string, data: any): void {
  searchCache.set(key, {
    data,
    expires: Date.now() + CACHE_TTL_MS,
  });
}

/**
 * POST /api/chat/search/web
 * 
 * Search the web for chat context
 * 
 * Body:
 * - query: string (required) - Search query
 * - maxResults?: number - Max results (default: 5)
 * - includeAnswer?: boolean - Include AI-generated answer (default: true)
 */
export const POST = withErrorHandling(
  async (request: NextRequest) => {
    // Check authentication
    const authResult = await requireAuth(request);
    if (authResult instanceof Response) {
      return authResult;
    }
    const { user } = authResult;

    // Parse body
    const body = await request.json();

    // Validate required fields
    const validation = validateRequired(body, ['query']);
    if (!validation.valid) {
      return errorResponse(
        `Missing required fields: ${validation.missing?.join(', ')}`,
        400,
        ChatErrorCodes.MISSING_REQUIRED_FIELD,
        { missing: validation.missing }
      );
    }

    const { query, maxResults, includeAnswer } = body;

    // Check cache
    const cacheKey = getCacheKey(query, maxResults);
    const cached = getCachedResult(cacheKey);
    if (cached) {
      return successResponse(cached);
    }

    // Get authorization header for agent-api call
    const authorization = request.headers.get('authorization') || undefined;

    // Perform search via agent-api
    const result = await executeWebSearch(
      query,
      user.id,
      maxResults || 5,
      authorization
    );

    // Cache successful results
    if (!result.error && result.results.length > 0) {
      setCachedResult(cacheKey, {
        results: result.results,
        provider: 'agent-api',
      });
    }

    // Return error if search failed
    if (result.error) {
      return errorResponse(
        result.error,
        503,
        ChatErrorCodes.SEARCH_UNAVAILABLE,
        { provider: 'agent-api' }
      );
    }

    return successResponse({
      results: result.results,
      provider: 'agent-api',
    });
  }
);
