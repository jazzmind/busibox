# Feature Specification: Production-Grade Document Ingestion Service

**Feature Branch**: `004-updated-ingestion-service`  
**Created**: 2025-11-05  
**Status**: Draft  
**Input**: User description: "Updated ingestion service with multi-vector hybrid search support for production-grade document processing"

## Clarifications

### Session 2025-11-05

- Q: What is the maximum file size limit and how should the system handle large files? → A: Unlimited upload size using chunked strategy and stream processing
- Q: What is the processing timeout duration? → A: Dynamic timeout based on file size (small=5min, medium=10min, large=20min)
- Q: How should the system handle duplicate file uploads? → A: Allow duplicates with deduplication at storage level using content hash (SHA-256)
- Q: Should the system reprocess duplicate files or reuse existing vectors? → A: Reuse existing vectors for duplicate content (same SHA-256 hash)
- Q: How should the system handle mixed-language documents? → A: Detect primary language for classification, store multiple detected languages, language-aware chunking

## User Scenarios & Testing *(mandatory)*

### User Story 1 - File Upload with Real-Time Status Tracking (Priority: P1)

A user needs to upload documents to the system and immediately see the progress of processing without polling or waiting for email notifications. The system provides real-time feedback at each stage of processing so users know exactly when their documents become searchable.

**Why this priority**: This is the entry point for all document processing. Without reliable upload and status tracking, users cannot feed content into the system or know when it's ready for use. Real-time feedback prevents user frustration and reduces support burden.

**Independent Test**: Can be fully tested by uploading a test document through the API, establishing a status connection, and verifying that progress updates stream in real-time through all processing stages (queued → parsing → chunking → embedding → completed).

**Acceptance Scenarios**:

1. **Given** an authenticated user with upload permissions, **When** user uploads a document file, **Then** system stores the file securely, returns a unique file identifier, and sets initial status to "queued"
2. **Given** a file upload has completed, **When** user connects to status tracking endpoint with file identifier, **Then** system immediately sends current status and continues streaming updates as processing progresses
3. **Given** document processing is in progress, **When** each processing stage completes, **Then** user receives status update with stage name, progress percentage, and relevant metrics (chunks created, vectors generated, etc.)
4. **Given** processing completes successfully, **When** final status update is sent, **Then** status includes total processing time, vector count, and confirmation that document is searchable

---

### User Story 2 - Multi-Format Document Support with Visual Content (Priority: P2)

A user uploads documents in various formats including PDFs with charts and tables, Word documents, plain text files, and structured data. The system extracts content appropriately for each format, preserving visual information from PDFs (charts, diagrams, tables) alongside text content to enable comprehensive search later.

**Why this priority**: Real-world documents come in many formats with mixed content types. Without broad format support and visual content extraction, users lose access to critical information embedded in charts, tables, and diagrams. This directly impacts search quality and usefulness.

**Independent Test**: Can be tested by uploading sample documents in each supported format (PDF with charts, DOCX, TXT, CSV, JSON) and verifying that content is extracted correctly, visual elements are captured, and resulting data enables effective search across both text and visual content.

**Acceptance Scenarios**:

1. **Given** a user uploads a PDF document containing text, charts, and tables, **When** processing completes, **Then** system extracts both text content and visual page representations, enabling search across both modalities
2. **Given** a user uploads a Word document with formatted content, **When** processing extracts text, **Then** section structure, headings, and formatting context are preserved for better chunking
3. **Given** a user uploads structured data (CSV, JSON), **When** processing completes, **Then** structured content is parsed and indexed appropriately for field-specific search
4. **Given** a PDF contains scanned pages without embedded text, **When** processing detects this, **Then** system performs optical character recognition to extract text content

---

### User Story 3 - Intelligent Metadata Extraction and Classification (Priority: P3)

When documents are uploaded, the system automatically extracts metadata (title, author, date, keywords) and classifies document type (report, email, code, article) and language. This metadata enhances search relevance and enables users to filter and organize their document corpus effectively.

**Why this priority**: Manual metadata entry is time-consuming and often skipped. Automatic extraction and classification improves search quality, enables filtering, and helps users organize large document collections without manual effort.

**Independent Test**: Can be tested by uploading documents with embedded metadata (PDFs with author/title fields) and various document types, then verifying that extracted metadata is accurate and classification confidence meets acceptable thresholds.

