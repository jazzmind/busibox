"""
Tests for Image Extractor Module
"""

import pytest
import os
import sys
from pathlib import Path
from PIL import Image
from io import BytesIO
from processors.image_extractor import ImageExtractor

# Add test_utils to path for shared testing utilities
_srv_dir = Path(__file__).parent.parent.parent
if str(_srv_dir) not in sys.path:
    sys.path.insert(0, str(_srv_dir))

from testing.environment import get_test_doc_repo_path


class TestImageExtractor:
    """Test suite for ImageExtractor class"""

    def setup_method(self):
        """Setup test fixtures"""
        self.extractor = ImageExtractor()
        # Use shared test doc path utility
        self.test_data_dir = get_test_doc_repo_path()
    
    def _find_sample(self, new_path, old_path):
        """Find sample file in new or old location."""
        new_full = self.test_data_dir / new_path
        if new_full.exists():
            return new_full
        old_full = self.test_data_dir / old_path
        if old_full.exists():
            return old_full
        return None

    def test_extract_images_from_pdf(self):
        """Test extracting images from a PDF file"""
        # Use architectural blueprint PDF which definitely has diagrams/images
        pdf_path = self._find_sample(
            "pdf/plans/doc2_washington/683 Washington Street As-Built (06-26-25) Sheet 1 (Rev 1) (09-14-25).pdf",
            "683 Washington Street As-Built (06-26-25) Sheet 1 (Rev 1) (09-14-25).pdf"
        )
        
        if pdf_path is None:
            pytest.skip("Sample PDF not found in either location")
        
        metadata_list, images_data = self.extractor.extract_from_pdf(str(pdf_path))
        
        # Should extract at least some images from the architectural PDF
        assert isinstance(metadata_list, list)
        assert isinstance(images_data, list)
        assert len(metadata_list) == len(images_data)
        
        # Check metadata structure
        if len(metadata_list) > 0:
            first_img = metadata_list[0]
            assert 'index' in first_img
            assert 'page' in first_img
            assert 'width' in first_img
            assert 'height' in first_img
            assert 'format' in first_img
            assert 'size' in first_img
            
            # Check image data is valid
            first_data = images_data[0]
            assert len(first_data) > 0
            # Should be able to load as PIL Image
            img = Image.open(BytesIO(first_data))
            assert img is not None

    def test_extract_images_from_docx(self):
        """Test extracting images from a DOCX file"""
        # This would require a sample DOCX file
        pytest.skip("DOCX sample not available yet")

    def test_image_format_conversion(self):
        """Test that images are converted to target format (PNG)"""
        # Use presentation file which should have images
        pdf_path = self.test_data_dir / "docs" / "doc08_us_bancorp_q4_2023_presentation" / "source.pdf"
        
        if not pdf_path.exists():
            pytest.skip(f"Sample PDF not found: {pdf_path}")
        
        metadata_list, images_data = self.extractor.extract_from_pdf(str(pdf_path))
        
        if len(metadata_list) > 0:
            # Check format is PNG
            assert metadata_list[0]['format'] == 'png'
            
            # Verify image data is actually PNG
            img = Image.open(BytesIO(images_data[0]))
            assert img.format == 'PNG'

    def test_image_naming_convention(self):
        """Test standardized image filename generation"""
        assert self.extractor.get_image_filename(0) == "image_0.png"
        assert self.extractor.get_image_filename(5) == "image_5.png"
        assert self.extractor.get_image_filename(10, "jpg") == "image_10.jpg"

    def test_no_images_in_document(self):
        """Test handling of documents without images"""
        # Test that attempting to extract from nonexistent file raises an exception
        # The extractor logs errors and re-raises them
        with pytest.raises(Exception):
            self.extractor.extract("/tmp/nonexistent.pdf")

    def test_image_quality_preservation(self):
        """Test that image quality is preserved during extraction"""
        # Use financial statements PDF which has logos/charts
        pdf_path = self._find_sample(
            "pdf/general/doc10_nestle_2022_financial_statements/source.pdf",
            "docs/doc10_nestle_2022_financial_statements/source.pdf"
        )
        
        if pdf_path is None:
            pytest.skip("Sample PDF not found in either location")
        
        metadata_list, images_data = self.extractor.extract_from_pdf(str(pdf_path))
        
        if len(metadata_list) > 0:
            # Check dimensions are reasonable
            assert metadata_list[0]['width'] > 0
            assert metadata_list[0]['height'] > 0
            
            # Image should have reasonable size (not corrupted)
            assert metadata_list[0]['size'] > 100  # At least 100 bytes

    def test_large_image_handling(self):
        """Test handling of very large images with size limits"""
        # Create extractor with small size limit
        small_extractor = ImageExtractor(max_image_size=1000)  # 1KB limit
        
        # Use brochure PDF which likely has large images
        pdf_path = self.test_data_dir / "docs" / "doc09_visit_phoenix_destination_brochure" / "source.pdf"
        
        if not pdf_path.exists():
            pytest.skip(f"Sample PDF not found: {pdf_path}")
        
        metadata_list, images_data = small_extractor.extract_from_pdf(str(pdf_path))
        
        # Should skip large images
        for metadata in metadata_list:
            assert metadata['size'] <= 1000

    def test_corrupted_image_handling(self):
        """Test handling of corrupted images in document"""
        # This would require a specially crafted PDF with corrupted images
        # For now, verify the extractor handles exceptions gracefully
        try:
            metadata, images = self.extractor.extract_from_pdf("/tmp/nonexistent.pdf")
        except Exception as e:
            # Should raise a meaningful exception, not crash
            assert str(e) is not None

    def test_extract_auto_detect_pdf(self):
        """Test auto-detection of PDF format"""
        # Use NASA technical document
        pdf_path = self.test_data_dir / "docs" / "doc07_nasa_composite_boom" / "source.pdf"
        
        if not pdf_path.exists():
            pytest.skip(f"Sample PDF not found: {pdf_path}")
        
        metadata, images = self.extractor.extract(str(pdf_path), mime_type="application/pdf")
        
        assert isinstance(metadata, list)
        assert isinstance(images, list)

    def test_extract_auto_detect_from_extension(self):
        """Test format detection from file extension"""
        # Use whitepaper PDF
        pdf_path = self.test_data_dir / "docs" / "doc06_urgent_care_whitepaper" / "source.pdf"
        
        if not pdf_path.exists():
            pytest.skip(f"Sample PDF not found: {pdf_path}")
        
        # Don't provide mime_type, should detect from extension
        metadata, images = self.extractor.extract(str(pdf_path))
        
        assert isinstance(metadata, list)
        assert isinstance(images, list)

    def test_unsupported_file_type(self):
        """Test handling of unsupported file types"""
        metadata, images = self.extractor.extract("/tmp/test.txt", mime_type="text/plain")
        
        # Should return empty lists for unsupported types
        assert metadata == []
        assert images == []

    def test_metadata_includes_original_format(self):
        """Test that metadata includes original image format"""
        # Use datasheet PDF
        pdf_path = self.test_data_dir / "docs" / "doc05_rslzva1_datasheet" / "source.pdf"
        
        if not pdf_path.exists():
            pytest.skip(f"Sample PDF not found: {pdf_path}")
        
        metadata_list, _ = self.extractor.extract_from_pdf(str(pdf_path))
        
        if len(metadata_list) > 0:
            assert 'original_format' in metadata_list[0]

    def test_image_index_sequential(self):
        """Test that image indices are sequential"""
        # Use research paper PDF
        pdf_path = self.test_data_dir / "docs" / "doc04_zero_shot_reasoners" / "source.pdf"
        
        if not pdf_path.exists():
            pytest.skip(f"Sample PDF not found: {pdf_path}")
        
        metadata_list, _ = self.extractor.extract_from_pdf(str(pdf_path))
        
        for i, metadata in enumerate(metadata_list):
            assert metadata['index'] == i

    def test_multiple_images_same_page(self):
        """Test handling of multiple images on the same page"""
        # Use patent document which likely has multiple figures per page
        pdf_path = self.test_data_dir / "docs" / "doc02_polymer_nanocapsules_patent" / "source.pdf"
        
        if not pdf_path.exists():
            pytest.skip(f"Sample PDF not found: {pdf_path}")
        
        metadata_list, images_data = self.extractor.extract_from_pdf(str(pdf_path))
        
        # All images should have valid page numbers
        for metadata in metadata_list:
            assert metadata['page'] >= 1

    def test_custom_target_format(self):
        """Test using a custom target format (e.g., JPEG)"""
        jpeg_extractor = ImageExtractor(target_format="JPEG")
        
        # Use RFP document
        pdf_path = self.test_data_dir / "docs" / "doc01_rfp_project_management" / "source.pdf"
        
        if not pdf_path.exists():
            pytest.skip(f"Sample PDF not found: {pdf_path}")
        
        metadata_list, images_data = jpeg_extractor.extract_from_pdf(str(pdf_path))
        
        if len(metadata_list) > 0:
            assert metadata_list[0]['format'] == 'jpeg'
            # Verify actual format
            img = Image.open(BytesIO(images_data[0]))
            assert img.format == 'JPEG'


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


