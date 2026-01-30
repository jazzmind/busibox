#!/bin/bash
# Monitor Marker model download progress

echo "======================================================================"
echo "MARKER MODEL DOWNLOAD MONITOR"
echo "======================================================================"
echo ""

CACHE_DIR="$HOME/Library/Caches/datalab/models"

if [ ! -d "$CACHE_DIR" ]; then
    echo "❌ Cache directory not found: $CACHE_DIR"
    echo "Download may not have started yet."
    exit 1
fi

echo "📦 Cache location: $CACHE_DIR"
echo ""

echo "Current size:"
du -sh "$CACHE_DIR"
echo ""

echo "Downloaded files:"
find "$CACHE_DIR" -type f -name "*.safetensors" -o -name "*.bin" -o -name "model.*" | while read file; do
    size=$(du -h "$file" | cut -f1)
    name=$(basename "$file")
    echo "  - $name ($size)"
done

echo ""
echo "Total files downloaded:"
find "$CACHE_DIR" -type f | wc -l | tr -d ' '

echo ""
echo "Expected downloads:"
echo "  - Layout model (~1.4GB)"
echo "  - OCR models (~500MB)"
echo "  - Other models (~200MB)"
echo "  - Total: ~2GB"
echo ""

# Check if download is complete
LARGE_FILES=$(find "$CACHE_DIR" -type f -size +1G | wc -l | tr -d ' ')
if [ "$LARGE_FILES" -gt 0 ]; then
    echo "✅ Large models found - download likely complete!"
else
    echo "⏳ Still downloading... (no large model files found yet)"
fi

echo ""
echo "To monitor in real-time, run:"
echo "  watch -n 2 bash tests/check_marker_download.sh"

