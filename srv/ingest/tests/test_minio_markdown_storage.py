"""
Tests for MinIO Markdown and Image Storage
"""

import pytest
from api.services.minio_service import MinIOService
from shared.config import Config


class TestMinIOMarkdownStorage:
    """Test suite for MinIO markdown and image storage"""

    @pytest.fixture
    def minio_service(self):
        """Create MinIO service instance"""
        config = Config().to_dict()
        return MinIOService(config)

    @pytest.mark.asyncio
    async def test_store_and_retrieve_markdown(self, minio_service):
        """Test storing and retrieving markdown content"""
        test_markdown = """# Test Document

This is a test markdown document.

## Section 1
Content here."""
        
        object_path = "test/test-file-123/content.md"
        
        # Upload markdown
        await minio_service.upload_text(test_markdown, object_path)
        
        # Retrieve markdown
        retrieved_content = minio_service.get_file_content(object_path)
        
        assert retrieved_content == test_markdown
        
        # Cleanup
        await minio_service.delete_file(object_path)

    @pytest.mark.asyncio
    async def test_store_and_retrieve_image(self, minio_service):
        """Test storing and retrieving image data"""
        # Create a simple test image (1x1 pixel PNG)
        test_image_data = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde'
        
        object_path = "test/test-file-123/images/image_0.png"
        
        # Upload image
        await minio_service.upload_bytes(test_image_data, object_path, content_type='image/png')
        
        # Retrieve image
        retrieved_data = minio_service.get_file_bytes(object_path)
        
        assert retrieved_data == test_image_data
        
        # Cleanup
        await minio_service.delete_file(object_path)

    @pytest.mark.asyncio
    async def test_markdown_path_format(self, minio_service):
        """Test that markdown follows correct path format"""
        user_id = "user-123"
        file_id = "file-456"
        expected_path = f"{user_id}/{file_id}/content.md"
        
        test_content = "# Test"
        
        await minio_service.upload_text(test_content, expected_path)
        
        # Verify file exists at expected path
        exists = await minio_service.file_exists(expected_path)
        assert exists is True
        
        # Cleanup
        await minio_service.delete_file(expected_path)

    @pytest.mark.asyncio
    async def test_images_path_format(self, minio_service):
        """Test that images follow correct path format"""
        user_id = "user-123"
        file_id = "file-456"
        image_index = 0
        expected_path = f"{user_id}/{file_id}/images/image_{image_index}.png"
        
        test_image = b'\x89PNG\r\n\x1a\n'  # Minimal PNG header
        
        await minio_service.upload_bytes(test_image, expected_path, content_type='image/png')
        
        # Verify file exists at expected path
        exists = await minio_service.file_exists(expected_path)
        assert exists is True
        
        # Cleanup
        await minio_service.delete_file(expected_path)

    @pytest.mark.asyncio
    async def test_error_handling_nonexistent_file(self, minio_service):
        """Test error handling when retrieving non-existent file"""
        with pytest.raises(Exception):  # Should raise S3Error
            minio_service.get_file_content("nonexistent/path/file.md")

    @pytest.mark.asyncio
    async def test_overwrite_existing_markdown(self, minio_service):
        """Test overwriting existing markdown content"""
        object_path = "test/test-file-123/content.md"
        
        # Upload original content
        original = "# Original Content"
        await minio_service.upload_text(original, object_path)
        
        # Verify original
        retrieved = minio_service.get_file_content(object_path)
        assert retrieved == original
        
        # Overwrite with new content
        updated = "# Updated Content\n\nNew information."
        await minio_service.upload_text(updated, object_path)
        
        # Verify updated
        retrieved_updated = minio_service.get_file_content(object_path)
        assert retrieved_updated == updated
        assert retrieved_updated != original
        
        # Cleanup
        await minio_service.delete_file(object_path)

    @pytest.mark.asyncio
    async def test_multiple_images_storage(self, minio_service):
        """Test storing multiple images for the same document"""
        user_id = "user-123"
        file_id = "file-456"
        
        images = [
            (b'\x89PNG\r\n\x1a\n\x00\x00', 0),
            (b'\x89PNG\r\n\x1a\n\x01\x01', 1),
            (b'\x89PNG\r\n\x1a\n\x02\x02', 2),
        ]
        
        # Upload all images
        for img_data, index in images:
            path = f"{user_id}/{file_id}/images/image_{index}.png"
            await minio_service.upload_bytes(img_data, path, content_type='image/png')
        
        # Verify all images exist and have correct content
        for img_data, index in images:
            path = f"{user_id}/{file_id}/images/image_{index}.png"
            retrieved = minio_service.get_file_bytes(path)
            assert retrieved == img_data
        
        # Cleanup
        for _, index in images:
            path = f"{user_id}/{file_id}/images/image_{index}.png"
            await minio_service.delete_file(path)

    @pytest.mark.asyncio
    async def test_markdown_encoding_utf8(self, minio_service):
        """Test that markdown correctly handles UTF-8 encoding"""
        test_markdown = """# Document with Special Characters

- Emoji: 🎉 ✨ 🚀
- Accents: café, naïve, résumé
- Symbols: © ® ™ € £ ¥
- Math: ∑ ∫ ∂ √ ∞
"""
        
        object_path = "test/test-file-123/content.md"
        
        # Upload
        await minio_service.upload_text(test_markdown, object_path)
        
        # Retrieve
        retrieved = minio_service.get_file_content(object_path)
        
        assert retrieved == test_markdown
        assert "🎉" in retrieved
        assert "café" in retrieved
        assert "∑" in retrieved
        
        # Cleanup
        await minio_service.delete_file(object_path)

    @pytest.mark.asyncio
    async def test_large_markdown_content(self, minio_service):
        """Test storing large markdown documents"""
        # Generate large markdown content (>1MB)
        large_content = "# Large Document\n\n" + ("## Section\n\nLorem ipsum dolor sit amet. " * 50000)
        
        object_path = "test/test-file-123/large-content.md"
        
        # Upload
        await minio_service.upload_text(large_content, object_path)
        
        # Retrieve
        retrieved = minio_service.get_file_content(object_path)
        
        assert len(retrieved) == len(large_content)
        assert retrieved[:50] == large_content[:50]  # Check beginning
        assert retrieved[-50:] == large_content[-50:]  # Check end
        
        # Cleanup
        await minio_service.delete_file(object_path)

    @pytest.mark.asyncio
    async def test_file_exists_check(self, minio_service):
        """Test file existence check functionality"""
        object_path = "test/test-file-123/content.md"
        
        # Initially should not exist
        exists_before = await minio_service.file_exists(object_path)
        assert exists_before is False
        
        # Upload file
        await minio_service.upload_text("# Test", object_path)
        
        # Now should exist
        exists_after = await minio_service.file_exists(object_path)
        assert exists_after is True
        
        # Delete file
        await minio_service.delete_file(object_path)
        
        # Should not exist again
        exists_deleted = await minio_service.file_exists(object_path)
        assert exists_deleted is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

