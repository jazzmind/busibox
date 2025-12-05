#!/bin/bash
# Run PDF Processing Test Suite
#
# This script runs the comprehensive PDF processing tests and generates a report.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
# Testdocs repo is cloned and symlinked as 'samples'
# Test docs are in pdf/general/ subdirectory
SAMPLES_DIR="$REPO_ROOT/samples/pdf/general"
# Fallback to old location for backwards compatibility
if [ ! -d "$SAMPLES_DIR" ]; then
    SAMPLES_DIR="$REPO_ROOT/samples/docs"
fi

echo "======================================================================"
echo "PDF PROCESSING TEST SUITE"
echo "======================================================================"
echo ""
echo "Repository: $REPO_ROOT"
echo "Samples:    $SAMPLES_DIR"
echo ""

# Check if samples exist
if [ ! -d "$SAMPLES_DIR" ]; then
    echo "ERROR: Samples directory not found: $SAMPLES_DIR"
    exit 1
fi

# Count downloaded PDFs
PDF_COUNT=$(find "$SAMPLES_DIR" -name "source.pdf" | wc -l | tr -d ' ')
echo "Found $PDF_COUNT downloaded PDFs"
echo ""

# Run tests
cd "$SCRIPT_DIR/.."

echo "======================================================================"
echo "Running Test Suite..."
echo "======================================================================"
echo ""

# Run with pytest
python -m pytest tests/test_pdf_processing_suite.py -v --tb=short "$@"

TEST_EXIT_CODE=$?

echo ""
echo "======================================================================"
echo "Test Suite Complete"
echo "======================================================================"
echo ""

if [ $TEST_EXIT_CODE -eq 0 ]; then
    echo "✅ All tests passed!"
else
    echo "❌ Some tests failed (exit code: $TEST_EXIT_CODE)"
fi

echo ""
echo "For detailed comparison of strategies, run:"
echo "  python tests/test_pdf_processing_suite.py"
echo ""

exit $TEST_EXIT_CODE

