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
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_principal
from app.db.session import get_session
from app.models.domain import Conversation, Message, ChatSettings
from app.schemas.auth import Principal
from app.schemas.conversation import Attachment, MessageRead
from app.schemas.dispatcher import DispatcherRequest, FileAttachment, UserSettings, RoutingDecision
from app.services.dispatcher_service import route_query
from app.services.model_selector import select_model_and_tools, list_available_models, ModelCapabilities
from app.services.chat_executor import execute_chat, execute_chat_stream
from app.services.insights_generator import generate_and_store_insights, should_generate_insights
from app.api.insights import get_insights_service
from app.config.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])
settings = get_settings()


async def _generate_insights_background(
    conversation: Conversation,
    messages: List[Message],
    user_id: str
) -> None:
    """
    Background task to generate insights from conversation.
    
    Args:
        conversation: Conversation object
        messages: List of messages
        user_id: User ID for authorization
    """
    try:
        insights_service = get_insights_service()
        
        await generate_and_store_insights(
            conversation=conversation,
            messages=messages,
            insights_service=insights_service,
            embedding_service_url=str(settings.ingest_api_url),
            authorization=None  # TODO: Pass user token if needed
        )
    except Exception as e:
        logger.error(
            f"Background insights generation failed: {e}",
            extra={"conversation_id": str(conversation.id)},
            exc_info=True
        )


class ChatMessageRequest(BaseModel):
    """Request to send a chat message."""
    message: str = Field(..., min_length=1, max_length=10000, description="User message")
    conversation_id: Optional[uuid.UUID] = Field(None, description="Conversation ID (creates new if not provided)")
    model: Optional[str] = Field("auto", description="Model selection: 'auto', 'chat', 'research', 'frontier'")
    attachments: Optional[List[Attachment]] = Field(default_factory=list, description="File attachments")
    enable_web_search: bool = Field(False, description="Enable web search tool")
    enable_doc_search: bool = Field(False, description="Enable document search tool")
    selected_agents: Optional[List[str]] = Field(None, description="Specific agent IDs to use (bypasses dispatcher)")
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0, description="Temperature override")
    max_tokens: Optional[int] = Field(None, ge=1, le=32000, description="Max tokens override")


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
            conversation_history=history_dicts
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
                        principal.sub
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
                
                # Forward event to client
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
        try:
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
            available_agents = ["web_search", "chat"]
            if payload.selected_agents:
                available_agents = payload.selected_agents
            
            # Collect content for storing
            full_content = []
            thoughts = []
            
            # Run agentic dispatcher
            async for event in run_agentic_dispatcher(
                query=payload.message,
                user_id=principal.sub,
                session=session,
                cancel=cancel_event,
                available_agents=available_agents,
                conversation_history=history_dicts,
                principal=principal,
            ):
                # Yield event to client
                yield f"event: {event.type}\ndata: {event.model_dump_json()}\n\n"
                
                # Collect content and thoughts
                if event.type == "content":
                    full_content.append(event.message)
                elif event.type in ("thought", "tool_start", "tool_result"):
                    thoughts.append({
                        "type": event.type,
                        "source": event.source,
                        "message": event.message,
                    })
            
            # Store assistant message
            # Join without separator - content chunks are already properly formatted
            response_text = "".join(full_content) if full_content else "No response generated."
            
            assistant_message = Message(
                conversation_id=conversation.id,
                role="assistant",
                content=response_text,
                routing_decision={
                    "thoughts": thoughts,
                    "selected_agents": available_agents,  # Store which agents were used
                } if thoughts or available_agents else None,
            )
            session.add(assistant_message)
            
            conversation.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            
            await session.commit()
            await session.refresh(assistant_message)
            
            # Send completion event with message ID
            completion_data = {
                'message_id': str(assistant_message.id),
                'conversation_id': str(conversation.id),
            }
            yield f"event: message_complete\ndata: {json.dumps(completion_data)}\n\n"
            
        except asyncio.CancelledError:
            logger.info("Agentic chat cancelled by client")
            cancel_event.set()
        except Exception as e:
            logger.error(f"Agentic streaming chat failed: {e}", exc_info=True)
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
        
        insight_count = await generate_and_store_insights(
            conversation=conversation,
            messages=messages,
            insights_service=insights_service,
            embedding_service_url=str(settings.ingest_api_url),
            authorization=None
        )
        
        logger.info(
            f"Manually generated {insight_count} insights for conversation {conversation_id}",
            extra={
                "user_sub": principal.sub,
                "conversation_id": str(conversation_id),
                "insight_count": insight_count
            }
        )
        
        return {
            "conversation_id": str(conversation_id),
            "insights_generated": insight_count,
            "message": f"Generated {insight_count} insights from conversation"
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

