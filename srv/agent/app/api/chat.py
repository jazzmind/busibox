"""
Chat API endpoints for conversation-based chat with intelligent routing.

This provides the main chat interface that:
- Manages conversation history
- Routes messages through the dispatcher
- Stores messages in the database
- Supports streaming responses
- Integrates with insights for agent memories
"""

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import get_principal
from app.db.session import get_session
from app.models.domain import Conversation, Message, ChatAttachment, ChatSettings
from app.schemas.auth import Principal
from app.schemas.conversation import Attachment, MessageRead
from app.schemas.dispatcher import DispatcherRequest, FileAttachment, UserSettings, RoutingDecision
from app.services.dispatcher_service import route_query
from app.services.model_selector import select_model_and_tools, list_available_models, ModelCapabilities
from app.services.chat_executor import execute_chat, execute_chat_stream
from app.services.insights_generator import (
    generate_and_store_insights,
    identify_knowledge_gaps,
    should_generate_insights,
)
from app.services.insights_service import ChatInsight
from app.api.insights import get_insights_service
from app.config.settings import get_settings
from app.auth.token_exchange import exchange_token_zero_trust

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])
settings = get_settings()


FILE_ID_FROM_URL_RE = re.compile(r"/files/([^/]+)/download")
BRIDGE_FILTERED_STANDARD_EVENTS = {
    "model_selected",
    "routing_decision",
    "planning",
    "tool_start",
    "tool_result",
    "agent_start",
    "agent_result",
    "agent_response_start",
    "synthesis_start",
}
BRIDGE_FILTERED_AGENTIC_EVENTS = {
    "thought",
    "plan",
    "progress",
    "tool_start",
    "tool_result",
}


def _extract_file_id_from_url(file_url: Optional[str]) -> Optional[str]:
    if not file_url:
        return None
    match = FILE_ID_FROM_URL_RE.search(file_url)
    if not match:
        return None
    return match.group(1)


def _is_bridge_request(metadata: Optional[Dict[str, Any]]) -> bool:
    """Detect bridge-originated chat requests (e.g. Telegram)."""
    if not metadata:
        return False
    bridge_channels = metadata.get("bridge_channels")
    return isinstance(bridge_channels, list) and len(bridge_channels) > 0


async def _generate_insights_background(
    conversation: Conversation,
    messages: List[Message],
    user_id: str,
    user_token: Optional[str] = None
) -> None:
    """
    Background task to generate insights from conversation.
    
    Args:
        conversation: Conversation object
        messages: List of messages
        user_id: User ID for authorization
        user_token: User's auth token for Zero Trust exchange
    """
    try:
        follow_up = await _generate_insights_and_pending_question(
            conversation=conversation,
            messages=messages,
            user_id=user_id,
            user_token=user_token,
        )
        logger.info(
            "Background insights complete for conversation %s (pending_follow_up=%s)",
            conversation.id,
            bool(follow_up),
        )
    except Exception as e:
        logger.error(
            f"Background insights generation failed: {e}",
            extra={"conversation_id": str(conversation.id)},
            exc_info=True
        )


async def _generate_insights_and_pending_question(
    conversation: Conversation,
    messages: List[Message],
    user_id: str,
    user_token: Optional[str] = None,
) -> Optional[str]:
    """
    Generate insights and optionally create one pending follow-up question.

    Returns:
        Question text if a new pending profile question was created, else None.
    """
    try:
        insights_service = get_insights_service()
        embedding_url = settings.embedding_api_url or "http://embedding-api:8005"

        await generate_and_store_insights(
            conversation=conversation,
            messages=messages,
            insights_service=insights_service,
            embedding_service_url=str(embedding_url),
            authorization=None,
        )

        # Evaluate profile gaps against the latest user insights.
        all_insights, _ = insights_service.list_user_insights(
            user_id=user_id,
            limit=250,
        )
        # Refresh pending profile prompts each chat cycle: remove stale unresolved
        # prompts, then compute whether a new question is still needed.
        non_pending_insights: List[Dict[str, Any]] = []
        for insight in all_insights:
            if str(insight.get("category", "")) == "pending_question":
                insight_id = str(insight.get("id", ""))
                if insight_id:
                    try:
                        insights_service.delete_insight(insight_id=insight_id, user_id=user_id)
                    except Exception as exc:
                        logger.warning("Failed to delete stale pending question %s: %s", insight_id, exc)
                continue
            non_pending_insights.append(insight)
        pending_question = await identify_knowledge_gaps(
            conversation=conversation,
            messages=messages,
            user_id=user_id,
            existing_insights=non_pending_insights,
        )
        if not pending_question:
            return None

        embedding = await insights_service.generate_embedding(
            pending_question.content,
            user_id=user_id,
            authorization=user_token,
        )
        pending_insight = ChatInsight(
            id=str(uuid.uuid4()),
            user_id=user_id,
            content=pending_question.content,
            embedding=embedding,
            conversation_id=pending_question.conversation_id,
            analyzed_at=int(datetime.now(timezone.utc).timestamp()),
            category="pending_question",
        )
        insights_service.insert_insights([pending_insight])
        logger.info(
            "Created pending follow-up question insight",
            extra={
                "conversation_id": str(conversation.id),
                "user_id": user_id,
            },
        )
        return pending_question.content
    except Exception as exc:
        logger.warning(
            "Failed to generate pending follow-up question (non-critical): %s",
            exc,
            exc_info=True,
        )
        return None