**Acceptance Scenarios**:

1. **Given** a PDF document with embedded metadata, **When** processing extracts metadata, **Then** title, author, creation date, and keywords are accurately identified and stored
2. **Given** a document is uploaded, **When** classification runs, **Then** document type (report, article, email, code, etc.) is identified with confidence score above 80%
3. **Given** a document in a non-English language, **When** language detection runs, **Then** language is correctly identified and stored for language-specific search optimization
4. **Given** extracted metadata exists, **When** users search documents, **Then** metadata enhances search relevance and enables metadata-based filtering

---

### User Story 4 - Optimized Content Chunking for Hybrid Search (Priority: P4)

The system intelligently divides documents into semantic chunks optimized for both keyword-based and semantic search. Chunking respects natural boundaries (paragraphs, sections) while maintaining appropriate size and overlap for effective retrieval across different search methods.

**Why this priority**: Poor chunking leads to broken context, missed search results, and irrelevant answers. Optimized chunking ensures that both traditional keyword search and modern semantic search find relevant content effectively.

**Independent Test**: Can be tested by uploading documents with clear section structure, verifying that chunks respect semantic boundaries, and confirming that chunk sizes fall within optimal ranges (400-800 tokens with 10-15% overlap).

**Acceptance Scenarios**:

1. **Given** a document with clear section headings, **When** chunking processes content, **Then** chunks preferentially break at section boundaries rather than mid-paragraph
2. **Given** text is being chunked, **When** chunk size is calculated, **Then** chunks fall within 400-800 token range with 10-15% overlap between adjacent chunks
3. **Given** a PDF document, **When** chunking occurs, **Then** page numbers are preserved with each chunk for visual content alignment
4. **Given** chunks are created, **When** metadata is stored, **Then** each chunk includes character offset, section heading (if available), and parent document reference

---

### User Story 5 - Multi-Vector Search Preparation (Priority: P5)

The system prepares documents for hybrid search by generating multiple types of search vectors: semantic embeddings for meaning-based search, keyword-based search structures for exact matches, and visual representations for PDF pages with charts and diagrams. This multi-vector approach enables comprehensive search that combines the strengths of different search methods.

**Why this priority**: Single-vector search (semantic only) misses exact term matches; keyword-only search misses conceptual matches. Combining multiple vector types delivers superior search results across diverse document types and query patterns.

**Independent Test**: Can be tested by verifying that processing generates all required vector types for appropriate content (dense embeddings for all text, keyword structures for searchable text, visual vectors for PDF pages), and confirming vectors are stored correctly in the search system.

**Acceptance Scenarios**:

1. **Given** text chunks are ready for vectorization, **When** embedding generation runs, **Then** dense semantic embeddings are generated with dimension appropriate to selected model
2. **Given** text content is indexed, **When** keyword search structures are created, **Then** text is analyzed and tokenized for exact and fuzzy keyword matching
3. **Given** a PDF document is processed, **When** page images are extracted, **Then** visual representations are generated that capture charts, tables, and layout for visual search
4. **Given** all vectors are generated, **When** storage occurs, **Then** vectors are linked to original content with appropriate metadata for result retrieval

---

### User Story 6 - Processing Failure Recovery and Error Reporting (Priority: P6)

When document processing encounters errors (corrupted files, unsupported formats, service timeouts), the system handles failures gracefully, provides clear error messages to users, and retries transient errors automatically without user intervention.

**Why this priority**: Processing failures are inevitable (network issues, corrupted files, resource constraints). Graceful error handling prevents lost documents, reduces user frustration, and minimizes support burden.

**Independent Test**: Can be tested by uploading intentionally corrupted files, simulating service failures, and verifying that system provides clear error messages, retries transient failures, and marks permanent failures without data loss.

**Acceptance Scenarios**:

1. **Given** a user uploads a corrupted file, **When** processing detects corruption, **Then** status updates to "failed" with clear error message explaining the issue
2. **Given** processing encounters a temporary service failure (network timeout), **When** error occurs, **Then** system automatically retries with exponential backoff up to 3 attempts
3. **Given** processing fails permanently (unsupported format), **When** maximum retries are exhausted, **Then** file remains stored, error is logged, and user receives actionable error message
4. **Given** processing partially succeeds (20 of 25 chunks completed), **When** failure occurs, **Then** system saves progress and resumes from last successful chunk on retry

