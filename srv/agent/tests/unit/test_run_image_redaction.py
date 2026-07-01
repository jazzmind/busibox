"""Tests for redacting base64 images before persisting run records."""

from app.services.run_service import redact_images_for_persistence


def test_payload_without_images_is_returned_unchanged():
    payload = {"prompt": "hello"}
    assert redact_images_for_persistence(payload) is payload


def test_images_replaced_with_metadata():
    payload = {
        "prompt": "extract this",
        "images": [{"media_type": "image/jpeg", "data": "A" * 1000}],
    }
    redacted = redact_images_for_persistence(payload)
    assert redacted["prompt"] == "extract this"
    assert redacted["images"] == [{"media_type": "image/jpeg", "size_b64": 1000}]
    # Original payload must keep the real bytes for execution
    assert payload["images"][0]["data"] == "A" * 1000