class ChatMessageRequest(BaseModel):
    """Request to send a chat message."""
    message: str = Field(..., min_length=1, max_length=50000, description="User message")
    conversation_id: Optional[uuid.UUID] = Field(None, description="Conversation ID (creates new if not provided)")
    model: Optional[str] = Field("auto", description="Model selection: 'auto', 'chat', 'research', 'frontier'")
    attachments: Optional[List[Attachment]] = Field(default_factory=list, description="File attachments")
    enable_web_search: bool = Field(False, description="Enable web search tool")
    enable_doc_search: bool = Field(False, description="Enable document search tool")
    selected_agents: Optional[List[str]] = Field(None, description="Specific agent IDs to use (bypasses dispatcher)")
    attachment_ids: Optional[List[uuid.UUID]] = Field(None, description="IDs of uploaded chat attachments")
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0, description="Temperature override")
    max_tokens: Optional[int] = Field(None, ge=1, le=32000, description="Max tokens override")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Application context metadata passed to agent tools (e.g. projectId, appName)")


class ChatMessageResponse(BaseModel):
    """Response from chat message."""
    message_id: uuid.UUID = Field(..., description="ID of the assistant message")
    conversation_id: uuid.UUID = Field(..., description="Conversation ID")
    content: str = Field(..., description="Assistant response")
    model: Optional[str] = Field(None, description="Model used for response")
    routing_decision: Optional[Dict[str, Any]] = Field(None, description="Dispatcher routing decision")
    tool_calls: Optional[List[Dict[str, Any]]] = Field(None, description="Tool calls made")
    run_id: Optional[uuid.UUID] = Field(None, description="Associated run ID if agent was used")


class ChatHistoryResponse(BaseModel):
    """Response with chat history."""
    conversation_id: uuid.UUID
    title: str
    messages: List[MessageRead]
    total_messages: int


class ModelsListResponse(BaseModel):
    """Response with available models."""
    models: List[ModelCapabilities]


# Legacy compatibility models
class ChatRequest(BaseModel):
    """Simple chat request (legacy)."""
    message: str = Field(..., description="User message")
    agentId: Optional[str] = Field(default="default", description="Agent ID (optional)")
    context: Optional[Dict[str, Any]] = Field(default=None, description="Additional context")


class ChatResponse(BaseModel):
    """Simple chat response (legacy)."""
    response: str = Field(..., description="Agent response")
    success: bool = Field(default=True, description="Success flag")