---

### User Story 7 - Scalable Concurrent Processing (Priority: P7)

The system handles multiple concurrent document uploads and processing jobs efficiently, distributing work across available resources without blocking or timing out. Users experience consistent processing times regardless of system load.

**Why this priority**: Real-world usage involves multiple users uploading documents simultaneously. Without efficient concurrent processing, the system becomes a bottleneck, leading to long wait times and poor user experience.

**Independent Test**: Can be tested by uploading multiple documents simultaneously (10-50 concurrent uploads), verifying that all are processed successfully, and measuring that processing times remain within acceptable ranges regardless of load.

**Acceptance Scenarios**:

1. **Given** multiple users upload documents simultaneously, **When** uploads are received, **Then** all files are accepted and queued without rejections or timeouts
2. **Given** processing queue has pending jobs, **When** workers are available, **Then** jobs are distributed evenly across workers to maximize throughput
3. **Given** system is under heavy load, **When** processing times are measured, **Then** average processing time increases by less than 50% compared to no-load conditions
4. **Given** processing capacity is reached, **When** new uploads arrive, **Then** files are queued with estimated processing time communicated to users

---

### Edge Cases

- Extremely large files (>1GB) are handled using chunked upload with stream processing - the system processes content as it streams in, minimizing memory footprint and enabling progress tracking during upload
- Duplicate file uploads (same content) are allowed and tracked separately per user, but storage is deduplicated using SHA-256 content hash - each user gets their own file record and processing status, while physical storage is shared for identical content
- Mixed-language documents (e.g., English with Chinese sections) are detected and stored with primary language plus all detected languages - language-aware chunking maintains language boundaries where feasible to optimize search quality
- Complex nested structures (ZIP files containing PDFs) are not supported in this iteration - users must extract files before upload; future versions may support archive expansion
- Embedding service unavailability during processing triggers automatic retry with exponential backoff (up to 3 attempts per FR-032); job fails if service remains unavailable after retries
- Image-only PDFs without extractable text trigger OCR processing automatically (FR-015); if OCR also fails, document is marked as failed with actionable error message
- User permission changes during processing: permissions are captured and frozen at upload time; documents retain original permission context throughout processing pipeline
- Malformed or malicious file uploads are detected during format validation (FR-002) and rejected; processing occurs in sandboxed environment to prevent system compromise
- Vector storage capacity approaching limits triggers health check degradation (503 status); monitoring alerts are generated; new ingestion jobs are queued but not processed until capacity is available
- Processing jobs that exceed dynamic timeout limits (5/10/20 minutes based on document size) are marked as failed with timeout error and can be manually retried

## Requirements *(mandatory)*

### Functional Requirements

#### File Upload and Storage

- **FR-001**: System MUST accept document uploads in multiple formats including PDF, DOCX, TXT, HTML, Markdown, CSV, and JSON
- **FR-002**: System MUST validate file format before accepting uploads and MUST support unlimited file sizes using chunked upload strategy with stream processing to manage memory efficiently
- **FR-003**: System MUST generate a unique identifier for each uploaded file and return it immediately to the user
- **FR-004**: System MUST calculate SHA-256 content hash for each uploaded file and use content-based deduplication at storage level to minimize storage costs while maintaining separate file records per user
- **FR-005**: System MUST store uploaded files securely with user ownership and permission information
- **FR-006**: System MUST accept upload requests only from authorized internal services with appropriate user context

#### Real-Time Status Tracking

- **FR-007**: System MUST provide a status tracking mechanism that streams real-time updates as processing progresses
- **FR-008**: System MUST update status at each major processing stage: queued, parsing, classifying, extracting metadata, chunking, generating embeddings, indexing, completed, or failed
- **FR-009**: Status updates MUST include progress percentage and stage-specific metrics (chunks created, vectors generated, etc.)
- **FR-010**: System MUST support multiple concurrent status connections for the same file identifier
- **FR-011**: System MUST automatically close status connections when processing completes or after timeout period

#### Document Processing Pipeline

