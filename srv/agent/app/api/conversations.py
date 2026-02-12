"""
API endpoints for conversation and message management.

Provides:
- GET /conversations: List user's conversations with pagination
- POST /conversations: Create a new conversation
- GET /conversations/{conversation_id}: Get conversation with messages
- PATCH /conversations/{conversation_id}: Update conversation title
- DELETE /conversations/{conversation_id}: Delete conversation and messages
- GET /conversations/{conversation_id}/messages: List messages with pagination
- POST /conversations/{conversation_id}/messages: Create a new message
- GET /messages/{message_id}: Get a single message
- GET /users/me/chat-settings: Get user's chat settings
- PUT /users/me/chat-settings: Update user's chat settings
"""

import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import get_principal
from app.db.session import get_session
from app.models.domain import ChatAttachment, ChatSettings, Conversation, ConversationShare, Message
from app.schemas.auth import Principal
from app.schemas.conversation import (
    ChatAttachmentCreate,
    ChatAttachmentRead,
    ChatSettingsRead,
    ChatSettingsUpdate,
    ConversationCreate,
    ConversationListResponse,
    ConversationRead,
    ConversationShareCreate,
    ConversationShareListResponse,
    ConversationShareRead,
    ConversationUpdate,
    ConversationWithMessages,
    MessageCreate,
    MessageListResponse,
    MessagePreview,
    MessageRead,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["conversations"])


# ========== Helper Functions ==========

async def get_conversation_or_404(
    conversation_id: uuid.UUID,
    session: AsyncSession,
    user_id: str,
    allow_shared: bool = True,
) -> Conversation:
    """Get conversation by ID and verify ownership or shared access.
    
    Args:
        allow_shared: If True, also allows access for users the conversation is shared with.
    """
    result = await session.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()
    
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found"
        )
    
    if conversation.user_id == user_id:
        return conversation
    
    # Check if user has shared access
    if allow_shared:
        share_result = await session.execute(
            select(ConversationShare).where(
                ConversationShare.conversation_id == conversation_id,
                ConversationShare.user_id == user_id,
            )
        )
        share = share_result.scalar_one_or_none()
        if share:
            return conversation
    
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You do not have access to this conversation"
    )


async def get_message_or_404(
    message_id: uuid.UUID,
    session: AsyncSession,
    user_id: str,
) -> Message:
    """Get message by ID and verify ownership via conversation"""
    result = await session.execute(
        select(Message)
        .options(selectinload(Message.conversation), selectinload(Message.chat_attachments))
        .where(Message.id == message_id)
    )
    message = result.scalar_one_or_none()
    
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Message {message_id} not found"
        )
    
    if message.conversation.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this message"
        )
    
    return message


# ========== Conversation Endpoints ==========

