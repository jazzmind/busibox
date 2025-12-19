"""Integration tests for attachment agent with local LLM."""
import pytest
import json

from app.agents.attachment_agent import attachment_agent


class TestAttachmentAgent:
    """Test attachment agent with real LiteLLM."""

    @pytest.mark.asyncio
    async def test_attachment_agent_no_attachments(self):
        """Test attachment agent handles no attachments."""
        result = await attachment_agent.run("No files attached")
        
        response = str(result).lower()
        assert "none" in response or "no" in response
        print(f"\nAttachment agent (no files): {response}")

    @pytest.mark.asyncio
    async def test_attachment_agent_image_file(self):
        """Test attachment agent recommends upload for images."""
        query = "I have an image file: photo.jpg (image/jpeg, 2.5MB)"
        result = await attachment_agent.run(query)
        
        response = str(result).lower()
        assert "upload" in response or "multimodal" in response or "image" in response
        print(f"\nAttachment agent (image): {response}")

    @pytest.mark.asyncio
    async def test_attachment_agent_pdf_file(self):
        """Test attachment agent recommends upload for PDF."""
        query = "I have a PDF document: report.pdf (application/pdf, 1.2MB)"
        result = await attachment_agent.run(query)
        
        response = str(result).lower()
        assert "upload" in response or "document" in response or "text" in response
        print(f"\nAttachment agent (PDF): {response}")

    @pytest.mark.asyncio
    async def test_attachment_agent_archive_file(self):
        """Test attachment agent recommends preprocessing for archives."""
        query = "I have an archive: data.zip (application/zip, 5MB)"
        result = await attachment_agent.run(query)
        
        response = str(result).lower()
        assert "extract" in response or "preprocess" in response or "archive" in response
        print(f"\nAttachment agent (archive): {response}")








