"""
Test Document Service

Seeds a small, repeatable set of documents from the test-doc-repo and reports their
ingestion status (including text + visual embeddings). Runs inside ingest-lxc and
uses the existing ingestion API for processing.
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx
import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

logger = structlog.get_logger()
router = APIRouter()

API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = os.getenv("API_PORT", "8002")
TEST_DOC_REPO_PATH = os.getenv("TEST_DOC_REPO_PATH", "/srv/test-docs")
TEST_DOC_STATE_PATH = os.getenv("TEST_DOC_STATE_PATH", "/srv/ingest/test-docs-state.json")

TEST_DOCS = [
    # Simple text-based PDFs that work well with pdfplumber (no Marker/ColPali needed)
    # These are research papers with clean text extraction for testing RAG search & roles
    {
        "id": "attention-paper",
        "name": "Attention is All You Need (Transformer paper)",
        "path": "pdf/1706.03762.pdf",
        "mime": "application/pdf",
        "role": "test-role-a",
    },
    {
        "id": "retrieval-paper",
        "name": "Retrieval-Augmented Generation paper",
        "path": "pdf/2005.14165.pdf",
        "mime": "application/pdf",
        "role": "test-role-b",
    },
    {
        "id": "ret-paper",
        "name": "Retrieval-Enhanced Transformer paper",
        "path": "pdf/2010.11929.pdf",
        "mime": "application/pdf",
        "role": "test-role-c",
    },
]

# Complex documents requiring Marker/ColPali (for separate testing)
# These should NOT be used in the default test suite
TEST_DOCS_COMPLEX = [
    {
        "id": "cat-image",
        "name": "Cat image (visual embedding - requires ColPali)",
        "path": "image/cat.jpg",
        "mime": "image/jpeg",
        "role": "test-role-a",
    },
    {
        "id": "finance-charts",
        "name": "US Bancorp Q4 2023 presentation (charts - requires Marker)",
        "path": "pdf/general/doc08_us_bancorp_q4_2023_presentation/source.pdf",
        "mime": "application/pdf",
        "role": "test-role-b",
    },
    {
        "id": "civil-plan",
        "name": "NY Harbor plan set (CAD drawings - requires ColPali)",
        "path": "pdf/plans/doc1_ny_harbor/W912DS-10-B-0004-Plans.pdf",
        "mime": "application/pdf",
        "role": "test-role-c",
    },
]


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _load_state() -> Dict[str, str]:
    """Load persisted file ids for test docs."""
    try:
        if Path(TEST_DOC_STATE_PATH).exists():
            return json.loads(Path(TEST_DOC_STATE_PATH).read_text())
    except Exception as exc:
        logger.warning("Failed to load test-doc state", error=str(exc))
    return {}


def _save_state(state: Dict[str, str]) -> None:
    try:
        Path(TEST_DOC_STATE_PATH).parent.mkdir(parents=True, exist_ok=True)
        Path(TEST_DOC_STATE_PATH).write_text(json.dumps(state, indent=2))
    except Exception as exc:
        logger.warning("Failed to persist test-doc state", error=str(exc))


def _find_role_id(request: Request, role_name: str) -> Optional[str]:
    """Find a role id by name from the JWT context."""
    roles = getattr(request.state, "user_roles", [])
    for role in roles:
        if getattr(role, "name", "").lower() == role_name.lower():
            return getattr(role, "id", None)
    return None


def _build_upload_url() -> str:
    return f"http://{API_HOST}:{API_PORT}/upload"


def _build_file_url(file_id: str) -> str:
    return f"http://{API_HOST}:{API_PORT}/files/{file_id}"


async def _seed_doc(
    doc: Dict[str, str],
    request: Request,
    state: Dict[str, str],
) -> Tuple[Optional[str], Optional[str]]:
    """
    Seed a single document.

    Returns:
        (file_id, error_message)
    """
    role_id = _find_role_id(request, doc["role"])
    if not role_id:
        return None, f"Missing role in JWT: {doc['role']}"

    abs_path = Path(TEST_DOC_REPO_PATH) / doc["path"]
    if not abs_path.exists():
        return None, f"File not found: {abs_path}"

    headers = {
        "Authorization": request.headers.get("Authorization", ""),
        "X-User-Id": getattr(request.state, "user_id", ""),
    }

    data = {
        "metadata": json.dumps(
            {
                "test_doc_id": doc["id"],
                "test_doc_role": doc["role"],
                "source_path": doc["path"],
            }
        ),
        "visibility": "shared",
        "role_ids": role_id,
        "processing_config": json.dumps(
            {
                "enable_visual_embeddings": True,
            }
        ),
        "force_reprocess": "true",  # Always reprocess test docs (skip duplicate detection)
    }

    files = {
        "file": (
            abs_path.name,
            abs_path.read_bytes(),
            doc["mime"],
        )
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(_build_upload_url(), headers=headers, data=data, files=files)

    if response.status_code >= 400:
        return None, response.text

    payload = response.json()
    file_id = payload.get("fileId")
    if file_id:
        state[doc["id"]] = file_id
        _save_state(state)
        return file_id, None

    return None, "No fileId returned from upload"


async def _fetch_status(file_id: str, request: Request) -> Dict:
    headers = {
        "Authorization": request.headers.get("Authorization", ""),
        "X-User-Id": getattr(request.state, "user_id", ""),
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(_build_file_url(file_id), headers=headers)
        if response.status_code >= 400:
            return {
                "fileId": file_id,
                "status": "error",
                "error": response.text,
            }
        data = response.json()
        return {
          "fileId": file_id,
          "status": data.get("status"),
          "chunks": data.get("chunkCount"),
          "vectors": data.get("vectorCount"),
          "visualEmbedding": data.get("visualEmbedding") or data.get("visualEmbeddingGenerated"),
        }


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------


@router.get("/test-docs/status")
async def get_test_docs_status(request: Request):
    """
    Return status for each predefined test document.
    """
    state = _load_state()
    documents: List[Dict] = []

    for doc in TEST_DOCS:
        doc_status: Dict[str, Optional[str]] = {
            "id": doc["id"],
            "name": doc["name"],
            "role": doc["role"],
        }
        file_id = state.get(doc["id"])
        if not file_id:
            doc_status.update(
                {
                    "status": "not_seeded",
                    "chunks": None,
                    "vectors": None,
                    "visualEmbedding": False,
                }
            )
        else:
            status_payload = await _fetch_status(file_id, request)
            doc_status.update(status_payload)
        documents.append(doc_status)

    return {
        "documents": documents,
        "repoPath": TEST_DOC_REPO_PATH,
    }


@router.post("/test-docs/cleanup")
async def cleanup_test_docs(request: Request):
    """
    Delete all test documents and clear state.
    
    This forces a fresh re-upload on next seed, useful for testing
    ingestion pipeline changes.
    """
    if not request.headers.get("Authorization"):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"error": "Authorization header required"},
        )

    state = _load_state()
    deleted = []
    errors = []

    headers = {
        "Authorization": request.headers.get("Authorization", ""),
        "X-User-Id": getattr(request.state, "user_id", ""),
    }

    # Delete all files from state
    for doc_id, file_id in state.items():
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.delete(
                    f"http://{API_HOST}:{API_PORT}/files/{file_id}",
                    headers=headers
                )
                if response.status_code >= 400:
                    errors.append({
                        "docId": doc_id,
                        "fileId": file_id,
                        "error": response.text
                    })
                else:
                    deleted.append({
                        "docId": doc_id,
                        "fileId": file_id
                    })
        except Exception as e:
            errors.append({
                "docId": doc_id,
                "fileId": file_id,
                "error": str(e)
            })

    # Clear state file
    _save_state({})
    
    logger.info(
        "Test docs cleanup completed",
        deleted_count=len(deleted),
        error_count=len(errors)
    )

    return {
        "deleted": deleted,
        "errors": errors,
        "message": f"Deleted {len(deleted)} documents, {len(errors)} errors"
    }


@router.post("/test-docs/seed")
async def seed_test_docs(request: Request):
    """
    Seed simple test documents (text-based PDFs) for RAG search & role testing.
    
    These documents work with basic pdfplumber extraction and don't require
    GPU services (Marker/ColPali). Use /test-docs/seed-complex for documents
    that require advanced extraction.
    
    Use /test-docs/cleanup first to force fresh re-upload.
    """
    if not request.headers.get("Authorization"):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"error": "Authorization header required"},
        )

    state = _load_state()
    results = []

    for doc in TEST_DOCS:
        file_id, error = await _seed_doc(doc, request, state)
        results.append(
            {
                "id": doc["id"],
                "name": doc["name"],
                "role": doc["role"],
                "fileId": file_id,
                "error": error,
            }
        )

    return {"seeded": results}


@router.post("/test-docs/seed-complex")
async def seed_complex_test_docs(request: Request):
    """
    Seed complex test documents requiring Marker/ColPali (GPU services).
    
    These documents include:
    - Images (require ColPali visual embeddings)
    - Charts/presentations (require Marker for layout)
    - CAD drawings/plans (require ColPali for visual understanding)
    
    Only use this endpoint when GPU services are available (production).
    """
    if not request.headers.get("Authorization"):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"error": "Authorization header required"},
        )

    state = _load_state()
    results = []

    for doc in TEST_DOCS_COMPLEX:
        file_id, error = await _seed_doc(doc, request, state)
        results.append(
            {
                "id": doc["id"],
                "name": doc["name"],
                "role": doc["role"],
                "fileId": file_id,
                "error": error,
            }
        )

    return {"seeded": results}