@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(50, ge=1, le=100, description="Number of conversations to return"),
    offset: int = Query(0, ge=0, description="Number of conversations to skip"),
    order_by: str = Query("created_at", description="Field to order by"),
    order: str = Query("desc", pattern="^(asc|desc)$", description="Sort order"),
    agent_id: Optional[str] = Query(None, description="Filter by agent ID (conversations where agent was used)"),
    source: Optional[str] = Query(None, description="Filter by source app (e.g., 'ai-portal', 'agent-manager')"),
) -> ConversationListResponse:
    """
    List user's conversations with pagination and ordering.
    
    Returns conversations with message count and last message preview.
    
    If agent_id is provided, only returns conversations where a message
    has this agent in its routing_decision.selected_agents list.
    
    If source is provided, only returns conversations created by that app/client.
    """
    try:
        from sqlalchemy import and_, or_
        
        # Base filter: owned OR shared with user
        shared_conv_subquery = (
            select(ConversationShare.conversation_id)
            .where(ConversationShare.user_id == principal.sub)
        )
        base_filter = or_(
            Conversation.user_id == principal.sub,
            Conversation.id.in_(shared_conv_subquery),
        )
        
        # Filter by source if provided
        if source:
            base_filter = and_(base_filter, Conversation.source == source)
        
        # If agent_id is provided, find conversations with messages that used this agent
        if agent_id:
            # Subquery to find conversation IDs with messages that used this agent
            # We check if the agent_id is in routing_decision.selected_agents
            from sqlalchemy import and_, cast, String
            from sqlalchemy.dialects.postgresql import JSONB
            
            # Find messages where routing_decision contains the agent_id
            agent_conversations_subquery = (
                select(Message.conversation_id)
                .where(
                    Message.routing_decision.isnot(None)
                )
                .distinct()
            )
            
            # Get all matching conversation IDs first
            conv_ids_result = await session.execute(agent_conversations_subquery)
            potential_conv_ids = [row[0] for row in conv_ids_result.fetchall()]
            
            # Filter to conversations where routing_decision.selected_agents contains agent_id
            matching_conv_ids = []
            for conv_id in potential_conv_ids:
                # Get messages for this conversation
                msg_query = select(Message).where(
                    and_(
                        Message.conversation_id == conv_id,
                        Message.routing_decision.isnot(None)
                    )
                )
                msg_result = await session.execute(msg_query)
                messages = msg_result.scalars().all()
                
                for msg in messages:
                    routing = msg.routing_decision
                    if routing and isinstance(routing, dict):
                        selected_agents = routing.get('selected_agents', [])
                        if agent_id in selected_agents:
                            matching_conv_ids.append(conv_id)
                            break
            
            # Filter to matching conversations owned by user
            if not matching_conv_ids:
                return ConversationListResponse(
                    conversations=[],
                    total=0,
                    limit=limit,
                    offset=offset
                )
            
            base_filter = and_(base_filter, Conversation.id.in_(matching_conv_ids))
        
        # Get total count
        count_query = select(func.count()).select_from(Conversation).where(base_filter)
        total_result = await session.execute(count_query)
        total = total_result.scalar_one()
        
        # Build query
        query = (
            select(Conversation)
            .where(base_filter)
            .offset(offset)
            .limit(limit)
        )
        
        # Apply ordering
        if order_by == "created_at":
            order_field = Conversation.created_at
        elif order_by == "updated_at":
            order_field = Conversation.updated_at
        else:
            order_field = Conversation.created_at
        
        if order == "desc":
            query = query.order_by(order_field.desc())
        else:
            query = query.order_by(order_field.asc())
        
        # Execute query
        result = await session.execute(query)
        conversations = result.scalars().all()
        
        # Build response with message counts and last messages
        conversation_reads = []
        for conv in conversations:
            # Get message count
            msg_count_query = select(func.count()).select_from(Message).where(
                Message.conversation_id == conv.id
            )
            msg_count_result = await session.execute(msg_count_query)
            message_count = msg_count_result.scalar_one()
            
            # Get last message
            last_msg_query = (
                select(Message)
                .where(Message.conversation_id == conv.id)
                .order_by(Message.created_at.desc())
                .limit(1)
            )
            last_msg_result = await session.execute(last_msg_query)
            last_message = last_msg_result.scalar_one_or_none()
            
            # Build preview
            last_message_preview = None
            if last_message:
                content_preview = last_message.content[:100] if len(last_message.content) > 100 else last_message.content
                last_message_preview = MessagePreview(
                    role=last_message.role,
                    content=content_preview,
                    created_at=last_message.created_at
                )
            
            conversation_reads.append(
                ConversationRead(
                    id=conv.id,
                    title=conv.title,
                    user_id=conv.user_id,
                    source=getattr(conv, 'source', None),
                    model=getattr(conv, 'model', None),
                    is_private=getattr(conv, 'is_private', False),
                    agent_id=getattr(conv, 'agent_id', None),
                    message_count=message_count,
                    last_message=last_message_preview,
                    created_at=conv.created_at,
                    updated_at=conv.updated_at
                )
            )
        
        logger.info(
            f"Listed {len(conversation_reads)} conversations for user {principal.sub}",
            extra={"user_sub": principal.sub, "total": total, "agent_id_filter": agent_id, "source_filter": source}
        )
        
        return ConversationListResponse(
            conversations=conversation_reads,
            total=total,
            limit=limit,
            offset=offset
        )
        
    except Exception as e:
        logger.error(f"Failed to list conversations: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list conversations"
        )


