"""Live end-to-end test: record-extractor reads a receipt image.

Requires the local liteLLM + model stack. Run with:
    LIVE_LLM_TESTS=1 python -m pytest tests/integration/test_invoke_images_live.py -v
"""

import base64
import json
import os
from pathlib import Path

import pytest

from app.agents.record_extractor_agent import RecordExtractorAgent

pytestmark = pytest.mark.skipif(
    not os.getenv("LIVE_LLM_TESTS"), reason="LIVE_LLM_TESTS not set"
)

RECEIPT_SCHEMA = {
    "name": "receipt_fields",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["merchant", "amount", "date", "cardLast4", "legible"],
        "properties": {
            "merchant": {"type": ["string", "null"]},
            "amount": {"type": ["number", "null"]},
            "date": {"type": ["string", "null"]},
            "cardLast4": {"type": ["string", "null"]},
            "legible": {"type": "boolean"},
        },
    },
}


async def test_extracts_fields_from_receipt_image():
    fixture = Path(__file__).parent.parent / "fixtures" / "receipt-sample.jpg"
    data = base64.b64encode(fixture.read_bytes()).decode()
    agent = RecordExtractorAgent()
    raw = await agent._call_structured_output(
        prompt="Read this receipt image and extract merchant, total amount, date (YYYY-MM-DD), and card last four digits.",
        system_prompt=agent.config.instructions,
        response_schema=RECEIPT_SCHEMA,
        images=[{"media_type": "image/jpeg", "data": data}],
    )
    result = json.loads(raw)
    assert result["legible"] is True
    assert result["amount"] == pytest.approx(11.45, abs=0.01)
    assert "starbucks" in (result["merchant"] or "").lower()
