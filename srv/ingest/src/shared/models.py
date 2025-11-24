"""
Pydantic models for ingestion service.

These models are used by both API and worker for data validation and serialization.
"""

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class ProcessingStage(str, Enum):
    """Processing stage enumeration."""
    QUEUED = "queued"
    PARSING = "parsing"
    CLASSIFYING = "classifying"
    EXTRACTING_METADATA = "extracting_metadata"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    COMPLETED = "completed"
    FAILED = "failed"


class File(BaseModel):
    """File metadata model."""
    file_id: str = Field(..., description="Unique file identifier (UUID)")
    user_id: str = Field(..., description="User who uploaded the file")
    filename: str = Field(..., description="Stored filename")
    original_filename: str = Field(..., description="Original upload filename")
    mime_type: str = Field(..., description="MIME type")
    size_bytes: int = Field(..., description="File size in bytes")
    storage_path: str = Field(..., description="S3 path in MinIO")
    content_hash: str = Field(..., description="SHA-256 content hash")
    document_type: Optional[str] = Field(None, description="Document type classification")
    primary_language: Optional[str] = Field(None, description="Primary language (ISO 639-1)")
    detected_languages: List[str] = Field(default_factory=list, description="All detected languages")
    classification_confidence: Optional[float] = Field(None, ge=0.0, le=1.0, description="Classification confidence")
    chunk_count: int = Field(default=0, description="Number of chunks")
    vector_count: int = Field(default=0, description="Number of vectors")
    processing_duration_seconds: Optional[int] = Field(None, description="Processing duration")
    extracted_title: Optional[str] = Field(None, description="Extracted title")
    extracted_author: Optional[str] = Field(None, description="Extracted author")
    extracted_date: Optional[datetime] = Field(None, description="Extracted date")
    extracted_keywords: List[str] = Field(default_factory=list, description="Extracted keywords")
    metadata: Dict = Field(default_factory=dict, description="Additional metadata")
    permissions: Dict = Field(default_factory=lambda: {"visibility": "private"}, description="Permissions")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


class Status(BaseModel):
    """Processing status model."""
    file_id: str = Field(..., description="File identifier")
    stage: ProcessingStage = Field(..., description="Current processing stage")
    progress: int = Field(..., ge=0, le=100, description="Progress percentage (0-100)")
    chunks_processed: Optional[int] = Field(None, description="Chunks processed so far")
    total_chunks: Optional[int] = Field(None, description="Total chunks")
    pages_processed: Optional[int] = Field(None, description="Pages processed so far")
    total_pages: Optional[int] = Field(None, description="Total pages")
    error_message: Optional[str] = Field(None, description="Error message if failed")
    retry_count: int = Field(default=0, description="Number of retries")
    started_at: Optional[datetime] = Field(None, description="Processing start time")
    completed_at: Optional[datetime] = Field(None, description="Processing completion time")
    updated_at: datetime = Field(..., description="Last update time")


class Chunk(BaseModel):
    """Chunk metadata model."""
    chunk_id: str = Field(..., description="Unique chunk identifier")
    file_id: str = Field(..., description="File identifier")
    chunk_index: int = Field(..., description="Chunk position in document (0-indexed)")
    text: str = Field(..., description="Chunk text content")
    char_offset: Optional[int] = Field(None, description="Character offset in original document")
    token_count: int = Field(..., description="Number of tokens")
    page_number: Optional[int] = Field(None, description="PDF page number (1-indexed)")
    section_heading: Optional[str] = Field(None, description="Section/chapter heading")
    metadata: Dict = Field(default_factory=dict, description="Additional chunk metadata")
    created_at: datetime = Field(..., description="Creation timestamp")


class Vector(BaseModel):
    """Vector embedding model."""
    id: str = Field(..., description="Vector identifier")
    file_id: str = Field(..., description="File identifier")
    chunk_index: int = Field(..., description="Chunk index (-1 for page images)")
    page_number: Optional[int] = Field(None, description="PDF page number")
    modality: str = Field(..., description="'text' or 'page_image'")
    text: Optional[str] = Field(None, description="Text content (for BM25)")
    text_dense: Optional[List[float]] = Field(None, description="Dense embedding (1024 dims, bge-large-en-v1.5)")
    text_sparse: Optional[Dict] = Field(None, description="Sparse BM25 embedding")
    page_vectors: Optional[List[float]] = Field(None, description="ColPali multi-vector (128 dims)")
    user_id: str = Field(..., description="User identifier")
    metadata: Dict = Field(default_factory=dict, description="Vector metadata")


class DocumentClassification(BaseModel):
    """Document classification result."""
    document_type: str = Field(..., description="Document type (report, article, email, etc.)")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Classification confidence")
    primary_language: str = Field(..., description="Primary language (ISO 639-1)")
    detected_languages: List[str] = Field(default_factory=list, description="All detected languages")
    content_hash: str = Field(..., description="Content hash for deduplication")

