"""
Database and Milvus schema definitions.

These schemas define the structure for PostgreSQL tables and Milvus collections.
"""

# PostgreSQL table names
POSTGRES_TABLES = {
    "files": "ingestion_files",
    "status": "ingestion_status",
    "chunks": "ingestion_chunks",
}

# Milvus collection name
MILVUS_COLLECTION = "documents"

# Processing stages
PROCESSING_STAGES = [
    "queued",
    "parsing",
    "classifying",
    "extracting_metadata",
    "chunking",
    "cleanup",
    "embedding",
    "indexing",
    "completed",
    "failed",
]

# Supported MIME types
SUPPORTED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # DOCX
    "text/plain",
    "text/html",
    "text/markdown",
    "text/csv",
    "application/json",
}

# Document types
DOCUMENT_TYPES = [
    "report",
    "article",
    "email",
    "code",
    "presentation",
    "spreadsheet",
    "manual",
    "other",
]

# Milvus field names
MILVUS_FIELDS = {
    "id": "id",
    "file_id": "file_id",
    "chunk_index": "chunk_index",
    "page_number": "page_number",
    "modality": "modality",
    "text": "text",
    "text_dense": "text_dense",
    "text_sparse": "text_sparse",
    "page_vectors": "page_vectors",
    "user_id": "user_id",
    "metadata": "metadata",
}

# Embedding dimensions
EMBEDDING_DIMS = {
    "text_dense": 1024,  # bge-large-en-v1.5 (local FastEmbed)
    "page_vectors": 128,  # ColPali patch dimension
}

