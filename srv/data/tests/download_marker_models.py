"""
Pre-download Marker models to cache them for testing.
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

print("="*80)
print("MARKER MODEL DOWNLOAD")
print("="*80)
print()

try:
    print("Importing Marker converter...")
    from marker.converters.pdf import PdfConverter
    
    print("Initializing PdfConverter (this will download models)...")
    print("This may take 5-10 minutes on first run...")
    print()
    
    converter = PdfConverter()
    
    print()
    print("="*80)
    print("✅ SUCCESS: All Marker models downloaded and cached!")
    print("="*80)
    print()
    print("Models are cached at: ~/Library/Caches/datalab/models/")
    print("Future runs will be much faster.")
    print()
    
except Exception as e:
    print()
    print("="*80)
    print("❌ ERROR downloading Marker models:")
    print("="*80)
    print(str(e))
    print()
    import traceback
    traceback.print_exc()
    sys.exit(1)