@router.post("/message", response_model=ChatMessageResponse)
async def send_chat_message(
    payload: ChatMessageRequest,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ChatMessageResponse:
    """
    Send a chat message and get a response.
    
    This endpoint:
    1. Creates or retrieves conversation
    2. Stores user message
    3. Routes through dispatcher for intelligent tool/agent selection
    4. Executes selected tools/agents
    5. Stores assistant response
    6. Returns response with metadata
    
    Args:
        payload: Chat message request
        principal: Authenticated user
        session: Database session
        
    Returns:
        ChatMessageResponse with assistant response and metadata
    """
    try:
        # Get or create conversation
        if payload.conversation_id:
            # Verify conversation exists and user owns it
            result = await session.execute(
                select(Conversation).where(
                    Conversation.id == payload.conversation_id,
                    Conversation.user_id == principal.sub
                )
            )
            conversation = result.scalar_one_or_none()
            
            if not conversation:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Conversation {payload.conversation_id} not found"
                )
        else:
            # Create new conversation
            conversation = Conversation(
                title=payload.message[:50] + "..." if len(payload.message) > 50 else payload.message,
                user_id=principal.sub
            )
            session.add(conversation)
            await session.flush()  # Get conversation ID
        
        # Store user message
        user_message = Message(
            conversation_id=conversation.id,
            role="user",
            content=payload.message,
            attachments=[att.model_dump() for att in payload.attachments] if payload.attachments else None
        )
        session.add(user_message)
        await session.flush()
        
        logger.info(
            f"Stored user message in conversation {conversation.id}",
            extra={
                "user_sub": principal.sub,
                "conversation_id": str(conversation.id),
                "message_id": str(user_message.id)
            }
        )
        
        # Get user's chat settings
        settings_result = await session.execute(
            select(ChatSettings).where(ChatSettings.user_id == principal.sub)
        )
        user_settings = settings_result.scalar_one_or_none()
        
        # Build list of enabled tools based on request and user settings
        enabled_tools = []
        if payload.enable_web_search:
            enabled_tools.append("web_search")
        if payload.enable_doc_search:
            enabled_tools.append("doc_search")
        
        # If user has settings, merge with request
        if user_settings:
            # User settings can disable tools
            if user_settings.enabled_tools:
                enabled_tools = [t for t in enabled_tools if t in user_settings.enabled_tools]
        
        # Get conversation history for context
        history_result = await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.asc())
            .limit(20)  # Last 20 messages for context
        )
        history_messages = history_result.scalars().all()
        history_dicts = [
            {"role": msg.role, "content": msg.content}
            for msg in history_messages
        ]
        
        # Auto model selection if requested
        selected_model = payload.model
        model_selection_reasoning = None
        
        # If specific agents are selected, bypass dispatcher and run them directly.
        # This aligns non-streaming behavior with the API contract and streaming endpoint.
        if payload.selected_agents:
            selected_agent_ids: list[str] = []
            for agent_id in payload.selected_agents:
                if agent_id == "test-agent":
                    # Allow friendly alias while keeping execution path UUID-based.
                    selected_agent_ids.append(str(uuid.uuid5(uuid.NAMESPACE_DNS, "busibox.builtin.test-agent")))
                else:
                    selected_agent_ids.append(agent_id)

            from app.schemas.dispatcher import RoutingDecision
            decision = RoutingDecision(
                selected_tools=[],
                selected_agents=selected_agent_ids,
                confidence=1.0,
                reasoning="Bypassed dispatcher: using explicitly selected agents",
                alternatives=[],
                requires_disambiguation=False
            )
            logger.info(
                "Selected agents provided: bypassing dispatcher",
                extra={
                    "user_sub": principal.sub,
                    "conversation_id": str(conversation.id),
                    "selected_agents": selected_agent_ids,
                }
            )
        # Special handling for test mode - bypass dispatcher's LLM call, use test-agent directly
        # This still uses the full agent system (agent prompt, execution) but skips dispatcher analysis
        elif payload.model == "test":
            # Generate deterministic UUID for test-agent (same as builtin_agents.py)
            test_agent_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, "busibox.builtin.test-agent"))
            logger.info(
                "Test mode: bypassing dispatcher LLM, routing directly to test-agent",
                extra={
                    "user_sub": principal.sub,
                    "conversation_id": str(conversation.id),
                    "test_agent_uuid": test_agent_uuid,
                }
            )
            # Create a routing decision that forces the test agent (no LLM call needed)
            from app.schemas.dispatcher import RoutingDecision
            decision = RoutingDecision(
                selected_tools=[],
                selected_agents=[test_agent_uuid],
                confidence=1.0,
                reasoning="Test mode: direct routing to test-agent (no dispatcher LLM)",
                alternatives=[],
                requires_disambiguation=False
            )
            selected_model = "test"  # Ensure we use the test model
        else:
            if payload.model == "auto":
                model_selection = select_model_and_tools(
                    message=payload.message,
                    attachments=[att.model_dump() for att in (payload.attachments or [])],
                    history=history_dicts,
                    user_model_preference=user_settings.model if user_settings else None,
                    enabled_tools=enabled_tools
                )
                
                selected_model = model_selection.model_id
                model_selection_reasoning = model_selection.reasoning
                
                logger.info(
                    f"Auto model selection: {selected_model}",
                    extra={
                        "user_sub": principal.sub,
                        "conversation_id": str(conversation.id),
                        "selected_model": selected_model,
                        "confidence": model_selection.confidence,
                        "reasoning": model_selection_reasoning
                    }
                )
            
            # Route through dispatcher
            dispatcher_request = DispatcherRequest(
                query=payload.message,
                available_tools=["web_search", "doc_search"],
                available_agents=[],  # TODO: Get from agent registry
                attachments=[
                    FileAttachment(name=att.name, type=att.type, url=att.url)
                    for att in (payload.attachments or [])
                ],
                user_settings=UserSettings(
                    enabled_tools=enabled_tools,
                    enabled_agents=user_settings.enabled_agents if user_settings else []
                )
            )
            
            request_id = str(uuid.uuid4())
            
            routing_response = await route_query(
                request=dispatcher_request,
                user_id=principal.sub,
                request_id=request_id,
                session=session
            )
            
            decision = routing_response.routing_decision
        
        logger.info(
            f"Dispatcher routing complete: confidence={decision.confidence:.2f}",
            extra={
                "user_sub": principal.sub,
                "conversation_id": str(conversation.id),
                "selected_tools": decision.selected_tools,
                "selected_agents": decision.selected_agents,
                "confidence": decision.confidence
            }
        )
        
        # Execute tools and agents
        execution_result = await execute_chat(
            query=payload.message,
            routing_decision=decision,
            model=selected_model,
            user_id=principal.sub,
            session=session,
            conversation_history=history_dicts,
            principal=principal
        )
        
        assistant_content = execution_result.content
        tool_calls = execution_result.get_tool_calls_json()
        run_ids = execution_result.get_run_ids()
        primary_run_id = run_ids[0] if run_ids else None
        
        logger.info(
            f"Chat execution complete",
            extra={
                "user_sub": principal.sub,
                "conversation_id": str(conversation.id),
                "tool_count": len(tool_calls),
                "agent_count": len(run_ids)
            }
        )
        
        # Store assistant message
        assistant_message = Message(
            conversation_id=conversation.id,
            role="assistant",
            content=assistant_content,
            routing_decision=decision.model_dump(),  # Model selection is in routing_decision
            tool_calls=tool_calls if tool_calls else None,
            run_id=primary_run_id
        )
        session.add(assistant_message)
        
        # Update conversation timestamp
        conversation.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        
        await session.commit()
        await session.refresh(assistant_message)
        
        logger.info(
            f"Chat message complete",
            extra={
                "user_sub": principal.sub,
                "conversation_id": str(conversation.id),
                "assistant_message_id": str(assistant_message.id)
            }
        )
        
        # Generate insights if conversation is ready
        try:
            # Get message count
            from sqlalchemy import func
            count_result = await session.execute(
                select(func.count()).select_from(Message).where(Message.conversation_id == conversation.id)
            )
            message_count = count_result.scalar_one()
            
            if should_generate_insights(conversation, message_count):
                # Generate insights asynchronously (don't wait)
                asyncio.create_task(
                    _generate_insights_background(
                        conversation,
                        history_messages + [user_message, assistant_message],
                        principal.sub,
                        principal.token  # Zero Trust: pass user's token
                    )
                )
                logger.info(
                    f"Triggered insights generation for conversation {conversation.id}",
                    extra={"conversation_id": str(conversation.id)}
                )
        except Exception as e:
            # Don't fail the request if insights generation fails
            logger.error(f"Failed to trigger insights generation: {e}", exc_info=True)
        
        return ChatMessageResponse(
            message_id=assistant_message.id,
            conversation_id=conversation.id,
            content=assistant_content,
            model=selected_model,  # Return the actual selected model
            routing_decision=decision.model_dump(),
            tool_calls=tool_calls if tool_calls else None,
            run_id=primary_run_id
        )
        
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        logger.error(f"Chat message failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Chat message failed: {str(e)}"
        )


