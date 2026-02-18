"""Internal authz routes (currently unused)."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


def set_pg_service(pg_service, pg_test_service=None):
    """Compatibility no-op: no internal routes currently require DB services."""
    return None
