"""
Bridge Main Application

Entry point for the Bridge service.  Runs two concurrent co-processes:

1. **FastAPI HTTP server** — email sending, health check (always runs)
2. **Signal bot** — polls for Signal messages and forwards to Agent API
   (only runs when signal_enabled=True and signal_phone_number is set)
"""

import asyncio
import logging
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List

from .agent_client import AgentClient
from .config import Settings, get_settings
from .signal_client import SignalClient, SignalMessage

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)


class RateLimiter:
    """Simple rate limiter for message processing."""

    def __init__(self, max_messages: int, window_seconds: int):
        self.max_messages = max_messages
        self.window_seconds = window_seconds
        self._messages: Dict[str, List[datetime]] = defaultdict(list)

    def is_allowed(self, sender: str) -> bool:
        """Check if sender is within rate limits."""
        now = datetime.now()
        cutoff = now - timedelta(seconds=self.window_seconds)

        # Clean old entries
        self._messages[sender] = [
            ts for ts in self._messages[sender] if ts > cutoff
        ]

        # Check limit
        if len(self._messages[sender]) >= self.max_messages:
            return False

        # Record this message
        self._messages[sender].append(now)
        return True


class SignalBot:
    """
    Main Signal bot application.
    
    Handles:
    - Message polling from Signal
    - Rate limiting
    - Message processing via Agent API
    - Response delivery back to Signal
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.rate_limiter = RateLimiter(
            max_messages=settings.rate_limit_messages,
            window_seconds=settings.rate_limit_window,
        )
        self._running = False

    async def process_message(
        self,
        message: SignalMessage,
        signal_client: SignalClient,
        agent_client: AgentClient,
    ):
        """
        Process a single incoming message.
        
        Args:
            message: Incoming Signal message
            signal_client: Signal API client
            agent_client: Agent API client
        """
        sender = message.sender
        text = message.message.strip()

        if not text:
            return

        # Check allowed phone numbers
        allowed = self.settings.get_allowed_phone_numbers()
        if allowed and sender not in allowed:
            logger.warning(f"Rejected message from unauthorized sender: {sender[:6]}...")
            return

        # Check rate limit
        if not self.rate_limiter.is_allowed(sender):
            logger.warning(f"Rate limited sender: {sender[:6]}...")
            await signal_client.send_message(
                sender,
                "⏳ You're sending messages too quickly. Please wait a moment.",
            )
            return

        # Handle special commands
        if text.lower() == "/help":
            await self._send_help(sender, signal_client)
            return

        if text.lower() == "/new":
            # Clear conversation to start fresh
            agent_client._conversations.pop(sender, None)
            await signal_client.send_message(
                sender,
                "🔄 Started a new conversation. How can I help you?",
            )
            return

        # Send typing indicator
        await signal_client.send_typing_indicator(sender)

        try:
            # Process with Agent API
            if self.settings.debug:
                # Use streaming in debug mode
                response = await self._process_streaming(
                    text, sender, signal_client, agent_client
                )
            else:
                # Use non-streaming for cleaner responses
                response = await self._process_simple(
                    text, sender, agent_client
                )

            # Send response (split if too long)
            await self._send_response(sender, response, signal_client)

        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
            await signal_client.send_message(
                sender,
                "❌ Sorry, I encountered an error processing your message. Please try again.",
            )

        finally:
            # Stop typing indicator
            await signal_client.send_typing_indicator(sender, stop=True)

    async def _process_simple(
        self,
        text: str,
        sender: str,
        agent_client: AgentClient,
    ) -> str:
        """Process message with non-streaming API."""
        response = await agent_client.chat_message(
            message=text,
            sender=sender,
            enable_web_search=self.settings.enable_web_search,
            enable_doc_search=self.settings.enable_doc_search,
            model=self.settings.default_model,
        )
        return response.content

    async def _process_streaming(
        self,
        text: str,
        sender: str,
        signal_client: SignalClient,
        agent_client: AgentClient,
    ) -> str:
        """Process message with streaming API."""
        content_parts = []
        thoughts = []

        async for event in agent_client.chat_message_stream(
            message=text,
            sender=sender,
            enable_web_search=self.settings.enable_web_search,
            enable_doc_search=self.settings.enable_doc_search,
            model=self.settings.default_model,
        ):
            event_type = event.get("_event_type", "")

            if event_type == "content":
                content_parts.append(event.get("message", ""))
            elif event_type == "thought":
                thoughts.append(event.get("message", ""))
            elif event_type == "tool_start":
                tool_name = event.get("message", "tool")
                logger.debug(f"Tool started: {tool_name}")
            elif event_type == "complete":
                break

        return "\n".join(content_parts) if content_parts else "No response generated."

    async def _send_response(
        self,
        sender: str,
        response: str,
        signal_client: SignalClient,
    ):
        """Send response, splitting if necessary."""
        max_length = self.settings.max_message_length

        if len(response) <= max_length:
            await signal_client.send_message(sender, response)
        else:
            # Split into chunks
            chunks = []
            current_chunk = ""

            for line in response.split("\n"):
                if len(current_chunk) + len(line) + 1 > max_length:
                    if current_chunk:
                        chunks.append(current_chunk)
                    current_chunk = line
                else:
                    if current_chunk:
                        current_chunk += "\n" + line
                    else:
                        current_chunk = line

            if current_chunk:
                chunks.append(current_chunk)

            # Send chunks with small delay
            for i, chunk in enumerate(chunks):
                if i > 0:
                    await asyncio.sleep(0.5)
                await signal_client.send_message(sender, chunk)

    async def _send_help(self, sender: str, signal_client: SignalClient):
        """Send help message."""
        help_text = """🤖 *AI Assistant Bot*

