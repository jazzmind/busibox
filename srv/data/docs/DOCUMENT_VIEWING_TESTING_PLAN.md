# Document Viewing System - Testing Plan

## Overview
Comprehensive test coverage for markdown storage, image extraction, and HTML rendering functionality.

## Phase 1: Backend Storage Tests

### 1. Database Migration Tests (`tests/test_markdown_storage_migration.py`)

**Test Cases:**
- `test_migration_adds_markdown_columns` - Verify all new columns exist
- `test_migration_is_idempotent` - Can run multiple times without errors
- `test_markdown_path_default_null` - New rows have null markdown_path by default
- `test_has_markdown_default_false` - New rows have has_markdown=false by default
- `test_image_count_default_zero` - New rows have image_count=0 by default

### 2. Markdown Generation Tests (`tests/test_markdown_generator.py`)

**Test Cases:**
- `test_generate_markdown_from_simple_text` - Convert plain text to markdown
- `test_generate_markdown_with_headings` - Preserve heading structure
- `test_generate_markdown_with_tables` - Convert tables to markdown format
- `test_generate_markdown_with_lists` - Preserve list formatting
- `test_generate_markdown_with_images` - Insert image references
- `test_markdown_sanitization` - Remove/escape dangerous content
- `test_markdown_from_marker_output` - Handle Marker-specific formatting
- `test_markdown_metadata_extraction` - Extract document title from content

### 3. Image Extraction Tests (`tests/test_image_extractor.py`)

**Test Cases:**
- `test_extract_images_from_pdf` - Extract all images from PDF
- `test_extract_images_from_docx` - Extract images from Word docs
- `test_image_format_conversion` - Convert to standard format (PNG)
- `test_image_naming_convention` - Verify naming (image_0.png, image_1.png)
- `test_no_images_in_document` - Handle documents without images
- `test_image_quality_preservation` - Verify resolution/quality
- `test_large_image_handling` - Handle large/high-res images
- `test_corrupted_image_handling` - Skip/log corrupted images

### 4. MinIO Storage Tests (`tests/test_minio_markdown_storage.py`)

**Test Cases:**
- `test_store_markdown_in_minio` - Upload markdown file to MinIO
- `test_retrieve_markdown_from_minio` - Download markdown file
- `test_markdown_path_format` - Verify path: {user_id}/{file_id}/content.md
- `test_store_images_in_minio` - Upload images directory
- `test_images_path_format` - Verify path: {user_id}/{file_id}/images/
- `test_list_images_from_minio` - List all images for a document
- `test_minio_error_handling` - Handle connection errors
- `test_markdown_overwrite_existing` - Update existing markdown

### 5. Worker Integration Tests (`tests/integration/test_markdown_pipeline.py`)

**Test Cases:**
- `test_end_to_end_markdown_generation_simple` - Full pipeline with Simple strategy
- `test_end_to_end_markdown_generation_marker` - Full pipeline with Marker strategy
- `test_markdown_stored_after_parsing` - Verify markdown saved after parsing stage
- `test_database_updated_with_paths` - Verify ingestion_files updated
- `test_images_extracted_and_stored` - Verify images saved to MinIO
- `test_processing_history_logs_markdown_steps` - History includes markdown steps
- `test_markdown_failure_doesnt_break_pipeline` - Continue on markdown error
- `test_reprocess_updates_markdown` - Reprocessing regenerates markdown

### 6. API Endpoint Tests (`tests/api/test_markdown_endpoints.py`)

**Test Cases:**

#### GET /files/{fileId}/markdown
- `test_get_markdown_success` - Returns markdown content
- `test_get_markdown_not_found` - 404 when file doesn't exist
- `test_get_markdown_unauthorized` - 403 when not file owner
- `test_get_markdown_not_generated` - 404 when markdown_path is null
- `test_get_markdown_includes_metadata` - Returns hasImages, imageCount

#### GET /files/{fileId}/html
- `test_get_html_success` - Returns rendered HTML
- `test_get_html_with_toc` - Includes table of contents
- `test_get_html_not_found` - 404 when file doesn't exist
- `test_get_html_unauthorized` - 403 when not file owner
- `test_get_html_headings_to_anchors` - Headings have IDs
- `test_get_html_image_urls_resolved` - Image src points to correct endpoint
- `test_get_html_sanitized` - No XSS vulnerabilities

#### GET /files/{fileId}/images/{index}
- `test_get_image_success` - Returns image binary
- `test_get_image_not_found` - 404 when image doesn't exist
- `test_get_image_unauthorized` - 403 when not file owner
- `test_get_image_correct_content_type` - Content-Type: image/png
- `test_get_image_invalid_index` - 400 for invalid index
- `test_get_image_range_requests` - Support partial content (206)

