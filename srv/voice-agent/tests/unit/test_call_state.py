"""
Unit tests for call state machine.
"""

import pytest
from uuid import uuid4

from models.call_state import (
    CallState,
    CallSession,
    CallLine,
    Speaker,
    AudioDetectionType,
    is_valid_transition,
    VALID_TRANSITIONS,
)


class TestCallState:
    """Tests for CallState enum and transitions."""

    def test_valid_transitions(self):
        """Test that valid transitions are recognized."""
        # Idle -> Dialing is valid
        assert is_valid_transition(CallState.IDLE, CallState.DIALING)
        
        # Dialing -> Ringing is valid
        assert is_valid_transition(CallState.DIALING, CallState.RINGING)
        
        # Connected -> Analyzing is valid
        assert is_valid_transition(CallState.CONNECTED, CallState.ANALYZING)
        
        # HumanDetected -> AIConversation is valid
        assert is_valid_transition(CallState.HUMAN_DETECTED, CallState.AI_CONVERSATION)

    def test_invalid_transitions(self):
        """Test that invalid transitions are rejected."""
        # Idle -> Connected is invalid (must go through Dialing)
        assert not is_valid_transition(CallState.IDLE, CallState.CONNECTED)
        
        # Ended -> Dialing is invalid
        assert not is_valid_transition(CallState.ENDED, CallState.DIALING)
        
        # Complete has no valid transitions
        assert not is_valid_transition(CallState.COMPLETE, CallState.IDLE)

    def test_all_states_have_transition_rules(self):
        """Verify all states are covered in transition rules."""
        for state in CallState:
            assert state in VALID_TRANSITIONS, f"Missing transition rules for {state}"


class TestCallLine:
    """Tests for CallLine model."""

    def test_create_line(self):
        """Test creating a call line."""
        line = CallLine(phone_number="+18001234567")
        
        assert line.id is not None
        assert line.phone_number == "+18001234567"
        assert line.state == CallState.IDLE
        assert not line.is_active

    def test_line_becomes_active(self):
        """Test line active status changes with state."""
        line = CallLine(phone_number="+18001234567")
        
        line.state = CallState.DIALING
        assert line.is_active
        
        line.state = CallState.CONNECTED
        assert line.is_active
        
        line.state = CallState.ENDED
        assert not line.is_active

    def test_add_transcript(self):
        """Test adding transcript entries to a line."""
        line = CallLine(phone_number="+18001234567")
        
        entry = line.add_transcript(
            speaker=Speaker.REMOTE,
            text="Hello, this is the IRS.",
            confidence=0.95,
        )
        
        assert len(line.transcript) == 1
        assert entry.speaker == Speaker.REMOTE
        assert entry.text == "Hello, this is the IRS."
        assert entry.confidence == 0.95


class TestCallSession:
    """Tests for CallSession model."""

    def test_create_session(self):
        """Test creating a call session."""
        session = CallSession(
            user_id="test-user",
            target_phone_number="+18001234567",
            target_name="IRS",
        )
        
        assert session.id is not None
        assert session.user_id == "test-user"
        assert session.target_phone_number == "+18001234567"
        assert session.state == CallState.IDLE
        assert len(session.lines) == 0

    def test_add_line_to_session(self):
        """Test adding lines to a session."""
        session = CallSession(
            user_id="test-user",
            target_phone_number="+18001234567",
        )
        
        line1 = session.add_line()
        assert len(session.lines) == 1
        assert line1.phone_number == "+18001234567"
        
        line2 = session.add_line("+18009876543")
        assert len(session.lines) == 2
        assert line2.phone_number == "+18009876543"

    def test_get_line_by_id(self):
        """Test retrieving a line by ID."""
        session = CallSession(
            user_id="test-user",
            target_phone_number="+18001234567",
        )
        
        line = session.add_line()
        
        found = session.get_line(line.id)
        assert found is not None
        assert found.id == line.id
        
        not_found = session.get_line(uuid4())
        assert not_found is None

    def test_active_lines(self):
        """Test getting active lines."""
        session = CallSession(
            user_id="test-user",
            target_phone_number="+18001234567",
        )
        
        line1 = session.add_line()
        line2 = session.add_line()
        
        line1.state = CallState.CONNECTED
        line2.state = CallState.ENDED
        
        active = session.active_lines
        assert len(active) == 1
        assert active[0].id == line1.id

    def test_full_transcript(self):
        """Test getting combined transcript from all lines."""
        session = CallSession(
            user_id="test-user",
            target_phone_number="+18001234567",
        )
        
        line1 = session.add_line()
        line2 = session.add_line()
        
        line1.add_transcript(Speaker.REMOTE, "First message")
        line2.add_transcript(Speaker.REMOTE, "Second message")
        line1.add_transcript(Speaker.AI, "Response")
        
        transcript = session.full_transcript
        assert len(transcript) == 3

    def test_session_is_active(self):
        """Test session active status."""
        session = CallSession(
            user_id="test-user",
            target_phone_number="+18001234567",
        )
        
        assert not session.is_active
        
        line = session.add_line()
        line.state = CallState.CONNECTED
        assert session.is_active
        
        line.state = CallState.ENDED
        assert not session.is_active
