"""
Personal memory API — the user's own files, and only theirs.

Every route is under ``/users/me/memory`` and resolves the store from the
caller's principal. There is no ``/users/{id}/memory`` and no admin listing
on purpose: memory is readable by its owner and the model serving that
owner, nobody else (see ``docs/developers/user-memory.md``).

    GET    /users/me/memory                 list files (path, description, size, version)
    GET    /users/me/memory/file?path=      read one file
    PUT    /users/me/memory/file            write one file {path, content, if_version?}
    DELETE /users/me/memory/file?path=      delete one file
    DELETE /users/me/memory                 delete everything
    GET    /users/me/memory/export          zip of all files (markdown)

The on/off switch is ``PUT /users/me/chat-settings {memory_enabled}``.
"""

from __future__ import annotations

import io
import logging
import zipfile
from typing import Any, Dict, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth.dependencies import get_principal
from app.config.settings import get_settings
from app.db.session import _should_use_test_db
from app.schemas.auth import Principal
from app.services.user_memory import (
    EncryptionUnavailable,
    LimitExceeded,
    MemoryError,
    MemoryStore,
    NotFound,
    PathError,
    PrivacyRefused,
    VersionConflict,
    memory_enabled_for,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users/me/memory", tags=["memory"])


class MemoryWriteRequest(BaseModel):
    path: str = Field(..., description="profile.md, preferences.md, topics/<x>.md, areas/<x>.md, people/<x>.md")
    content: str = Field(..., max_length=20000)
    if_version: Optional[Union[int, str]] = Field(None, description="Version last read, or 'new'")


def _raise(exc: MemoryError) -> None:
    code = {
        PathError: 400, NotFound: 404, VersionConflict: 409, LimitExceeded: 413,
        PrivacyRefused: 422, EncryptionUnavailable: 503,
    }.get(type(exc), 400)
    detail: Dict[str, Any] = {"code": exc.code, "message": str(exc)}
    if isinstance(exc, VersionConflict) and exc.current is not None:
        detail["current"] = exc.current.as_dict()
    raise HTTPException(status_code=code, detail=detail)


async def _store(request: Request, principal: Principal) -> MemoryStore:
    if not getattr(get_settings(), "memory_enabled", True):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "memory_disabled", "message": "Personal memory is turned off on this platform."})
    return MemoryStore(principal, use_test_db=_should_use_test_db(request))


@router.get("")
async def list_memory(request: Request, principal: Principal = Depends(get_principal)) -> Dict[str, Any]:
    store = await _store(request, principal)
    try:
        files = await store.list()
    except MemoryError as exc:
        _raise(exc)
    return {
        "enabled": await memory_enabled_for(principal.sub, _should_use_test_db(request)),
        "encrypted": store.crypto is not None and bool(getattr(store.crypto, "enabled", False)),
        "files": [f.as_dict() for f in files],
        "limits": {
            "max_files": int(getattr(get_settings(), "memory_max_files", 40)),
            "max_file_bytes": int(getattr(get_settings(), "memory_max_file_bytes", 8000)),
        },
    }


@router.get("/file")
async def read_memory_file(
    request: Request,
    path: str = Query(..., description="Memory path"),
    principal: Principal = Depends(get_principal),
) -> Dict[str, Any]:
    store = await _store(request, principal)
    try:
        return (await store.read(path)).as_dict()
    except MemoryError as exc:
        _raise(exc)


@router.put("/file")
async def write_memory_file(
    request: Request,
    payload: MemoryWriteRequest,
    principal: Principal = Depends(get_principal),
) -> Dict[str, Any]:
    store = await _store(request, principal)
    try:
        info = await store.write(payload.path, payload.content, if_version=payload.if_version)
        return info.as_dict()
    except MemoryError as exc:
        _raise(exc)


@router.delete("/file")
async def delete_memory_file(
    request: Request,
    path: str = Query(...),
    principal: Principal = Depends(get_principal),
) -> Dict[str, Any]:
    store = await _store(request, principal)
    try:
        return {"path": path, "deleted": await store.delete(path)}
    except MemoryError as exc:
        _raise(exc)


@router.delete("")
async def delete_all_memory(request: Request, principal: Principal = Depends(get_principal)) -> Dict[str, Any]:
    store = await _store(request, principal)
    try:
        return {"deleted": await store.delete_all()}
    except MemoryError as exc:
        _raise(exc)


@router.get("/export")
async def export_memory(request: Request, principal: Principal = Depends(get_principal)) -> StreamingResponse:
    store = await _store(request, principal)
    try:
        files = await store.export()
    except MemoryError as exc:
        _raise(exc)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, content in files.items():
            zf.writestr(f"memory/{path}", content)
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="busibox-memory.zip"', "Cache-Control": "no-store"},
    )