@router.post("/message/stream")
async def send_chat_message_stream(
    payload: ChatMessageRequest,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """
    Send a chat message and stream the response.
    
    This endpoint provides Server-Sent Events (SSE) streaming for real-time responses.
    
    Event types:
    - model_selected: Model selection result
    - routing_decision: Dispatcher routing decision
    - content_chunk: Partial response content
    - tool_call: Tool execution update
    - message_complete: Final message with ID
    - error: Error occurred
    
    Args:
        payload: Chat message request
        principal: Authenticated user
        session: Database session
        
    Returns:
        StreamingResponse with SSE events
    """
    async def generate_events() -> AsyncGenerator[str, None]:
        """Generate SSE events for streaming response."""
        try:
            suppress_thinking_events = _is_bridge_request(payload.metadata)
            # Get or create conversation (same logic as non-streaming)
            if payload.conversation_id:
                result = await session.execute(
                    select(Conversation).where(
                        Conversation.id == payload.conversation_id,
                        Conversation.user_id == principal.sub
                    )
                )
                conversation = result.scalar_one_or_none()
                
                if not conversation:
                    yield f"event: error\ndata: {json.dumps({'error': 'Conversation not found'})}\n\n"
                    return
            else:
                conversation = Conversation(
                    title=payload.message[:50] + "..." if len(payload.message) > 50 else payload.message,
                    user_id=principal.sub
                )
                session.add(conversation)
                await session.flush()
            
            # Store user message
            user_message = Message(
                conversation_id=conversation.id,
                role="user",
                content=payload.message,
                attachments=[att.model_dump() for att in payload.attachments] if payload.attachments else None
            )
            session.add(user_message)
            await session.flush()

            # Load uploaded chat-attachments and link them to the user message
            attachment_metadata: List[Dict[str, Any]] = []
            if payload.attachment_ids:
                attachment_result = await session.execute(
                    select(ChatAttachment).where(ChatAttachment.id.in_(payload.attachment_ids))
                )
                attachments = attachment_result.scalars().all()
                requested_ids = {str(att_id) for att_id in payload.attachment_ids}
                found_ids = {str(att.id) for att in attachments}
                missing_ids = requested_ids - found_ids
                if missing_ids:
                    logger.warning(
                        "Some attachment IDs were not found",
                        extra={
                            "user_sub": principal.sub,
                            "conversation_id": str(conversation.id),
                            "missing_attachment_ids": sorted(missing_ids),
                        },
                    )

                for attachment in attachments:
                    attachment.message_id = user_message.id
                    attachment_metadata.append({
                        "id": str(attachment.id),
                        "file_id": _extract_file_id_from_url(attachment.file_url),
                        "filename": attachment.filename,
                        "mime_type": attachment.mime_type,
                        "file_url": attachment.file_url,
                        "parsed_content": attachment.parsed_content,
                    })
            
            # Get user settings
            settings_result = await session.execute(
                select(ChatSettings).where(ChatSettings.user_id == principal.sub)
            )
            user_settings = settings_result.scalar_one_or_none()
            
            # Get conversation history
            history_result = await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.created_at.asc())
                .limit(20)
            )
            history_messages = history_result.scalars().all()
            history_dicts = [
                {"role": msg.role, "content": msg.content}
                for msg in history_messages
            ]
            
            # Auto model selection
            selected_model = payload.model
            if payload.model == "auto":
                enabled_tools = []
                if payload.enable_web_search:
                    enabled_tools.append("web_search")
                if payload.enable_doc_search:
                    enabled_tools.append("doc_search")
                
                model_selection = select_model_and_tools(
                    message=payload.message,
                    attachments=[att.model_dump() for att in (payload.attachments or [])],
                    history=history_dicts,
                    user_model_preference=user_settings.model if user_settings else None,
                    enabled_tools=enabled_tools
                )
                
                selected_model = model_selection.model_id
                
                # Send model selection event
                if not suppress_thinking_events:
                    yield f"event: model_selected\ndata: {json.dumps(model_selection.model_dump())}\n\n"
            
            # Route through dispatcher
            # If specific agents are selected, use them as available_agents for intelligent routing
            from app.models.domain import AgentDefinition
            
            enabled_tools = []
            if payload.enable_web_search:
                enabled_tools.append("web_search")
            if payload.enable_doc_search:
                enabled_tools.append("doc_search")
            
            if user_settings and user_settings.enabled_tools:
                enabled_tools = [t for t in enabled_tools if t in user_settings.enabled_tools]
            
            # Determine available agents for dispatcher
            # If user selected specific agents, use those as available options
            # Otherwise, use all active agents
            if payload.selected_agents:
                available_agents_for_routing = payload.selected_agents
            else:
                # Get all active agents
                stmt = select(AgentDefinition).where(AgentDefinition.is_active.is_(True))
                result = await session.execute(stmt)
                all_agents = result.scalars().all()
                available_agents_for_routing = [str(agent.id) for agent in all_agents]
            
            dispatcher_request = DispatcherRequest(
                query=payload.message,
                available_tools=["web_search", "doc_search"],
                available_agents=available_agents_for_routing,
                attachments=[
                    FileAttachment(name=att.name, type=att.type, url=att.url)
                    for att in (payload.attachments or [])
                ],
                user_settings=UserSettings(
                    enabled_tools=enabled_tools,
                    enabled_agents=available_agents_for_routing  # Use the determined agents
                )
            )
            
            request_id = str(uuid.uuid4())
            routing_response = await route_query(
                request=dispatcher_request,
                user_id=principal.sub,
                request_id=request_id,
                session=session
            )
            
            decision = routing_response.routing_decision
            
            # Send routing decision event
            if not suppress_thinking_events:
                yield f"event: routing_decision\ndata: {json.dumps(decision.model_dump())}\n\n"
            
            # Execute tools and agents with streaming
            full_content = []
            tool_calls = []
            run_ids = []
            
            async for event in execute_chat_stream(
                query=payload.message,
                routing_decision=decision,
                model=selected_model,
                user_id=principal.sub,
                session=session,
                principal=principal,
                conversation_history=history_dicts
            ):
                event_type = event["type"]
                event_data = event["data"]
                
                # Forward event to client (hide verbose thinking events for bridge channels)
                if not (suppress_thinking_events and event_type in BRIDGE_FILTERED_STANDARD_EVENTS):
                    yield f"event: {event_type}\ndata: {json.dumps(event_data)}\n\n"
                
                # Collect data for storage
                if event_type == "content_chunk":
                    full_content.append(event_data["chunk"])
                elif event_type == "tool_result":
                    tool_calls.append(event_data)
                elif event_type == "agent_result" and event_data.get("run_id"):
                    run_ids.append(event_data["run_id"])
            
            # Combine content
            response_text = "".join(full_content)
            primary_run_id = uuid.UUID(run_ids[0]) if run_ids else None
            
            # Store assistant message
            assistant_message = Message(
                conversation_id=conversation.id,
                role="assistant",
                content=response_text,
                routing_decision=decision.model_dump(),  # Model selection is in routing_decision
                tool_calls=tool_calls if tool_calls else None,
                run_id=primary_run_id
            )
            session.add(assistant_message)
            
            conversation.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            
            await session.commit()
            await session.refresh(assistant_message)
            
            # Send completion event
            completion_data = {
                'message_id': str(assistant_message.id),
                'conversation_id': str(conversation.id),
                'model': selected_model
            }
            yield f"event: message_complete\ndata: {json.dumps(completion_data)}\n\n"
            
        except Exception as e:
            logger.error(f"Streaming chat failed: {e}", exc_info=True)
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
    
    return StreamingResponse(
        generate_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # Disable nginx buffering
        }
    )


