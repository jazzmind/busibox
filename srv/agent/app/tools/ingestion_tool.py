"""Document ingestion tool for processing files."""
from typing import Optional, Dict, Any

from pydantic import BaseModel, Field
from pydantic_ai import Tool

from app.clients.ingest_client import IngestClient


class IngestionInput(BaseModel):
    """Input schema for ingestion tool."""
    file_path: str = Field(description="Path to the file to ingest")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Optional metadata dictionary")
    force_reprocess: bool = Field(default=False, description="Force reprocessing even if duplicate")


class IngestionOutput(BaseModel):
    """Output schema for ingestion tool."""
    success: bool = Field(description="Whether ingestion was successful")
    file_id: str = Field(description="Unique identifier for the ingested file")
    filename: str = Field(description="Original filename")
    status: str = Field(description="Processing status")
    message: str = Field(description="Status message")
    duplicate_detected: bool = Field(default=False, description="Whether duplicate was detected")
    error: Optional[str] = Field(default=None, description="Error message if failed")


async def ingest_document(
    file_path: str,
    metadata: Optional[Dict[str, Any]] = None,
    force_reprocess: bool = False,
) -> IngestionOutput:
    """
    Ingest and process a document file.
    
    This tool uploads a document to the ingestion service for processing.
    The document will be:
    1. Uploaded to storage
    2. Parsed for text extraction
    3. Chunked into semantic segments
    4. Embedded for semantic search
    5. Indexed in the vector database
    
    Args:
        file_path: Path to the file to ingest
        metadata: Optional metadata to attach to the document
        force_reprocess: Force reprocessing even if content hash matches existing document
        
    Returns:
        IngestionOutput with file_id and processing status
        
    Note:
        Processing happens asynchronously. Use the file_id to check status later.
    """
    try:
        async with IngestClient() as client:
            response = await client.upload_document(
                file_path=file_path,
                metadata=metadata,
                force_reprocess=force_reprocess,
            )
        
        return IngestionOutput(
            success=True,
            file_id=response.file_id,
            filename=response.filename,
            status=response.status,
            message=response.message or "Document uploaded successfully",
            duplicate_detected=response.duplicate_detected,
        )
    
    except FileNotFoundError as e:
        return IngestionOutput(
            success=False,
            file_id="",
            filename="",
            status="failed",
            message="File not found",
            error=str(e),
        )
    
    except Exception as e:
        return IngestionOutput(
            success=False,
            file_id="",
            filename="",
            status="failed",
            message="Ingestion failed",
            error=str(e),
        )


# Create the Pydantic AI tool
ingestion_tool = Tool(
    ingest_document,
    takes_ctx=False,
    name="ingest_document",
    description="""Ingest and process a document file for analysis and search.
Use this tool when:
- You need to process a new document for analysis
- A user uploads a file that needs to be indexed
- You want to add a document to the searchable knowledge base

The tool handles:
- PDF, DOCX, TXT, MD, and other text formats
- Text extraction and parsing
- Semantic chunking
- Embedding generation
- Vector database indexing

Returns a file_id that can be used to reference the document in searches.""",
)








