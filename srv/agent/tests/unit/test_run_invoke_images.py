"""Validation tests for input.images on RunInvoke."""

import pytest
from pydantic import ValidationError

from app.schemas.run import RunInvoke


def _invoke(images):
    return RunInvoke(agent_name="record-extractor", input={"prompt": "x", "images": images})


def test_no_images_is_valid():
    inv = RunInvoke(agent_name="record-extractor", input={"prompt": "x"})
    assert "images" not in inv.input


def test_valid_images_accepted():
    inv = _invoke([{"media_type": "image/jpeg", "data": "aGVsbG8="}])
    assert len(inv.input["images"]) == 1


@pytest.mark.parametrize("media_type", ["image/gif", "application/pdf", "text/plain", None])
def test_disallowed_media_type_rejected(media_type):
    with pytest.raises(ValidationError, match="media_type"):
        _invoke([{"media_type": media_type, "data": "aGVsbG8="}])


def test_more_than_four_images_rejected():
    imgs = [{"media_type": "image/png", "data": "aGVsbG8="}] * 5
    with pytest.raises(ValidationError, match="at most 4"):
        _invoke(imgs)


def test_oversized_image_rejected():
    big = "A" * 7_000_001
    with pytest.raises(ValidationError, match="size limit"):
        _invoke([{"media_type": "image/jpeg", "data": big}])


def test_empty_data_rejected():
    with pytest.raises(ValidationError, match="non-empty"):
        _invoke([{"media_type": "image/jpeg", "data": ""}])


def test_images_must_be_a_list():
    with pytest.raises(ValidationError, match="must be a list"):
        RunInvoke(agent_name="record-extractor", input={"prompt": "x", "images": "nope"})


def test_exactly_four_images_accepted():
    imgs = [{"media_type": "image/png", "data": "aGVsbG8="}] * 4
    inv = _invoke(imgs)
    assert len(inv.input["images"]) == 4


def test_data_at_exact_size_limit_accepted():
    inv = _invoke([{"media_type": "image/jpeg", "data": "A" * 7_000_000}])
    assert len(inv.input["images"]) == 1


def test_explicit_none_images_is_valid():
    inv = _invoke(None)
    assert inv.input["images"] is None


def test_non_dict_list_item_rejected():
    with pytest.raises(ValidationError, match="must be an object"):
        _invoke(["aGVsbG8="])