@router.post("/message/stream/agentic")
async def send_chat_message_stream_agentic(
    payload: ChatMessageRequest,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """
    Send a chat message using the agentic dispatcher with real-time streaming.
    
    This endpoint provides a more interactive experience where:
    - The dispatcher explains what it's doing in real-time
    - Agents stream their thoughts and tool usage
    - Users can see the research process as it happens
    
    Event types:
    - thought: Dispatcher/agent reasoning (for collapsible thinking section)
    - tool_start: Starting a tool execution
    - tool_result: Tool completed with result
    - content: Final response content (streams to chat message)
    - complete: Execution finished
    - error: Error occurred
    
    Args:
        payload: Chat message request
        principal: Authenticated user
        session: Database session
        
    Returns:
        StreamingResponse with SSE events
    """
    from app.services.agentic_dispatcher import run_agentic_dispatcher
    
    # Create cancellation event
    cancel_event = asyncio.Event()
    
    async def generate_events() -> AsyncGenerator[str, None]:
        """Generate SSE events from agentic dispatcher."""
        import time as _time
        _t_request = _time.monotonic()
        logger.info(
            "Agentic chat request started",
            extra={
                "user_id": principal.sub,
                "message_preview": payload.message[:80],
                "conversation_id": str(payload.conversation_id) if payload.conversation_id else None,
                "agent_id": payload.agent_id if hasattr(payload, 'agent_id') else None,
                "selected_agents": payload.selected_agents if hasattr(payload, 'selected_agents') else None,
            }
        )
        try:
            suppress_thinking_events = _is_bridge_request(payload.metadata)
            # Get or create conversation
            title_updated = False
            if payload.conversation_id:
                result = await session.execute(
                    select(Conversation).where(
                        Conversation.id == payload.conversation_id,
                        Conversation.user_id == principal.sub
                    )
                )
                conversation = result.scalar_one_or_none()
                
                if not conversation:
                    yield f"event: error\ndata: {json.dumps({'error': 'Conversation not found'})}\n\n"
                    return
                
                # Update title if it's still the default "New Conversation"
                if conversation.title == "New Conversation":
                    generated_title = payload.message[:50] + "..." if len(payload.message) > 50 else payload.message
                    conversation.title = generated_title
                    title_updated = True
                    # Send title update event
                    yield f"event: title_update\ndata: {json.dumps({'conversation_id': str(conversation.id), 'title': generated_title})}\n\n"
            else:
                # Generate title from first message (truncate to 50 chars)
                generated_title = payload.message[:50] + "..." if len(payload.message) > 50 else payload.message
                conversation = Conversation(
                    title=generated_title,
                    user_id=principal.sub
                )
                session.add(conversation)
                await session.flush()
                
                # Send conversation created event with title
                yield f"event: conversation_created\ndata: {json.dumps({'conversation_id': str(conversation.id), 'title': generated_title})}\n\n"
            
            # Store user message
            user_message = Message(
                conversation_id=conversation.id,
                role="user",
                content=payload.message,
                attachments=[att.model_dump() for att in payload.attachments] if payload.attachments else None
            )
            session.add(user_message)
            await session.flush()

            # Load uploaded chat-attachments and link them to the user message
            attachment_metadata: List[Dict[str, Any]] = []
            if payload.attachment_ids:
                attachment_result = await session.execute(
                    select(ChatAttachment).where(ChatAttachment.id.in_(payload.attachment_ids))
                )
                attachments = attachment_result.scalars().all()
                requested_ids = {str(att_id) for att_id in payload.attachment_ids}
                found_ids = {str(att.id) for att in attachments}
                missing_ids = requested_ids - found_ids
                if missing_ids:
                    logger.warning(
                        "Some attachment IDs were not found",
                        extra={
                            "user_sub": principal.sub,
                            "conversation_id": str(conversation.id),
                            "missing_attachment_ids": sorted(missing_ids),
                        },
                    )

                for attachment in attachments:
                    attachment.message_id = user_message.id
                    attachment_metadata.append({
                        "id": str(attachment.id),
                        "file_id": _extract_file_id_from_url(attachment.file_url),
                        "filename": attachment.filename,
                        "mime_type": attachment.mime_type,
                        "file_url": attachment.file_url,
                        "parsed_content": attachment.parsed_content,
                    })
            
            # Get conversation history
            history_result = await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.created_at.asc())
                .limit(20)
            )
            history_messages = history_result.scalars().all()
            history_dicts = [
                {"role": msg.role, "content": msg.content}
                for msg in history_messages
            ]
            
            # Determine available agents
            # Default to chat agent only - it's the versatile general-purpose agent
            # that can use tools (web search, documents, etc.) when needed
            if payload.selected_agents:
                available_agents = payload.selected_agents
            else:
                available_agents = ["chat"]
            
            # Collect content for storing
            full_content = []
            thoughts = []
            run_events = []
            selected_agent_id = None
            
            # Run agentic dispatcher
            dispatcher_metadata: Dict[str, Any] = dict(payload.metadata or {})
            dispatcher_metadata["conversation_id"] = str(conversation.id)
            async for event in run_agentic_dispatcher(
                query=payload.message,
                user_id=principal.sub,
                session=session,
                cancel=cancel_event,
                available_agents=available_agents,
                conversation_history=history_dicts,
                principal=principal,
                metadata=dispatcher_metadata,
                attachment_metadata=attachment_metadata,
            ):
                # Yield event to client (hide verbose thinking events for bridge channels)
                if not (suppress_thinking_events and event.type in BRIDGE_FILTERED_AGENTIC_EVENTS):
                    yield f"event: {event.type}\ndata: {event.model_dump_json()}\n\n"
                
                # Collect content and thoughts
                if event.type == "content":
                    full_content.append(event.message)
                elif event.type in ("thought", "tool_start", "tool_result", "plan", "progress"):
                    thought_item = {
                        "type": event.type,
                        "source": event.source,
                        "message": event.message,
                    }
                    # Persist structured intent-routing diagnostics for later analysis.
                    if (
                        event.type == "thought"
                        and isinstance(event.data, dict)
                        and event.data.get("phase") == "intent_routing"
                    ):
                        thought_item["data"] = {
                            "phase": "intent_routing",
                            "action_type": event.data.get("action_type"),
                            "needs_tools": event.data.get("needs_tools"),
                            "confidence": event.data.get("confidence"),
                            "routing_source": event.data.get("routing_source"),
                            "follow_up_question": event.data.get("follow_up_question"),
                        }
                    thoughts.append(thought_item)
                
                # Build run event log for RunRecord
                run_events.append({
                    "type": event.type,
                    "source": event.source,
                    "message": event.message[:500] if event.message else "",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                
                # Capture the selected agent ID from dispatcher routing
                if event.data and isinstance(event.data, dict):
                    if "selected_agent" in event.data:
                        selected_agent_id = event.data["selected_agent"]
            
            # Store assistant message
            # Join without separator - content chunks are already properly formatted
            response_text = "".join(full_content) if full_content else "No response generated."
            
            assistant_message = Message(
                conversation_id=conversation.id,
                role="assistant",
                content=response_text,
                routing_decision={
                    "thoughts": thoughts,
                    "selected_agents": available_agents,
                } if thoughts or available_agents else None,
            )
            session.add(assistant_message)
            
            conversation.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            
            # Create a RunRecord so this chat shows in agent API logs
            try:
                from app.models.domain import RunRecord, AgentDefinition
                
                agent_uuid = None
                if selected_agent_id:
                    try:
                        agent_uuid = uuid.UUID(selected_agent_id)
                    except (ValueError, TypeError):
                        # Name-based agent, look up UUID
                        agent_name = selected_agent_id
                        agent_row = (await session.execute(
                            select(AgentDefinition).where(AgentDefinition.name == agent_name)
                        )).scalar_one_or_none()
                        if agent_row:
                            agent_uuid = agent_row.id
                
                if not agent_uuid:
                    # Fallback: look up "chat" agent by name
                    chat_row = (await session.execute(
                        select(AgentDefinition).where(AgentDefinition.name == "chat")
                    )).scalar_one_or_none()
                    if chat_row:
                        agent_uuid = chat_row.id
                
                if agent_uuid:
                    elapsed_s = round((_time.monotonic() - _t_request), 2)
                    run_record = RunRecord(
                        agent_id=agent_uuid,
                        status="completed",
                        input={"prompt": payload.message, "source": "chat", "conversation_id": str(conversation.id)},
                        output={"response": response_text[:2000]},
                        events=run_events[-50:],
                        created_by=principal.sub,
                    )
                    session.add(run_record)
            except Exception as run_err:
                logger.warning(f"Failed to create chat RunRecord (non-critical): {run_err}")
            
            await session.commit()
            await session.refresh(assistant_message)

            # Trigger insights generation + pending follow-up question for the agentic path.
            pending_follow_up_question: Optional[str] = None
            try:
                from sqlalchemy import func
                count_result = await session.execute(
                    select(func.count()).select_from(Message).where(Message.conversation_id == conversation.id)
                )
                message_count = count_result.scalar_one()
                if should_generate_insights(conversation, message_count):
                    pending_follow_up_question = await _generate_insights_and_pending_question(
                        conversation=conversation,
                        messages=history_messages + [user_message, assistant_message],
                        user_id=principal.sub,
                        user_token=principal.token,
                    )
            except Exception as exc:
                logger.error("Failed to trigger agentic insights generation: %s", exc, exc_info=True)

            if pending_follow_up_question:
                interim_payload = {
                    "type": "interim",
                    "source": "insights",
                    "message": pending_follow_up_question,
                    "data": {
                        "kind": "profile_follow_up",
                        "bridge_channels": payload.metadata.get("bridge_channels", []) if payload.metadata else [],
                    },
                }
                yield f"event: interim\ndata: {json.dumps(interim_payload)}\n\n"

            # Online eval: sample a percentage of production conversations for
            # background LLM quality grading (fire-and-forget).
            try:
                from app.services.eval_runner import sample_online_eval
                asyncio.ensure_future(
                    sample_online_eval(
                        session=session,
                        conversation_id=conversation.id,
                        message_id=assistant_message.id,
                        query=payload.message,
                        response=response_text,
                        agent_id=selected_agent_id,
                        user_id=principal.sub,
                    )
                )
            except Exception as _eval_exc:
                logger.debug(f"Online eval hook skipped: {_eval_exc}")
            
            # Send completion event with message ID
            completion_data = {
                'message_id': str(assistant_message.id),
                'conversation_id': str(conversation.id),
            }
            yield f"event: message_complete\ndata: {json.dumps(completion_data)}\n\n"
            
            logger.info(
                "Agentic chat request complete",
                extra={
                    "total_ms": round((_time.monotonic() - _t_request) * 1000),
                    "conversation_id": str(conversation.id),
                    "response_length": len(response_text),
                }
            )
            
        except asyncio.CancelledError:
            logger.info(
                "Agentic chat cancelled by client",
                extra={"elapsed_ms": round((_time.monotonic() - _t_request) * 1000)},
            )
            cancel_event.set()
        except Exception as e:
            logger.error(
                f"Agentic streaming chat failed: {e}",
                extra={"elapsed_ms": round((_time.monotonic() - _t_request) * 1000)},
                exc_info=True,
            )
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
    
    return StreamingResponse(
        generate_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # Disable nginx buffering
        }
    )


@router.post("/{conversation_id}/generate-insights")
async def generate_conversation_insights(
    conversation_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """
    Manually trigger insights generation for a conversation.
    
    This endpoint allows users to manually generate insights from a conversation.
    Normally, insights are generated automatically after conversations reach
    a certain length.
    
    Args:
        conversation_id: Conversation ID
        principal: Authenticated user
        session: Database session
        
    Returns:
        Dict with insights count
    """
    try:
        # Verify conversation exists and user owns it
        result = await session.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == principal.sub
            )
        )
        conversation = result.scalar_one_or_none()
        
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Conversation {conversation_id} not found"
            )
        
        # Get messages
        messages_result = await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
        )
        messages = list(messages_result.scalars().all())
        
        if len(messages) < 2:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Conversation must have at least 2 messages to generate insights"
            )
        
        # Generate insights
        insights_service = get_insights_service()
        
        # Zero Trust: Exchange user's token for data-api audience
        data_token = None
        if principal.token:
            data_token = await exchange_token_zero_trust(
                subject_token=principal.token,
                target_audience="data-api",
                user_id=principal.sub
            )
        
        # Use dedicated embedding-api service (no auth required)
        embedding_url = settings.embedding_api_url or "http://embedding-api:8005"
        
        new_count, existing_count = await generate_and_store_insights(
            conversation=conversation,
            messages=messages,
            insights_service=insights_service,
            embedding_service_url=str(embedding_url),
            authorization=None  # embedding-api doesn't require auth
        )
        
        total_count = new_count + existing_count
        
        logger.info(
            f"Manually generated {new_count} new insights for conversation {conversation_id} ({existing_count} already existed)",
            extra={
                "user_sub": principal.sub,
                "conversation_id": str(conversation_id),
                "new_insight_count": new_count,
                "existing_insight_count": existing_count
            }
        )
        
        return {
            "conversation_id": str(conversation_id),
            "insights_generated": new_count,
            "existing_insights": existing_count,
            "total_insights": total_count,
            "message": f"Generated {new_count} new insights from conversation ({existing_count} already existed)"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to generate insights: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate insights: {str(e)}"
        )


