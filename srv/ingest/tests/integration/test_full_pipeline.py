"""
End-to-End Integration Test for Document Processing Pipeline

This test uploads a REAL document and verifies the ENTIRE pipeline:
1. Upload → 2. Parse → 3. Chunk → 4. Embed → 5. Index → 6. Markdown/Images → 7. Retrieve

If this test passes, the system actually works.
"""

import pytest
import uuid
import time
from pathlib import Path
from httpx import AsyncClient


class TestFullDocumentPipeline:
    """Test the complete document processing pipeline end-to-end"""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_and_process_real_pdf(self, async_client: AsyncClient):
        """
        Upload a real PDF and verify complete processing pipeline.
        This is the ONE test that actually matters.
        """
        # Use a real sample PDF
        sample_pdf = Path(__file__).parent.parent.parent / "samples" / "inthebeginning.pdf"
        
        if not sample_pdf.exists():
            pytest.skip(f"Sample PDF not found: {sample_pdf}")
        
        # Step 1: Upload the document
        with open(sample_pdf, "rb") as f:
            files = {"file": ("test.pdf", f, "application/pdf")}
            response = await async_client.post(
                "/upload",
                files=files,
                data={"metadata": "{}"}
            )
        
        assert response.status_code == 200, f"Upload failed: {response.text}"
        upload_data = response.json()
        file_id = upload_data.get("file_id")
        assert file_id is not None, "No file_id in upload response"
        
        # Step 2: Wait for processing to complete (with timeout)
        max_wait = 120  # 2 minutes max
        start_time = time.time()
        status = None
        
        while time.time() - start_time < max_wait:
            response = await async_client.get(f"/files/{file_id}")
            assert response.status_code == 200, f"Status check failed: {response.text}"
            
            data = response.json()
            status = data.get("status", {}).get("stage")
            
            if status == "completed":
                break
            elif status == "failed":
                error = data.get("status", {}).get("errorMessage", "Unknown error")
                pytest.fail(f"Processing failed: {error}")
            
            time.sleep(2)  # Poll every 2 seconds
        
        assert status == "completed", f"Processing did not complete in {max_wait}s. Last status: {status}"
        
        # Step 3: Verify file metadata
        response = await async_client.get(f"/files/{file_id}")
        assert response.status_code == 200
        metadata = response.json()
        
        # Must have these fields populated
        assert metadata.get("chunkCount", 0) > 0, "No chunks created"
        assert metadata.get("vectorCount", 0) > 0, "No vectors created"
        assert metadata.get("documentType") is not None, "No document type classified"
        assert metadata.get("primaryLanguage") is not None, "No language detected"
        
        # Step 4: Verify chunks are retrievable
        response = await async_client.get(f"/files/{file_id}/chunks?page=1&page_size=10")
        assert response.status_code == 200, f"Chunks retrieval failed: {response.text}"
        chunks_data = response.json()
        assert len(chunks_data.get("chunks", [])) > 0, "No chunks returned"
        
        # Step 5: Verify markdown was generated
        response = await async_client.get(f"/files/{file_id}/markdown")
        assert response.status_code == 200, f"Markdown retrieval failed: {response.text}"
        markdown_data = response.json()
        assert markdown_data.get("markdown") is not None, "No markdown content"
        assert len(markdown_data.get("markdown", "")) > 0, "Markdown is empty"
        
        # Step 6: Verify HTML rendering works
        response = await async_client.get(f"/files/{file_id}/html")
        assert response.status_code == 200, f"HTML retrieval failed: {response.text}"
        html_data = response.json()
        assert html_data.get("html") is not None, "No HTML content"
        assert html_data.get("toc") is not None, "No table of contents"
        assert len(html_data.get("html", "")) > 0, "HTML is empty"
        
        # Step 7: Verify processing history was recorded
        response = await async_client.get(f"/files/{file_id}/history")
        assert response.status_code == 200, f"History retrieval failed: {response.text}"
        history = response.json()
        assert len(history) > 0, "No processing history recorded"
        
        # Verify key stages are present
        stages = {step.get("stage") for step in history}
        required_stages = {"parsing", "chunking", "embedding", "indexing"}
        missing_stages = required_stages - stages
        assert len(missing_stages) == 0, f"Missing stages in history: {missing_stages}"
        
        # Step 8: Verify search works
        response = await async_client.post(
            f"/files/{file_id}/search",
            json={"query": "test", "limit": 5}
        )
        assert response.status_code == 200, f"Search failed: {response.text}"
        search_results = response.json()
        assert "results" in search_results, "No results field in search response"
        
        # Step 9: Verify deletion works
        response = await async_client.delete(f"/files/{file_id}")
        assert response.status_code == 200, f"Deletion failed: {response.text}"
        
        # Verify file is actually deleted
        response = await async_client.get(f"/files/{file_id}")
        assert response.status_code == 404, "File still exists after deletion"
        
        print(f"✅ FULL PIPELINE TEST PASSED - Document {file_id} processed successfully")


    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_reprocess_document(self, async_client: AsyncClient):
        """
        Test that reprocessing a document works correctly.
        """
        # Upload a document
        sample_pdf = Path(__file__).parent.parent.parent / "samples" / "diagram.pdf"
        
        if not sample_pdf.exists():
            pytest.skip(f"Sample PDF not found: {sample_pdf}")
        
        with open(sample_pdf, "rb") as f:
            files = {"file": ("diagram.pdf", f, "application/pdf")}
            response = await async_client.post("/upload", files=files, data={"metadata": "{}"})
        
        assert response.status_code == 200
        file_id = response.json().get("file_id")
        
        # Wait for initial processing
        max_wait = 60
        start_time = time.time()
        while time.time() - start_time < max_wait:
            response = await async_client.get(f"/files/{file_id}")
            if response.json().get("status", {}).get("stage") == "completed":
                break
            time.sleep(2)
        
        # Get initial chunk count
        response = await async_client.get(f"/files/{file_id}")
        initial_data = response.json()
        initial_chunks = initial_data.get("chunkCount", 0)
        
        # Trigger reprocessing
        response = await async_client.post(f"/files/{file_id}/reprocess")
        assert response.status_code == 200, f"Reprocess failed: {response.text}"
        
        # Wait for reprocessing
        start_time = time.time()
        while time.time() - start_time < max_wait:
            response = await async_client.get(f"/files/{file_id}")
            status = response.json().get("status", {}).get("stage")
            if status == "completed":
                break
            time.sleep(2)
        
        # Verify reprocessing completed
        response = await async_client.get(f"/files/{file_id}")
        assert response.status_code == 200
        reprocessed_data = response.json()
        assert reprocessed_data.get("status", {}).get("stage") == "completed"
        
        # Cleanup
        await async_client.delete(f"/files/{file_id}")
        
        print(f"✅ REPROCESS TEST PASSED - Document {file_id} reprocessed successfully")


    @pytest.mark.asyncio
    @pytest.mark.integration  
    async def test_multiple_documents_concurrent(self, async_client: AsyncClient):
        """
        Test processing multiple documents concurrently.
        Verifies the system can handle concurrent load.
        """
        sample_pdfs = [
            Path(__file__).parent.parent.parent / "samples" / "inthebeginning.pdf",
            Path(__file__).parent.parent.parent / "samples" / "diagram.pdf",
        ]
        
        # Filter to existing files
        existing_pdfs = [pdf for pdf in sample_pdfs if pdf.exists()]
        if len(existing_pdfs) < 2:
            pytest.skip("Need at least 2 sample PDFs")
        
        # Upload multiple documents
        file_ids = []
        for pdf in existing_pdfs[:2]:  # Just test 2 for speed
            with open(pdf, "rb") as f:
                files = {"file": (pdf.name, f, "application/pdf")}
                response = await async_client.post("/upload", files=files, data={"metadata": "{}"})
            
            assert response.status_code == 200
            file_ids.append(response.json().get("file_id"))
        
        # Wait for all to complete
        max_wait = 180  # 3 minutes for multiple docs
        start_time = time.time()
        completed = set()
        
        while time.time() - start_time < max_wait and len(completed) < len(file_ids):
            for file_id in file_ids:
                if file_id in completed:
                    continue
                
                response = await async_client.get(f"/files/{file_id}")
                if response.status_code == 200:
                    status = response.json().get("status", {}).get("stage")
                    if status == "completed":
                        completed.add(file_id)
            
            if len(completed) < len(file_ids):
                time.sleep(3)
        
        assert len(completed) == len(file_ids), f"Only {len(completed)}/{len(file_ids)} documents completed"
        
        # Verify all documents are accessible
        for file_id in file_ids:
            response = await async_client.get(f"/files/{file_id}")
            assert response.status_code == 200
            assert response.json().get("chunkCount", 0) > 0
        
        # Cleanup
        for file_id in file_ids:
            await async_client.delete(f"/files/{file_id}")
        
        print(f"✅ CONCURRENT TEST PASSED - {len(file_ids)} documents processed concurrently")