I can help you with questions, web searches, and more!

*Commands:*
• /help - Show this help message
• /new - Start a new conversation

*Tips:*
• Ask me anything!
• I can search the web for recent information
• I remember context within a conversation

*Examples:*
• "What's the weather like today?"
• "Explain quantum computing"
• "Search for the latest news about AI"
"""
        await signal_client.send_message(sender, help_text)

    async def run(self):
        """Run the bot main loop."""
        settings = self.settings
        self._running = True

        logger.info(f"Starting Signal Bot for {settings.signal_phone_number}")
        logger.info(f"Agent API: {settings.agent_api_url}")
        logger.info(f"Signal CLI: {settings.signal_cli_url}")

        async with SignalClient(
            base_url=str(settings.signal_cli_url),
            phone_number=settings.signal_phone_number,
        ) as signal_client:
            async with AgentClient(
                base_url=str(settings.agent_api_url),
                auth_token_url=str(settings.auth_token_url),
                delegation_token=settings.delegation_token,
            ) as agent_client:
                # Verify connections
                if not await signal_client.is_registered():
                    logger.error("Signal phone number is not registered!")
                    return

                if not await agent_client.health_check():
                    logger.warning("Agent API health check failed, continuing anyway...")

                logger.info("Bot initialized successfully, starting message polling...")

                # Main message loop
                async for message in signal_client.poll_messages(
                    interval=settings.poll_interval
                ):
                    if not self._running:
                        break

                    # Skip group messages for now
                    if message.is_group_message:
                        logger.debug("Skipping group message")
                        continue

                    # Process in background to not block polling
                    asyncio.create_task(
                        self.process_message(message, signal_client, agent_client)
                    )

    def stop(self):
        """Stop the bot."""
        self._running = False
        logger.info("Bot stopping...")


async def run_api_server(settings: Settings):
    """Start the FastAPI HTTP server."""
    import uvicorn
    from .api import create_app

    app = create_app(settings)
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=settings.bridge_api_port,
        log_level=settings.log_level.lower(),
    )
    server = uvicorn.Server(config)
    logger.info(f"Starting Bridge API on port {settings.bridge_api_port}")
    await server.serve()


async def run_signal_bot(settings: Settings):
    """Run the Signal bot polling loop (if enabled and configured)."""
    if not settings.signal_enabled:
        logger.info("Signal channel disabled — skipping Signal bot")
        return

    if not settings.signal_phone_number:
        logger.warning("Signal enabled but SIGNAL_PHONE_NUMBER not set — skipping Signal bot")
        return

    if not settings.delegation_token:
        logger.warning("Signal enabled but DELEGATION_TOKEN not set — skipping Signal bot")
        return

    bot = SignalBot(settings)
    try:
        await bot.run()
    except Exception as e:
        logger.error(f"Signal bot crashed: {e}", exc_info=True)
        raise


async def main():
    """Main entry point — runs API server and Signal bot concurrently."""
    settings = get_settings()

    # Configure logging level
    logging.getLogger().setLevel(settings.log_level)

    logger.info(f"Bridge starting (env={settings.environment})")
    logger.info(f"  API server:  port {settings.bridge_api_port}")
    logger.info(f"  Signal bot:  {'enabled' if settings.signal_enabled else 'disabled'}")
    logger.info(f"  Email:       {'enabled' if settings.email_enabled else 'disabled'}")

    # Handle shutdown gracefully
    loop = asyncio.get_event_loop()

    def signal_handler():
        logger.info("Shutdown signal received")

    try:
        import signal as sig

        for s in (sig.SIGINT, sig.SIGTERM):
            loop.add_signal_handler(s, signal_handler)
    except (ImportError, NotImplementedError):
        pass  # Windows doesn't support signal handlers

    try:
        await asyncio.gather(
            run_api_server(settings),
            run_signal_bot(settings),
        )
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error(f"Bridge crashed: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    asyncio.run(main())