@router.get("/models", response_model=ModelsListResponse)
async def list_models(
    principal: Principal = Depends(get_principal),
) -> ModelsListResponse:
    """
    List available models with their capabilities.
    
    Returns all models that can be selected for chat, including:
    - Model ID and name
    - Capabilities (vision, tools, reasoning)
    - Performance characteristics (speed, cost)
    - Token limits
    
    Args:
        principal: Authenticated user
        
    Returns:
        ModelsListResponse with available models
    """
    models = list_available_models()
    
    logger.info(
        f"Listed {len(models)} available models",
        extra={"user_sub": principal.sub, "model_count": len(models)}
    )
    
    return ModelsListResponse(models=models)


@router.get("/{conversation_id}/history", response_model=ChatHistoryResponse)
async def get_chat_history(
    conversation_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
    limit: int = 100,
) -> ChatHistoryResponse:
    """
    Get chat history for a conversation.
    
    Args:
        conversation_id: Conversation ID
        principal: Authenticated user
        session: Database session
        limit: Maximum number of messages to return
        
    Returns:
        ChatHistoryResponse with conversation and messages
    """
    try:
        # Verify conversation exists and user owns it
        result = await session.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == principal.sub
            )
        )
        conversation = result.scalar_one_or_none()
        
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Conversation {conversation_id} not found"
            )
        
        # Get messages
        messages_result = await session.execute(
            select(Message)
            .options(selectinload(Message.chat_attachments))
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
            .limit(limit)
        )
        messages = [MessageRead.model_validate(msg) for msg in messages_result.scalars().all()]
        
        # Get total count
        from sqlalchemy import func
        count_result = await session.execute(
            select(func.count()).select_from(Message).where(Message.conversation_id == conversation_id)
        )
        total = count_result.scalar_one()
        
        logger.info(
            f"Retrieved chat history for conversation {conversation_id}",
            extra={
                "user_sub": principal.sub,
                "conversation_id": str(conversation_id),
                "message_count": len(messages),
                "total_messages": total
            }
        )
        
        return ChatHistoryResponse(
            conversation_id=conversation.id,
            title=conversation.title,
            messages=messages,
            total_messages=total
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get chat history: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get chat history: {str(e)}"
        )