@router.post("/conversations", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: ConversationCreate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ConversationRead:
    """
    Create a new conversation.
    
    If no title is provided, defaults to "New Conversation".
    Source can be set to identify which app/client created the conversation.
    """
    try:
        title = payload.title if payload.title else "New Conversation"
        
        conversation = Conversation(
            title=title,
            user_id=principal.sub,
            source=payload.source,
            model=payload.model,
            is_private=payload.is_private,
            agent_id=payload.agent_id,
        )
        
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)
        
        logger.info(
            f"Created conversation {conversation.id} for user {principal.sub} (source: {payload.source})",
            extra={"conversation_id": str(conversation.id), "user_sub": principal.sub, "source": payload.source}
        )
        
        return ConversationRead(
            id=conversation.id,
            title=conversation.title,
            user_id=conversation.user_id,
            source=conversation.source,
            model=conversation.model,
            is_private=conversation.is_private,
            agent_id=conversation.agent_id,
            message_count=0,
            last_message=None,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at
        )
        
    except Exception as e:
        await session.rollback()
        logger.error(f"Failed to create conversation: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create conversation"
        )


@router.get("/conversations/{conversation_id}", response_model=ConversationWithMessages)
async def get_conversation(
    conversation_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
    include_messages: bool = Query(True, description="Include messages in response"),
    message_limit: int = Query(100, ge=1, le=500, description="Max messages to return"),
    message_offset: int = Query(0, ge=0, description="Message offset for pagination"),
) -> ConversationWithMessages:
    """
    Get a conversation by ID with optional messages.
    
    Returns conversation details with paginated messages.
    """
    try:
        conversation = await get_conversation_or_404(conversation_id, session, principal.sub)
        
        messages: List[MessageRead] = []
        if include_messages:
            msg_query = (
                select(Message)
                .options(selectinload(Message.chat_attachments))
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.asc())
                .offset(message_offset)
                .limit(message_limit)
            )
            msg_result = await session.execute(msg_query)
            messages = [MessageRead.model_validate(msg) for msg in msg_result.scalars().all()]
        
        logger.info(
            f"Retrieved conversation {conversation_id} with {len(messages)} messages",
            extra={"conversation_id": str(conversation_id), "message_count": len(messages)}
        )
        
        return ConversationWithMessages(
            id=conversation.id,
            title=conversation.title,
            user_id=conversation.user_id,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            messages=messages
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get conversation: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get conversation"
        )


