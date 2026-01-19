# Voice Agent Service

Busibox Voice AI Platform - VoIP calling, real-time transcription, IVR navigation, and AI-powered voice conversations.

## Overview

The Voice Agent service provides:

1. **VoIP Calling** - Make outbound calls via SIP trunk
2. **Real-time Transcription** - Speech-to-text using faster-whisper
3. **Hold Detection** - Detect hold music vs live agents
4. **IVR Navigation** - AI-driven phone menu navigation
5. **Voice Conversations** - Real-time AI voice interactions
6. **Human Handoff** - Seamlessly transfer control with AI coaching
7. **Transcript Storage** - Save transcripts to document library

## Quick Start

### Prerequisites

- Python 3.11+
- FreeSWITCH (for telephony)
- SIP trunk account (Telnyx recommended)
- GPU recommended for transcription

### Local Development

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set environment variables
export VOICE_FREESWITCH_HOST=localhost
export VOICE_FREESWITCH_PASSWORD=ClueCon
export VOICE_SIP_TRUNK_USERNAME=your-username
export VOICE_SIP_TRUNK_PASSWORD=your-password

# Run service
python src/main.py
```

### Docker

```bash
docker build -t voice-agent .
docker run -p 8005:8005 \
  -e VOICE_FREESWITCH_HOST=host.docker.internal \
  voice-agent
```

## API Endpoints

### Call Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/calls` | Start new call session |
| GET | `/api/v1/calls` | List active calls |
| GET | `/api/v1/calls/{id}` | Get call details |
| POST | `/api/v1/calls/{id}/dial` | Add parallel line |
| POST | `/api/v1/calls/{id}/dtmf` | Send DTMF tones |
| POST | `/api/v1/calls/{id}/takeover` | Human takeover |
| POST | `/api/v1/calls/{id}/hangup` | Hangup call |

### WebSocket

| Endpoint | Description |
|----------|-------------|
| `/api/v1/calls/{id}/stream` | Real-time call events |

### Transcripts

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/transcripts/{session_id}/save` | Save transcript |
| GET | `/api/v1/transcripts` | List saved transcripts |
| GET | `/api/v1/transcripts/{id}` | Get transcript |

## Configuration

Environment variables (prefix `VOICE_`):

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | 0.0.0.0 | Server bind address |
| `PORT` | 8005 | Server port |
| `FREESWITCH_HOST` | 127.0.0.1 | FreeSWITCH host |
| `FREESWITCH_PORT` | 8021 | FreeSWITCH ESL port |
| `FREESWITCH_PASSWORD` | ClueCon | ESL password |
| `SIP_TRUNK_HOST` | sip.telnyx.com | SIP provider |
| `SIP_TRUNK_USERNAME` | | SIP username |
| `SIP_TRUNK_PASSWORD` | | SIP password |
| `WHISPER_MODEL` | base.en | Whisper model size |
| `WHISPER_DEVICE` | auto | Device (cpu/cuda/auto) |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Voice Agent Service                      │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │  REST API   │  │  WebSocket  │  │  Call Manager       │ │
│  │  (FastAPI)  │  │  (Real-time)│  │  (Orchestration)    │ │
│  └─────────────┘  └─────────────┘  └─────────────────────┘ │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────┐│
│  │                  Audio Pipeline                          ││
│  │  ┌───────────┐  ┌───────────┐  ┌─────────────────────┐ ││
│  │  │  Capture  │→ │    VAD    │→ │    Transcription    │ ││
│  │  └───────────┘  └───────────┘  └─────────────────────┘ ││
│  └─────────────────────────────────────────────────────────┘│
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────┐│
│  │                  Telephony Layer                         ││
│  │  ┌───────────────┐          ┌──────────────────────┐   ││
│  │  │  FreeSWITCH   │  ←────→  │  SIP Trunk (Telnyx)  │   ││
│  │  │  (ESL Client) │          │                      │   ││
│  │  └───────────────┘          └──────────────────────┘   ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
```

## Development Phases

- [x] **Phase 1**: Audio pipeline + transcription + basic UI
- [ ] **Phase 2**: VAD + hold detection + notifications
- [ ] **Phase 3**: IVR navigation agent
- [ ] **Phase 4**: Voice conversation agent + TTS
- [ ] **Phase 5**: WebRTC handoff + AI coaching
- [ ] **Phase 6**: Transcript storage + RAG integration

## Testing

```bash
# Run tests
pytest tests/

# With coverage
pytest tests/ --cov=src --cov-report=html
```

## Related Documentation

- [Architecture Overview](../../docs/architecture/voice-agent.md)
- [FreeSWITCH Setup](../../docs/guides/freeswitch-setup.md)
- [SIP Trunk Configuration](../../docs/configuration/sip-trunk.md)
