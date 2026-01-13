"""
Base class for streaming-aware agents.

Provides a framework for agents that can stream their thoughts and progress
directly to the user in real-time.
"""

import asyncio
from abc import ABC, abstractmethod
from typing import Awaitable, Callable, Optional

from app.schemas.streaming import StreamEvent, thought, content, error


# Type alias for the streaming callback
StreamCallback = Callable[[StreamEvent], Awaitable[None]]


class StreamingAgent(ABC):
    """
    Base class for agents that stream thoughts to the user.
    
    Subclasses implement run_with_streaming() to execute their logic
    while streaming progress updates via the provided callback.
    """
    
    # Override in subclass
    name: str = "Agent"
    
    @abstractmethod
    async def run_with_streaming(
        self,
        query: str,
        stream: StreamCallback,
        cancel: asyncio.Event,
        context: Optional[dict] = None,
    ) -> str:
        """
        Execute agent logic, streaming thoughts as we go.
        
        Args:
            query: The user's query to process
            stream: Async callback to stream events to the user
            cancel: Event that signals cancellation request
            context: Optional context from dispatcher (conversation history, etc.)
            
        Returns:
            Final output string (also streamed via 'content' events)
        """
        pass
    
    async def stream_thought(self, stream: StreamCallback, message: str, data: dict = None) -> None:
        """Helper to stream a thought event."""
        await stream(thought(source=self.name, message=message, data=data))
    
    async def stream_content(self, stream: StreamCallback, message: str, data: dict = None) -> None:
        """Helper to stream content to the chat message."""
        await stream(content(source=self.name, message=message, data=data))
    
    async def stream_error(self, stream: StreamCallback, message: str, data: dict = None) -> None:
        """Helper to stream an error event."""
        await stream(error(source=self.name, message=message, data=data))
    
    def is_cancelled(self, cancel: asyncio.Event) -> bool:
        """Check if execution should be cancelled."""
        return cancel.is_set()


class SimpleStreamingAgent(StreamingAgent):
    """
    A simple streaming agent that wraps an existing PydanticAI agent.
    
    Useful for converting existing agents to the streaming pattern
    without rewriting their core logic.
    """
    
    def __init__(self, name: str, pydantic_agent, description: str = ""):
        self.name = name
        self.pydantic_agent = pydantic_agent
        self.description = description
    
    async def run_with_streaming(
        self,
        query: str,
        stream: StreamCallback,
        cancel: asyncio.Event,
        context: Optional[dict] = None,
    ) -> str:
        """
        Run the wrapped PydanticAI agent with basic streaming.
        
        Note: This doesn't provide fine-grained streaming of the agent's
        internal tool calls - for that, use a custom StreamingAgent subclass.
        """
        if self.is_cancelled(cancel):
            return ""
        
        await self.stream_thought(stream, f"Processing your request...")
        
        try:
            # Run the PydanticAI agent
            result = await self.pydantic_agent.run(query)
            
            if self.is_cancelled(cancel):
                return ""
            
            # Extract output
            output = str(result.output) if hasattr(result, 'output') else str(result)
            
            # Stream the content
            await self.stream_content(stream, output)
            
            return output
            
        except Exception as e:
            await self.stream_error(stream, f"Error: {str(e)}")
            return f"Error: {str(e)}"