@router.patch("/conversations/{conversation_id}", response_model=ConversationRead)
async def update_conversation(
    conversation_id: uuid.UUID,
    payload: ConversationUpdate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ConversationRead:
    """
    Update a conversation's title.
    """
    try:
        conversation = await get_conversation_or_404(conversation_id, session, principal.sub, allow_shared=False)
        
        if payload.title is not None:
            conversation.title = payload.title
        if payload.is_private is not None:
            conversation.is_private = payload.is_private
        if payload.model is not None:
            conversation.model = payload.model
        
        await session.commit()
        await session.refresh(conversation)
        
        # Get message count for response
        msg_count_query = select(func.count()).select_from(Message).where(
            Message.conversation_id == conversation.id
        )
        msg_count_result = await session.execute(msg_count_query)
        message_count = msg_count_result.scalar_one()
        
        logger.info(
            f"Updated conversation {conversation_id}",
            extra={"conversation_id": str(conversation_id)}
        )
        
        return ConversationRead(
            id=conversation.id,
            title=conversation.title,
            user_id=conversation.user_id,
            source=conversation.source,
            model=conversation.model,
            is_private=conversation.is_private,
            agent_id=conversation.agent_id,
            message_count=message_count,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at
        )
        
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        logger.error(f"Failed to update conversation: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update conversation"
        )


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> None:
    """
    Delete a conversation and all its messages.
    
    Messages are cascade deleted automatically.
    """
    try:
        conversation = await get_conversation_or_404(conversation_id, session, principal.sub)
        
        await session.delete(conversation)
        await session.commit()
        
        logger.info(
            f"Deleted conversation {conversation_id}",
            extra={"conversation_id": str(conversation_id)}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        logger.error(f"Failed to delete conversation: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete conversation"
        )


# ========== Message Endpoints ==========

@router.get("/conversations/{conversation_id}/messages", response_model=MessageListResponse)
async def list_messages(
    conversation_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(100, ge=1, le=500, description="Number of messages to return"),
    offset: int = Query(0, ge=0, description="Number of messages to skip"),
    order: str = Query("asc", pattern="^(asc|desc)$", description="Sort order by created_at"),
) -> MessageListResponse:
    """
    List messages in a conversation with pagination.
    """
    try:
        # Verify conversation ownership
        await get_conversation_or_404(conversation_id, session, principal.sub)
        
        # Get total count
        count_query = select(func.count()).select_from(Message).where(
            Message.conversation_id == conversation_id
        )
        total_result = await session.execute(count_query)
        total = total_result.scalar_one()
        
        # Build query
        query = (
            select(Message)
            .options(selectinload(Message.chat_attachments))
            .where(Message.conversation_id == conversation_id)
            .offset(offset)
            .limit(limit)
        )
        
        if order == "desc":
            query = query.order_by(Message.created_at.desc())
        else:
            query = query.order_by(Message.created_at.asc())
        
        # Execute query
        result = await session.execute(query)
        messages = [MessageRead.model_validate(msg) for msg in result.scalars().all()]
        
        logger.info(
            f"Listed {len(messages)} messages for conversation {conversation_id}",
            extra={"conversation_id": str(conversation_id), "total": total}
        )
        
        return MessageListResponse(
            messages=messages,
            total=total,
            limit=limit,
            offset=offset
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list messages: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list messages"
        )


@router.post("/conversations/{conversation_id}/messages", response_model=MessageRead, status_code=status.HTTP_201_CREATED)
async def create_message(
    conversation_id: uuid.UUID,
    payload: MessageCreate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> MessageRead:
    """
    Create a new message in a conversation.
    """
    try:
        # Verify conversation ownership (or editor share access)
        conversation = await get_conversation_or_404(conversation_id, session, principal.sub)
        
        # If user is not owner, check they have editor share
        if conversation.user_id != principal.sub:
            share_result = await session.execute(
                select(ConversationShare).where(
                    ConversationShare.conversation_id == conversation_id,
                    ConversationShare.user_id == principal.sub,
                    ConversationShare.role == 'editor',
                )
            )
            if not share_result.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You do not have edit access to this conversation"
                )
        
        # Create message
        message = Message(
            conversation_id=conversation_id,
            role=payload.role,
            content=payload.content,
            attachments=[att.model_dump() for att in payload.attachments] if payload.attachments else None,
            metadata_json=payload.metadata,
            run_id=payload.run_id,
            routing_decision=payload.routing_decision,
            tool_calls=payload.tool_calls,
        )
        
        session.add(message)
        
        # Update conversation updated_at timestamp
        from datetime import datetime, timezone
        conversation.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        
        await session.commit()
        
        # Link attachment IDs if provided
        if payload.attachment_ids:
            for att_id in payload.attachment_ids:
                att_result = await session.execute(
                    select(ChatAttachment).where(ChatAttachment.id == att_id)
                )
                attachment = att_result.scalar_one_or_none()
                if attachment:
                    attachment.message_id = message.id
            await session.commit()
        
        # Re-fetch message with chat_attachments eagerly loaded
        result = await session.execute(
            select(Message)
            .options(selectinload(Message.chat_attachments))
            .where(Message.id == message.id)
        )
        message = result.scalar_one()
        
        logger.info(
            f"Created message {message.id} in conversation {conversation_id}",
            extra={"message_id": str(message.id), "conversation_id": str(conversation_id)}
        )
        
        return MessageRead.model_validate(message)
        
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        logger.error(f"Failed to create message: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create message"
        )


@router.get("/messages/{message_id}", response_model=MessageRead)
async def get_message(
    message_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> MessageRead:
    """
    Get a single message by ID.
    """
    try:
        message = await get_message_or_404(message_id, session, principal.sub)
        
        logger.info(
            f"Retrieved message {message_id}",
            extra={"message_id": str(message_id)}
        )
        
        return MessageRead.model_validate(message)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get message: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get message"
        )


# ========== Conversation Sharing Endpoints ==========

@router.post("/conversations/{conversation_id}/shares", response_model=ConversationShareRead, status_code=status.HTTP_201_CREATED)
async def share_conversation(
    conversation_id: uuid.UUID,
    payload: ConversationShareCreate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ConversationShareRead:
    """Share a conversation with another user. Only the owner can share."""
    try:
        # Only owner can share
        conversation = await get_conversation_or_404(conversation_id, session, principal.sub, allow_shared=False)
        
        if payload.user_id == principal.sub:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot share with yourself")
        
        # Check if already shared
        existing = await session.execute(
            select(ConversationShare).where(
                ConversationShare.conversation_id == conversation_id,
                ConversationShare.user_id == payload.user_id,
            )
        )
        share = existing.scalar_one_or_none()
        
        if share:
            # Update role
            share.role = payload.role
            await session.commit()
            await session.refresh(share)
        else:
            share = ConversationShare(
                conversation_id=conversation_id,
                user_id=payload.user_id,
                role=payload.role,
                shared_by=principal.sub,
            )
            session.add(share)
            await session.commit()
            await session.refresh(share)
        
        return ConversationShareRead.model_validate(share)
        
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        logger.error(f"Failed to share conversation: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to share conversation")


@router.get("/conversations/{conversation_id}/shares", response_model=ConversationShareListResponse)
async def list_conversation_shares(
    conversation_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ConversationShareListResponse:
    """List all shares for a conversation. Owner or shared users can view."""
    try:
        await get_conversation_or_404(conversation_id, session, principal.sub)
        
        result = await session.execute(
            select(ConversationShare)
            .where(ConversationShare.conversation_id == conversation_id)
            .order_by(ConversationShare.shared_at.desc())
        )
        shares = [ConversationShareRead.model_validate(s) for s in result.scalars().all()]
        
        return ConversationShareListResponse(shares=shares)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list shares: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to list shares")


@router.delete("/conversations/{conversation_id}/shares/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unshare_conversation(
    conversation_id: uuid.UUID,
    user_id: str,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Remove sharing for a user. Only the owner can unshare."""
    try:
        await get_conversation_or_404(conversation_id, session, principal.sub, allow_shared=False)
        
        result = await session.execute(
            select(ConversationShare).where(
                ConversationShare.conversation_id == conversation_id,
                ConversationShare.user_id == user_id,
            )
        )
        share = result.scalar_one_or_none()
        
        if not share:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Share not found")
        
        await session.delete(share)
        await session.commit()
        
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        logger.error(f"Failed to unshare conversation: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to unshare conversation")


# ========== Chat Attachment Endpoints ==========

@router.post("/chat-attachments", response_model=ChatAttachmentRead, status_code=status.HTTP_201_CREATED)
async def create_chat_attachment(
    payload: ChatAttachmentCreate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ChatAttachmentRead:
    """Create a chat attachment (initially unlinked to a message)."""
    try:
        attachment = ChatAttachment(
            filename=payload.filename,
            file_url=payload.file_url,
            mime_type=payload.mime_type,
            size_bytes=payload.size_bytes,
            added_to_library=payload.added_to_library,
            library_document_id=payload.library_document_id,
            parsed_content=payload.parsed_content,
        )
        session.add(attachment)
        await session.commit()
        await session.refresh(attachment)
        
        return ChatAttachmentRead.model_validate(attachment)
    except Exception as e:
        await session.rollback()
        logger.error(f"Failed to create attachment: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create attachment")


@router.get("/chat-attachments/{attachment_id}", response_model=ChatAttachmentRead)
async def get_chat_attachment(
    attachment_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ChatAttachmentRead:
    """Get a chat attachment by ID."""
    try:
        result = await session.execute(
            select(ChatAttachment).where(ChatAttachment.id == attachment_id)
        )
        attachment = result.scalar_one_or_none()
        
        if not attachment:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
        
        # If linked to a message, verify conversation access
        if attachment.message_id:
            msg_result = await session.execute(
                select(Message).options(selectinload(Message.conversation)).where(Message.id == attachment.message_id)
            )
            message = msg_result.scalar_one_or_none()
            if message:
                await get_conversation_or_404(message.conversation_id, session, principal.sub)
        
        return ChatAttachmentRead.model_validate(attachment)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get attachment: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get attachment")


@router.delete("/chat-attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat_attachment(
    attachment_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a chat attachment."""
    try:
        result = await session.execute(
            select(ChatAttachment).where(ChatAttachment.id == attachment_id)
        )
        attachment = result.scalar_one_or_none()
        
        if not attachment:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
        
        await session.delete(attachment)
        await session.commit()
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        logger.error(f"Failed to delete attachment: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete attachment")


# ========== Chat Settings Endpoints ==========

@router.get("/users/me/chat-settings", response_model=ChatSettingsRead)
async def get_chat_settings(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ChatSettingsRead:
    """
    Get user's chat settings, creating defaults if not found.
    """
    try:
        result = await session.execute(
            select(ChatSettings).where(ChatSettings.user_id == principal.sub)
        )
        settings = result.scalar_one_or_none()
        
        # Create default settings if not found
        if not settings:
            settings = ChatSettings(
                user_id=principal.sub,
                enabled_tools=[],
                enabled_agents=[],
                temperature=0.7,
                max_tokens=2000
            )
            session.add(settings)
            await session.commit()
            await session.refresh(settings)
            
            logger.info(
                f"Created default chat settings for user {principal.sub}",
                extra={"user_sub": principal.sub}
            )
        
        return ChatSettingsRead.model_validate(settings)
        
    except Exception as e:
        logger.error(f"Failed to get chat settings: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get chat settings"
        )


@router.put("/users/me/chat-settings", response_model=ChatSettingsRead)
async def update_chat_settings(
    payload: ChatSettingsUpdate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ChatSettingsRead:
    """
    Update user's chat settings (upsert operation).
    """
    try:
        result = await session.execute(
            select(ChatSettings).where(ChatSettings.user_id == principal.sub)
        )
        settings = result.scalar_one_or_none()
        
        # Create if not exists
        if not settings:
            settings = ChatSettings(user_id=principal.sub)
            session.add(settings)
        
        # Update fields
        if payload.enabled_tools is not None:
            settings.enabled_tools = payload.enabled_tools
        if payload.enabled_agents is not None:
            settings.enabled_agents = payload.enabled_agents
        if payload.model is not None:
            settings.model = payload.model
        if payload.temperature is not None:
            settings.temperature = payload.temperature
        if payload.max_tokens is not None:
            settings.max_tokens = payload.max_tokens
        
        await session.commit()
        await session.refresh(settings)
        
        logger.info(
            f"Updated chat settings for user {principal.sub}",
            extra={"user_sub": principal.sub}
        )
        
        return ChatSettingsRead.model_validate(settings)
        
    except Exception as e:
        await session.rollback()
        logger.error(f"Failed to update chat settings: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update chat settings"
        )









