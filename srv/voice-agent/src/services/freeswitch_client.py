"""
FreeSWITCH ESL Client.

Provides async interface to FreeSWITCH via Event Socket Layer (ESL).
Handles call origination, DTMF, audio streaming, and call bridging.
"""

import asyncio
import logging
from typing import Any, Callable, Dict, Optional
from uuid import UUID

import structlog

from config.settings import get_settings

logger = structlog.get_logger(__name__)


class FreeSwitchError(Exception):
    """FreeSWITCH operation error."""

    pass


class FreeSwitchClient:
    """
    Async client for FreeSWITCH ESL.
    
    Provides high-level interface for:
    - Originating outbound calls
    - Sending DTMF tones
    - Streaming audio for transcription
    - Bridging calls to WebRTC
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        password: Optional[str] = None,
    ):
        settings = get_settings()
        self.host = host or settings.freeswitch_host
        self.port = port or settings.freeswitch_port
        self.password = password or settings.freeswitch_password
        
        self._connection = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._connected = False
        self._event_handlers: Dict[str, Callable] = {}
        self._response_queue: asyncio.Queue = asyncio.Queue()
        self._event_task: Optional[asyncio.Task] = None

    async def connect(self) -> bool:
        """Connect to FreeSWITCH ESL."""
        try:
            logger.info(
                "Connecting to FreeSWITCH",
                host=self.host,
                port=self.port,
            )
            
            self._reader, self._writer = await asyncio.open_connection(
                self.host, self.port
            )
            
            # Read initial content-type header
            await self._read_response()
            
            # Authenticate
            auth_cmd = f"auth {self.password}\n\n"
            self._writer.write(auth_cmd.encode())
            await self._writer.drain()
            
            response = await self._read_response()
            if "Reply-Text: +OK" not in response:
                raise FreeSwitchError(f"Authentication failed: {response}")
            
            # Subscribe to events
            await self._subscribe_events()
            
            # Start event listener
            self._event_task = asyncio.create_task(self._event_listener())
            
            self._connected = True
            logger.info("Connected to FreeSWITCH successfully")
            return True
            
        except Exception as e:
            logger.error("Failed to connect to FreeSWITCH", error=str(e))
            self._connected = False
            return False

    async def disconnect(self) -> None:
        """Disconnect from FreeSWITCH."""
        if self._event_task:
            self._event_task.cancel()
            try:
                await self._event_task
            except asyncio.CancelledError:
                pass
        
        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()
        
        self._connected = False
        logger.info("Disconnected from FreeSWITCH")

    async def _read_response(self) -> str:
        """Read a complete ESL response."""
        headers = {}
        content = ""
        
        # Read headers
        while True:
            line = await self._reader.readline()
            line = line.decode().strip()
            if not line:
                break
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip()] = value.strip()
        
        # Read content if present
        content_length = int(headers.get("Content-Length", 0))
        if content_length > 0:
            content = await self._reader.read(content_length)
            content = content.decode()
        
        return content or str(headers)

    async def _subscribe_events(self) -> None:
        """Subscribe to relevant FreeSWITCH events."""
        events = [
            "CHANNEL_CREATE",
            "CHANNEL_ANSWER",
            "CHANNEL_HANGUP",
            "CHANNEL_HANGUP_COMPLETE",
            "DTMF",
            "CHANNEL_PROGRESS",
            "CHANNEL_PROGRESS_MEDIA",
            "CHANNEL_BRIDGE",
            "CHANNEL_UNBRIDGE",
            "RECORD_START",
            "RECORD_STOP",
            "CUSTOM",
        ]
        
        cmd = f"event plain {' '.join(events)}\n\n"
        self._writer.write(cmd.encode())
        await self._writer.drain()
        await self._read_response()

    async def _event_listener(self) -> None:
        """Background task to listen for ESL events."""
        while self._connected:
            try:
                response = await self._read_response()
                if response:
                    await self._handle_event(response)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error reading ESL event", error=str(e))
                await asyncio.sleep(0.1)

    async def _handle_event(self, event_data: str) -> None:
        """Handle incoming ESL events."""
        # Parse event and dispatch to registered handlers
        event_type = None
        for line in event_data.split("\n"):
            if line.startswith("Event-Name:"):
                event_type = line.split(":", 1)[1].strip()
                break
        
        if event_type and event_type in self._event_handlers:
            try:
                handler = self._event_handlers[event_type]
                await handler(event_data)
            except Exception as e:
                logger.error(
                    "Error handling event",
                    event_type=event_type,
                    error=str(e),
                )

    def on_event(self, event_name: str, handler: Callable) -> None:
        """Register an event handler."""
        self._event_handlers[event_name] = handler

    async def _send_command(self, command: str) -> str:
        """Send an ESL command and wait for response."""
        if not self._connected:
            raise FreeSwitchError("Not connected to FreeSWITCH")
        
        full_cmd = f"api {command}\n\n"
        self._writer.write(full_cmd.encode())
        await self._writer.drain()
        
        return await self._read_response()

    async def _send_bgapi(self, command: str) -> str:
        """Send a background API command."""
        if not self._connected:
            raise FreeSwitchError("Not connected to FreeSWITCH")
        
        full_cmd = f"bgapi {command}\n\n"
        self._writer.write(full_cmd.encode())
        await self._writer.drain()
        
        response = await self._read_response()
        
        # Extract job UUID from response
        for line in response.split("\n"):
            if "Job-UUID:" in line:
                return line.split(":", 1)[1].strip()
        
        return response

    async def originate(
        self,
        phone_number: str,
        caller_id: Optional[str] = None,
        gateway: str = "telnyx",
        timeout: int = 60,
    ) -> str:
        """
        Originate an outbound call.
        
        Args:
            phone_number: Destination phone number (E.164 format)
            caller_id: Caller ID to display
            gateway: SIP gateway name
            timeout: Ring timeout in seconds
            
        Returns:
            FreeSWITCH channel UUID
        """
        settings = get_settings()
        caller_id = caller_id or settings.sip_trunk_caller_id
        
        # Clean phone number
        phone_number = phone_number.replace(" ", "").replace("-", "")
        if not phone_number.startswith("+"):
            phone_number = f"+{phone_number}"
        
        # Build originate command
        # Format: originate {vars}sofia/gateway/name/number &park()
        channel_vars = [
            f"origination_caller_id_number={caller_id}",
            f"origination_caller_id_name=Busibox",
            f"originate_timeout={timeout}",
            "ignore_early_media=true",
            "absolute_codec_string=PCMU,PCMA",
        ]
        
        vars_str = ",".join(channel_vars)
        dial_string = f"sofia/gateway/{gateway}/{phone_number}"
        
        # Park the call so we can control it
        command = f"originate {{{vars_str}}}{dial_string} &park()"
        
        logger.info(
            "Originating call",
            phone_number=phone_number,
            gateway=gateway,
        )
        
        response = await self._send_bgapi(command)
        logger.debug("Originate response", response=response)
        
        return response

    async def hangup(self, uuid: str, cause: str = "NORMAL_CLEARING") -> bool:
        """Hangup a call."""
        command = f"uuid_kill {uuid} {cause}"
        response = await self._send_command(command)
        return "+OK" in response

    async def send_dtmf(self, uuid: str, digits: str) -> bool:
        """
        Send DTMF tones to a call.
        
        Args:
            uuid: Channel UUID
            digits: DTMF digits to send (0-9, *, #)
        """
        # Use uuid_send_dtmf for in-band DTMF
        command = f"uuid_send_dtmf {uuid} {digits}"
        response = await self._send_command(command)
        
        logger.info(
            "Sent DTMF",
            uuid=uuid,
            digits=digits,
        )
        
        return "+OK" in response

    async def bridge(self, uuid1: str, uuid2: str) -> bool:
        """Bridge two calls together."""
        command = f"uuid_bridge {uuid1} {uuid2}"
        response = await self._send_command(command)
        return "+OK" in response

    async def start_audio_stream(
        self,
        uuid: str,
        websocket_url: str,
    ) -> bool:
        """
        Start streaming audio from a call to a WebSocket.
        
        Uses mod_audio_stream or similar to stream RTP audio
        to our transcription service.
        """
        # This depends on the exact FreeSWITCH audio streaming module installed
        # Common options: mod_audio_stream, mod_shout, uuid_audio_stream
        command = f"uuid_audio_stream {uuid} start {websocket_url} mono 16000"
        response = await self._send_command(command)
        return "+OK" in response

    async def stop_audio_stream(self, uuid: str) -> bool:
        """Stop audio streaming for a call."""
        command = f"uuid_audio_stream {uuid} stop"
        response = await self._send_command(command)
        return "+OK" in response

    async def get_channel_info(self, uuid: str) -> Dict[str, Any]:
        """Get information about a channel."""
        command = f"uuid_dump {uuid}"
        response = await self._send_command(command)
        
        info = {}
        for line in response.split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                info[key.strip()] = value.strip()
        
        return info

    async def transfer_to_webrtc(
        self,
        uuid: str,
        webrtc_endpoint: str,
    ) -> bool:
        """
        Transfer a call to a WebRTC endpoint.
        
        This bridges the SIP call to the user's browser via mod_verto.
        """
        # This would use mod_verto or a similar WebRTC bridge
        command = f"uuid_transfer {uuid} verto.rtc/{webrtc_endpoint}"
        response = await self._send_command(command)
        return "+OK" in response

    @property
    def is_connected(self) -> bool:
        """Check if connected to FreeSWITCH."""
        return self._connected


# Singleton instance
_freeswitch_client: Optional[FreeSwitchClient] = None


def get_freeswitch_client() -> FreeSwitchClient:
    """Get the global FreeSWITCH client instance."""
    global _freeswitch_client
    if _freeswitch_client is None:
        _freeswitch_client = FreeSwitchClient()
    return _freeswitch_client