- **FR-012**: System MUST check for existing processed content using SHA-256 hash and reuse vectors if found, skipping processing pipeline for duplicate content
- **FR-013**: System MUST extract text content from uploaded files using format-appropriate parsing methods when processing new content
- **FR-014**: For PDF files containing charts and tables, system MUST extract both text content and visual page representations
- **FR-015**: System MUST perform optical character recognition on scanned PDFs that lack embedded text
- **FR-016**: System MUST automatically classify documents by type (report, article, email, code, etc.) with confidence scoring
- **FR-017**: System MUST detect primary language for document classification and store all detected languages (for mixed-language documents) to enable language-specific search optimization
- **FR-018**: System MUST extract embedded metadata (title, author, date, keywords) from document files when available
- **FR-019**: System MUST chunk text content into segments of 400-800 tokens with 10-15% overlap between adjacent chunks, using language-aware chunking when multiple languages are detected
- **FR-020**: Chunking MUST respect natural semantic boundaries (paragraphs, sections) when possible
- **FR-021**: System MUST preserve page numbers, section headings, and character offsets with each chunk
- **FR-022**: System MUST generate dense semantic embeddings for all text chunks using a configured embedding model
- **FR-023**: For PDF documents, system MUST generate visual representations of pages that capture charts, tables, and layout
- **FR-024**: System MUST store text embeddings, keyword search structures, and visual representations linked to content hash for reuse across duplicate uploads

#### Metadata and Entity Storage

- **FR-025**: System MUST store file metadata including filename, size, format, content hash (SHA-256), upload user, permissions, and timestamps
- **FR-026**: System MUST store document classification (type and confidence), primary language, all detected languages (for mixed-language docs), and extracted metadata
- **FR-027**: System MUST store processing metrics including chunk count, vector count, and processing duration
- **FR-028**: System MUST store chunk-level metadata including index, text content, character offset, and page number
- **FR-029**: System MUST link file records to shared content hash for vector reuse while maintaining separate per-user file metadata
- **FR-030**: All stored data MUST be associated with user ownership and permission information for access control

#### Error Handling and Recovery

- **FR-031**: System MUST detect corrupted or malformed files during processing and mark them as failed with descriptive error messages
- **FR-032**: System MUST automatically retry transient processing failures (network timeouts, service unavailability) up to 3 times with exponential backoff
- **FR-033**: System MUST NOT retry permanent failures (unsupported format, corrupted file) and MUST mark status as permanently failed
- **FR-034**: For partial processing failures, system MUST save progress and resume from last successful step on retry
- **FR-035**: System MUST log all processing errors with sufficient context for debugging (file identifier, stage, error details)
- **FR-036**: System MUST prevent processing jobs from running indefinitely by enforcing dynamic processing time limits based on document size: small documents (<10 pages) timeout at 5 minutes, medium documents (10-50 pages) timeout at 10 minutes, large documents (>50 pages) timeout at 20 minutes. Page count is estimated during the parsing stage (FR-013) and used to calculate timeout before processing begins. For non-PDF formats without explicit pages, timeout defaults to medium (10 minutes).

#### Concurrent Processing and Scalability

- **FR-037**: System MUST queue processing jobs in a reliable queue that supports multiple concurrent workers
- **FR-038**: System MUST distribute queued jobs across available workers to maximize throughput
- **FR-039**: System MUST handle at least 50 concurrent file uploads without rejections or errors
- **FR-040**: System MUST process documents in batches (embedding generation, vector storage) to optimize resource usage
- **FR-041**: System MUST support horizontal scaling by adding additional worker processes or containers

#### Security and Access Control

- **FR-042**: System MUST enforce that only authorized internal services can upload files on behalf of users
- **FR-043**: All file operations MUST validate user ownership and permissions before execution
- **FR-044**: System MUST propagate user permission information through entire processing pipeline to vector storage
- **FR-045**: System MUST redact sensitive information from logs (file contents, user credentials)

### Key Entities

- **Ingestion File**: Represents an uploaded document with metadata including unique identifier, original filename, file format, size, content hash (SHA-256), storage location, user ownership, permissions, upload timestamp, and processing status
- **Processing Status**: Tracks real-time processing state for a file including current stage, progress percentage, stage-specific metrics (chunks processed, vectors generated), error messages, and timestamps
- **Document Classification**: Contains document type (report, article, email, code), primary language identifier, all detected languages (array for mixed-language docs), and confidence scores from classification process
- **Extracted Metadata**: Includes document title, author, creation date, keywords, and summary information extracted from document
- **Text Chunk**: Represents a segment of document text with chunk index, actual text content, character offset in original document, token count, section heading, page number, and link to parent file
- **Search Vector**: Contains embedding type (dense semantic, keyword structure, or visual representation), vector data, dimensionality, model identifier, and link to source chunk or page
- **Processing Job**: Represents a queued or in-progress processing task with job identifier, file identifier, user context, job type, creation time, start time, and completion time

