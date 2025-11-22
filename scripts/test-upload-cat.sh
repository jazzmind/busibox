#!/bin/bash
set -euo pipefail

# Test script to upload cat.jpg to the ingest service

INGEST_URL="http://10.96.200.206:8002"
USER_ID="00000000-0000-0000-0000-000000000001"  # Valid UUID format
FILE_PATH="samples/cat.jpg"

echo "=== Uploading cat.jpg to ingest service ==="
echo "File: $FILE_PATH"
echo "Ingest URL: $INGEST_URL"
echo "User ID: $USER_ID"
echo ""

# Upload the file
echo "1. Uploading file..."
UPLOAD_RESPONSE=$(curl -s -X POST \
  -H "X-User-Id: $USER_ID" \
  -F "file=@$FILE_PATH" \
  "$INGEST_URL/upload")

echo "Upload response:"
echo "$UPLOAD_RESPONSE" | jq '.'
echo ""

# Extract file ID
FILE_ID=$(echo "$UPLOAD_RESPONSE" | jq -r '.fileId')
echo "File ID: $FILE_ID"
echo ""

# Request presigned URL
echo "2. Requesting presigned URL..."
PRESIGNED_RESPONSE=$(curl -s -X GET \
  -H "X-User-Id: $USER_ID" \
  "$INGEST_URL/files/$FILE_ID/presigned-url?expiry=3600")

echo "Presigned URL response:"
echo "$PRESIGNED_RESPONSE" | jq '.'
echo ""

# Extract presigned URL
PRESIGNED_URL=$(echo "$PRESIGNED_RESPONSE" | jq -r '.url')
echo "=== Presigned URL ==="
echo "$PRESIGNED_URL"
echo ""

# Test accessing the presigned URL
echo "3. Testing presigned URL access..."
HTTP_CODE=$(curl -s -o /tmp/cat-download.jpg -w "%{http_code}" "$PRESIGNED_URL")
echo "HTTP Status: $HTTP_CODE"

if [ "$HTTP_CODE" = "200" ]; then
  echo "✓ Successfully downloaded image via presigned URL"
  FILE_SIZE=$(stat -f%z /tmp/cat-download.jpg 2>/dev/null || stat -c%s /tmp/cat-download.jpg 2>/dev/null)
  echo "Downloaded file size: $FILE_SIZE bytes"
  echo "Downloaded to: /tmp/cat-download.jpg"
  
  # Compare file sizes
  ORIGINAL_SIZE=$(stat -f%z "$FILE_PATH" 2>/dev/null || stat -c%s "$FILE_PATH" 2>/dev/null)
  echo "Original file size: $ORIGINAL_SIZE bytes"
  
  if [ "$FILE_SIZE" = "$ORIGINAL_SIZE" ]; then
    echo "✓ File sizes match!"
  else
    echo "✗ File sizes don't match"
  fi
else
  echo "✗ Failed to download image"
  echo "Response:"
  cat /tmp/cat-download.jpg
fi

echo ""
echo "=== Summary ==="
echo "File ID: $FILE_ID"
echo "Presigned URL: $PRESIGNED_URL"
