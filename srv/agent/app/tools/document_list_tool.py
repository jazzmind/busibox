"""Deterministic inventory of documents accessible to the current chat."""

import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel
from pydantic_ai import RunContext, Tool

from app.agents.core import BusiboxDeps

_INVENTORY_STOP_WORDS = {
    "all", "available", "attachment", "attachments", "can", "current",
    "document", "documents", "doc", "docs", "file", "files", "in", "list",
    "me", "my", "of", "related", "see", "show", "the", "to", "what",
    "which", "you",
}


class DocumentInventoryItem(BaseModel):
    """A document returned from the authoritative library inventory."""

    file_id: str
    filename: str
    library_id: Optional[str] = None
    library_name: Optional[str] = None
    status: Optional[str] = None
    chunk_count: Optional[int] = None
    vector_count: Optional[int] = None


class DocumentListOutput(BaseModel):
    """Deterministic document inventory result."""

    success: bool
    scope: str
    total: int
    documents: List[DocumentInventoryItem]
    context: str
    error: Optional[str] = None


def _search_terms(query: Optional[str]) -> List[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9]+", (query or "").lower())
        if len(token) > 1 and token not in _INVENTORY_STOP_WORDS
    ]


def _document_item(
    document: Dict[str, Any],
    *,
    library_id: Optional[str] = None,
    library_name: Optional[str] = None,
) -> Optional[DocumentInventoryItem]:
    file_id = document.get("fileId") or document.get("file_id") or document.get("id")
    filename = (
        document.get("filename")
        or document.get("name")
        or document.get("originalFilename")
        or document.get("original_filename")
    )
    if not file_id or not filename:
        return None
    return DocumentInventoryItem(
        file_id=str(file_id),
        filename=str(filename),
        library_id=str(
            document.get("libraryId") or document.get("library_id") or library_id or ""
        ) or None,
        library_name=str(
            document.get("libraryName") or document.get("library_name") or library_name or ""
        ) or None,
        status=document.get("status") or document.get("stage"),
        chunk_count=document.get("chunkCount") or document.get("chunk_count"),
        vector_count=document.get("vectorCount") or document.get("vector_count"),
    )


async def list_documents(
    ctx: RunContext[BusiboxDeps],
    query: Optional[str] = None,
    limit: int = 100,
) -> DocumentListOutput:
    """List accessible documents without using semantic relevance search."""
    metadata = ctx.deps.metadata or {}
    scope = metadata.get("knowledge_scope") or "all"
    capped_limit = max(1, min(limit, 200))

    try:
        raw_documents: List[DocumentInventoryItem] = []

        if scope == "attachments":
            for document in metadata.get("attachment_documents") or []:
                item = _document_item(document)
                if item:
                    raw_documents.append(item)
            scope_label = "conversation attachments"
        else:
            libraries = await ctx.deps.busibox_client.list_libraries()
            library_by_id = {
                str(library.get("id")): library
                for library in libraries
                if library.get("id")
            }
            if scope == "libraries":
                library_ids = [
                    str(library_id)
                    for library_id in (metadata.get("selected_library_ids") or [])
                    if library_id
                ]
                scope_label = "selected library"
            else:
                library_ids = list(library_by_id)
                scope_label = "all accessible libraries"

            for library_id in library_ids:
                library = library_by_id.get(library_id) or {}
                documents = await ctx.deps.busibox_client.library_documents(library_id)
                for document in documents:
                    item = _document_item(
                        document,
                        library_id=library_id,
                        library_name=library.get("name"),
                    )
                    if item:
                        raw_documents.append(item)

        deduplicated = {item.file_id: item for item in raw_documents}
        documents = sorted(
            deduplicated.values(),
            key=lambda item: (item.library_name or "", item.filename.lower()),
        )

        terms = _search_terms(query)
        if terms:
            documents = [
                item for item in documents
                if all(
                    term in f"{item.filename} {item.library_name or ''}".lower()
                    for term in terms
                )
            ]

        documents = documents[:capped_limit]
        if documents:
            lines = []
            for item in documents:
                details = [item.filename]
                if item.status:
                    details.append(f"status: {item.status}")
                if item.library_name:
                    details.append(f"library: {item.library_name}")
                lines.append("- " + " | ".join(details))
            context = (
                f"Found {len(documents)} documents in {scope_label}. "
                "This is an authoritative inventory, not a relevance-search result.\n"
                + "\n".join(lines)
            )
        else:
            context = f"No documents matched in {scope_label}."

        return DocumentListOutput(
            success=True,
            scope=scope,
            total=len(documents),
            documents=documents,
            context=context,
        )
    except Exception as exc:
        return DocumentListOutput(
            success=False,
            scope=scope,
            total=0,
            documents=[],
            context="Document inventory could not be loaded.",
            error=str(exc),
        )


document_list_tool = Tool(
    list_documents,
    takes_ctx=True,
    name="list_documents",
    description=(
        "List the user's accessible uploaded documents deterministically. Use this for "
        "document/file inventory or filename questions. Do not use semantic document_search "
        "to answer which documents are available."
    ),
)