## Success Criteria *(mandatory)*

### Measurable Outcomes

#### Upload and Status Tracking

- **SC-001**: Users receive file identifier and initial status within 2 seconds of upload completion
- **SC-002**: Status updates stream to users within 2 seconds of each processing stage completion
- **SC-003**: 100% of uploaded files receive final status (completed or failed) - no files remain in "processing" indefinitely
- **SC-004**: Status tracking supports at least 100 concurrent connections without degradation

#### Processing Performance

- **SC-005**: Small documents (< 10 pages) complete processing within 2 minutes (5 minute timeout); duplicate content completes within 10 seconds using vector reuse
- **SC-006**: Medium documents (10-50 pages) complete processing within 5 minutes (10 minute timeout); duplicate content completes within 10 seconds using vector reuse
- **SC-007**: Large documents (50-200 pages) complete processing within 15 minutes (20 minute timeout); duplicate content completes within 10 seconds using vector reuse
- **SC-008**: System maintains processing performance within 50% of baseline when handling 50 concurrent uploads

#### Processing Quality

- **SC-009**: Text extraction accuracy exceeds 95% for standard document formats (PDF, DOCX, TXT)
- **SC-010**: Document classification confidence exceeds 80% for common document types
- **SC-011**: Primary language detection accuracy exceeds 95% for supported languages; all languages present in mixed-language docs are identified with at least 90% accuracy
- **SC-012**: Chunk boundaries align with semantic structure (paragraphs, sections) in at least 80% of cases, and respect language boundaries in mixed-language documents when feasible
- **SC-013**: All processing stages complete successfully for at least 95% of valid uploads

#### Error Handling

- **SC-014**: Transient processing errors (network timeouts) result in successful retry within 30 seconds for at least 90% of cases
- **SC-015**: Permanent processing errors (corrupted files) are detected and marked as failed within 1 minute with clear error message
- **SC-016**: Processing failures preserve uploaded files and metadata for manual review or retry
- **SC-017**: Error messages provide actionable information (e.g., "File is corrupted" vs "Processing failed")

#### System Reliability

- **SC-018**: System maintains uptime of 99.9% for upload endpoint (< 9 hours downtime per year)
- **SC-019**: No data loss occurs during processing failures - all uploaded files remain retrievable
- **SC-020**: Processing queue does not grow unbounded - queue depth remains below 1000 pending jobs under normal operation
- **SC-021**: System gracefully handles resource exhaustion by queueing jobs rather than rejecting uploads

## Assumptions

- Internal services calling the ingestion API will handle user authentication and pass validated user context in headers
- Chunked upload and stream processing enable unlimited file sizes without memory constraints
- Content-based deduplication using SHA-256 hashing minimizes storage costs while maintaining separate user-specific file records
- Vector reuse for duplicate content (same SHA-256 hash) reduces compute costs and processing time significantly
- Embedding and visual processing services (liteLLM, ColPali) are deployed separately and accessible via network
- Vector storage system (Milvus) is deployed separately and accessible via network
- Metadata storage system (PostgreSQL) is deployed separately and accessible via network
- Object storage for files (MinIO) is deployed separately and accessible via network
- Maximum concurrent uploads are bounded by upstream rate limiting (assumed ~100 uploads/minute)
- Documents are primarily text-based (PDF, DOCX, TXT); image-heavy documents may require additional processing time
- Processing timeouts are enforced dynamically based on document size (5/10/20 minutes for small/medium/large) to prevent resource exhaustion while allowing sufficient time for large documents
- Retry logic assumes transient failures resolve within 1-2 minutes (network timeouts, temporary service unavailability)
- Queue system will persist jobs across worker restarts to prevent job loss
- Visual representations (ColPali) are generated only for PDF documents; other formats use text-only processing
- Metadata extraction uses embedded document properties where available; missing metadata is acceptable
- Classification and language detection use heuristic methods supplemented by external services when needed
- Mixed-language documents store primary language (most prevalent) plus array of all detected languages for search optimization
- Language-aware chunking attempts to maintain language boundaries but prioritizes semantic coherence when languages are tightly interwoven