# ========== Legacy Compatibility Endpoints ==========

@router.get("/api/slow-endpoint")
async def slow_endpoint():
    """
    Test endpoint that simulates a slow response.
    Used for testing timeout handling in clients.
    """
    import asyncio
    await asyncio.sleep(5)  # Sleep for 5 seconds
    return {"message": "This endpoint is intentionally slow"}


@router.post("/api/chat", response_model=ChatResponse)
async def chat_legacy(
    payload: ChatRequest,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ChatResponse:
    """
    Simple chat endpoint for backward compatibility (DEPRECATED).
    
    **DEPRECATED**: Use POST /chat/message instead for full conversation support.
    
    This endpoint provides a simplified interface that:
    1. Routes the query through the dispatcher
    2. Returns a simple text response
    3. Does NOT store conversation history
    
    For conversation-based chat with history, use /chat/message.
    For advanced features, use /dispatcher/route or /runs endpoints directly.
    
    Args:
        payload: Chat request with message and optional context
        principal: Authenticated user
        session: Database session
        
    Returns:
        ChatResponse with agent response
        
    Raises:
        HTTPException: 400 if request is invalid
    """
    try:
        logger.info(
            f"Legacy chat request from user {principal.sub}: {payload.message[:50]}...",
            extra={
                "user_sub": principal.sub,
                "agent_id": payload.agentId,
                "message_length": len(payload.message),
            },
        )
        
        # Use dispatcher to route the query
        dispatcher_request = DispatcherRequest(
            query=payload.message,
            available_tools=["web_search", "doc_search"],
            available_agents=[],
            attachments=[],
            user_settings=UserSettings(
                enabled_tools=["web_search", "doc_search"],
                enabled_agents=[]
            ),
        )
        
        # Generate request ID for tracing
        request_id = str(uuid.uuid4())
        
        # Route query
        routing_response = await route_query(
            request=dispatcher_request,
            user_id=principal.sub,
            request_id=request_id,
            session=session,
        )
        
        # Return simple response based on routing decision
        decision = routing_response.routing_decision
        response_text = (
            f"Query routed to: {', '.join(decision.selected_tools or ['none'])}. "
            f"Confidence: {decision.confidence:.2f}. "
            f"Reasoning: {decision.reasoning}"
        )
        
        logger.info(
            f"Legacy chat response generated for user {principal.sub}",
            extra={
                "user_sub": principal.sub,
                "response_length": len(response_text),
                "confidence": decision.confidence,
            },
        )
        
        return ChatResponse(
            response=response_text,
            success=True,
        )
        
    except Exception as e:
        logger.error(f"Legacy chat request failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Chat request failed: {str(e)}",
        )


# =============================================================================
# MESSAGE DELETION
# =============================================================================

@router.delete("/{conversation_id}/messages/{message_id}")
async def delete_message(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """
    Delete a message from a conversation.
    
    Only the conversation owner can delete messages.
    """
    # Verify conversation exists and user owns it
    result = await session.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == principal.sub,
        )
    )
    conversation = result.scalar_one_or_none()
    
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found or you don't have permission to modify it",
        )
    
    # Find and delete the message
    result = await session.execute(
        select(Message).where(
            Message.id == message_id,
            Message.conversation_id == conversation_id,
        )
    )
    message = result.scalar_one_or_none()
    
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found",
        )
    
    await session.delete(message)
    await session.commit()
    
    logger.info(
        f"Message {message_id} deleted from conversation {conversation_id} by user {principal.sub}"
    )
    
    return {"success": True, "message_id": str(message_id)}