### 7. HTML Rendering Tests (`tests/test_html_renderer.py`)

**Test Cases:**
- `test_markdown_to_html_conversion` - Basic conversion
- `test_html_toc_generation` - Generate TOC from headings
- `test_html_heading_ids` - Headings have unique IDs
- `test_html_image_src_replacement` - Images point to API endpoint
- `test_html_table_styling` - Tables have proper CSS classes
- `test_html_code_block_syntax_highlighting` - Code blocks formatted
- `test_html_sanitization` - Remove script tags, dangerous attributes
- `test_html_responsive_images` - Images have max-width styling
- `test_html_toc_nested_structure` - TOC respects heading hierarchy

### 8. Error Handling Tests (`tests/test_markdown_error_handling.py`)

**Test Cases:**
- `test_markdown_generation_failure_logged` - Errors logged to history
- `test_minio_upload_failure_retry` - Retry transient upload errors
- `test_image_extraction_partial_failure` - Continue if some images fail
- `test_corrupted_pdf_markdown_fallback` - Use simple extraction as fallback
- `test_markdown_timeout_handling` - Timeout for very large documents
- `test_minio_storage_full` - Handle storage quota errors

## Phase 2: Frontend Tests (AI Portal)

### 9. Document Detail Page Tests (`busibox-portal/src/app/documents/[fileId]/__tests__/page.test.tsx`)

**Test Cases:**
- `test_overview_tab_default` - Overview tab shown by default
- `test_content_tab_renders_html` - Content tab shows HTML
- `test_content_tab_shows_toc` - TOC displayed and functional
- `test_metadata_tab_preserved` - Existing metadata tab works
- `test_processing_tab_preserved` - Processing history tab works
- `test_search_bar_above_tabs` - Search bar in correct position
- `test_stat_boxes_display` - Pages, chunks, words, time shown
- `test_markdown_not_available_message` - Message when markdown_path is null
- `test_reprocess_button_works` - Can trigger reprocessing

### 10. Chunks Browsing Page Tests (`busibox-portal/src/app/documents/[fileId]/chunks/__tests__/page.test.tsx`)

**Test Cases:**
- `test_chunks_page_renders` - Page loads successfully
- `test_chunks_pagination_works` - Can navigate pages
- `test_chunks_filter_by_page` - Filter chunks by page number
- `test_chunks_filter_by_section` - Filter by section heading
- `test_chunks_search` - Search within chunk text
- `test_chunks_navigation_from_detail` - Link from detail page works
- `test_chunks_empty_state` - Message when no chunks exist
- `test_chunks_highlight_search_terms` - Search terms highlighted

### 11. API Proxy Tests (`busibox-portal/src/app/api/documents/[fileId]/__tests__/`)

**Test Cases:**

#### /markdown/route.test.ts
- `test_markdown_proxy_success` - Proxies to ingest service
- `test_markdown_proxy_error_handling` - Handles ingest errors
- `test_markdown_proxy_auth` - Passes auth headers

#### /html/route.test.ts
- `test_html_proxy_success` - Returns HTML from ingest
- `test_html_proxy_caching` - Caches HTML responses
- `test_html_proxy_error_handling` - Handles errors

#### /images/[index]/route.test.ts
- `test_image_proxy_success` - Proxies image requests
- `test_image_proxy_content_type` - Sets correct headers
- `test_image_proxy_streaming` - Streams large images

## Test Fixtures

### Required Test Files
- `samples/sample.pdf` - Simple PDF with text and images
- `samples/complex.pdf` - PDF with tables, images, multiple sections
- `samples/no-images.pdf` - PDF without images
- `samples/diagram.pdf` - PDF with technical diagrams (existing)
- `samples/sample.docx` - Word document with images

### Mock Services
- Mock MinIO client for storage operations
- Mock Marker service responses
- Mock ColPali service responses
- Mock PostgreSQL for database tests

## Test Coverage Goals

- **Unit Tests**: 80%+ coverage for new modules
- **Integration Tests**: All critical paths tested
- **API Tests**: 100% endpoint coverage
- **Frontend Tests**: 70%+ component coverage

## Test Execution

```bash
# Run all markdown-related tests
pytest tests/test_markdown*.py -v

# Run with coverage
pytest tests/test_markdown*.py --cov=src/processors/markdown --cov-report=html

# Run integration tests only
pytest tests/integration/test_markdown_pipeline.py -v

# Run API endpoint tests
pytest tests/api/test_markdown_endpoints.py -v

# Frontend tests
cd busibox-portal
npm test -- document
```

## Continuous Integration

All tests should run in CI/CD:
1. Database migrations applied to test DB
2. MinIO test instance running
3. Integration tests with real services
4. Frontend tests with mock API responses
5. End-to-end smoke tests on test environment


