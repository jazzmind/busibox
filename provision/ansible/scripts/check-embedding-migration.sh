#!/bin/bash
#
# Check if embedding model has changed and re-embedding is needed
#
# This script compares the currently configured embedding model/dimension
# against what's stored in Milvus collections.
#
# Usage: bash check-embedding-migration.sh [--check|--migrate|--force]
#   --check   : Only check if migration is needed (default)
#   --migrate : Perform migration if needed
#   --force   : Force migration even if not detected as needed
#
# Environment:
#   MILVUS_IP : Milvus server IP (default: 10.96.200.204)
#
# Exit codes:
#   0 - No migration needed (or migration completed successfully)
#   1 - Migration needed
#   2 - Error

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANSIBLE_DIR="$(dirname "$SCRIPT_DIR")"

# Find repo root (go up from provision/ansible/scripts)
REPO_ROOT="$(cd "$ANSIBLE_DIR/../.." && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Parse arguments
ACTION="check"
FORCE=false
for arg in "$@"; do
    case $arg in
        --check)
            ACTION="check"
            ;;
        --migrate)
            ACTION="migrate"
            ;;
        --force)
            FORCE=true
            ;;
        *)
            echo "Unknown argument: $arg"
            exit 2
            ;;
    esac
done

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Embedding Migration Check${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Get configured embedding model from model_registry.yml
CONFIG_FILE="$ANSIBLE_DIR/group_vars/all/model_registry.yml"
if [ ! -f "$CONFIG_FILE" ]; then
    echo -e "${RED}ERROR: Cannot find model_registry.yml at $CONFIG_FILE${NC}"
    exit 2
fi

# Extract embedding model key from model_purposes section
# The line looks like: "  embedding: "bge-large"   # comment"
# We use grep to find it and extract the value
EMBEDDING_LINE=$(grep -E "^[[:space:]]+embedding:" "$CONFIG_FILE" | head -1)

if [ -z "$EMBEDDING_LINE" ]; then
    echo -e "${RED}ERROR: Cannot find 'embedding:' line in model_registry.yml${NC}"
    exit 2
fi

# Extract the value - handle both "bge-large" (quoted) and bge-large (unquoted)
EMBEDDING_KEY=$(echo "$EMBEDDING_LINE" | sed 's/.*embedding:[[:space:]]*//' | sed 's/#.*//' | tr -d '"' | tr -d "'" | xargs)

if [ -z "$EMBEDDING_KEY" ]; then
    echo -e "${RED}ERROR: Cannot parse embedding model value from: $EMBEDDING_LINE${NC}"
    exit 2
fi

echo -e "Configured embedding model key: ${GREEN}$EMBEDDING_KEY${NC}"

# Get the model config for this key - look in available_models section
# The block looks like:
#   "bge-large":
#     provider: "fastembed"
#     model_name: "BAAI/bge-large-en-v1.5"
#     dimension: 1024

# Use grep/sed to find the model block and extract values
# First find the line number of the model definition
MODEL_LINE_NUM=$(grep -n "\"$EMBEDDING_KEY\":" "$CONFIG_FILE" | head -1 | cut -d: -f1)

if [ -n "$MODEL_LINE_NUM" ]; then
    # Extract the next 10 lines after the model definition and look for model_name and dimension
    CONFIGURED_MODEL=$(sed -n "${MODEL_LINE_NUM},\$p" "$CONFIG_FILE" | head -15 | grep "model_name:" | head -1 | sed 's/.*model_name:[[:space:]]*//' | tr -d '"' | tr -d "'" | xargs)
    CONFIGURED_DIM=$(sed -n "${MODEL_LINE_NUM},\$p" "$CONFIG_FILE" | head -15 | grep "dimension:" | head -1 | sed 's/.*dimension:[[:space:]]*//' | tr -d '"' | xargs)
fi

if [ -z "$CONFIGURED_MODEL" ]; then
    echo -e "${YELLOW}WARNING: Cannot find model_name for $EMBEDDING_KEY, using default${NC}"
    CONFIGURED_MODEL="BAAI/bge-large-en-v1.5"
fi

if [ -z "$CONFIGURED_DIM" ]; then
    echo -e "${YELLOW}WARNING: Cannot find dimension for $EMBEDDING_KEY, using default${NC}"
    CONFIGURED_DIM="1024"
fi

echo -e "Configured model: ${GREEN}$CONFIGURED_MODEL${NC}"
echo -e "Configured dimension: ${GREEN}$CONFIGURED_DIM${NC}"
echo ""

# Determine Milvus IP
if [ -n "$MILVUS_IP" ]; then
    MILVUS_HOST="$MILVUS_IP"
else
    # Default to production
    MILVUS_HOST="10.96.200.204"
fi

echo -e "Checking Milvus at: ${BLUE}$MILVUS_HOST${NC}"
echo ""

# Check if we can connect to Milvus
if ! ssh -o ConnectTimeout=5 -o BatchMode=yes root@$MILVUS_HOST "echo ok" &>/dev/null; then
    echo -e "${YELLOW}WARNING: Cannot SSH to Milvus host ($MILVUS_HOST)${NC}"
    echo "Make sure:"
    echo "  1. Milvus container is running"
    echo "  2. SSH access is configured"
    echo "  3. MILVUS_IP environment variable is set correctly"
    exit 2
fi

# Check Milvus collection schema
echo "Querying Milvus for current embedding dimension..."
MILVUS_DIM=$(ssh root@$MILVUS_HOST "/opt/milvus-tools/bin/python -c \"
from pymilvus import connections, Collection, utility
try:
    connections.connect('default', host='localhost', port=19530)
    if utility.has_collection('documents'):
        col = Collection('documents')
        for field in col.schema.fields:
            if field.name == 'text_dense':
                print(field.params.get('dim', 'unknown'))
                break
        else:
            print('no_text_dense_field')
    else:
        print('no_collection')
except Exception as e:
    print(f'error: {e}')
\"" 2>/dev/null || echo "ssh_error")

if [[ "$MILVUS_DIM" == ssh_error* ]] || [[ "$MILVUS_DIM" == error* ]]; then
    echo -e "${YELLOW}WARNING: Could not query Milvus${NC}"
    echo "Error: $MILVUS_DIM"
    echo ""
    echo "Make sure Milvus is running and the pymilvus tools are installed."
    exit 2
fi

if [ "$MILVUS_DIM" = "no_collection" ]; then
    echo -e "${GREEN}✓ No 'documents' collection exists yet - no migration needed${NC}"
    echo ""
    echo "The collection will be created with dimension $CONFIGURED_DIM when you deploy Milvus."
    exit 0
fi

if [ "$MILVUS_DIM" = "no_text_dense_field" ]; then
    echo -e "${YELLOW}WARNING: 'documents' collection exists but has no 'text_dense' field${NC}"
    echo "This may indicate a schema issue. Consider recreating the collection."
    exit 2
fi

echo -e "Current Milvus dimension: ${BLUE}$MILVUS_DIM${NC}"
echo ""

# Compare dimensions
if [ "$MILVUS_DIM" = "$CONFIGURED_DIM" ]; then
    echo -e "${GREEN}✓ Embedding dimensions match - no migration needed${NC}"
    echo ""
    echo "  Milvus:     $MILVUS_DIM-dimensional"
    echo "  Configured: $CONFIGURED_DIM-dimensional ($CONFIGURED_MODEL)"
    exit 0
else
    echo -e "${YELLOW}⚠ EMBEDDING DIMENSION MISMATCH DETECTED${NC}"
    echo ""
    echo -e "  Milvus collection:  ${RED}$MILVUS_DIM${NC}-dimensional"
    echo -e "  Configured model:   ${GREEN}$CONFIGURED_DIM${NC}-dimensional ($CONFIGURED_MODEL)"
    echo ""
    
    if [ "$ACTION" = "check" ]; then
        echo -e "${YELLOW}Migration is needed!${NC}"
        echo ""
        echo "To migrate, select option 8 'Migrate Embeddings (Milvus)' from the menu"
        echo "or run: make migrate-embeddings"
        echo ""
        echo "This will:"
        echo "  1. Drop the existing Milvus 'documents' collection"
        echo "  2. Recreate it with the new dimension ($CONFIGURED_DIM)"
        echo ""
        echo -e "${RED}WARNING: This will delete all existing embeddings!${NC}"
        echo "You will need to re-ingest all documents after migration."
        exit 1
    fi
    
    if [ "$ACTION" = "migrate" ]; then
        echo -e "${YELLOW}Starting migration...${NC}"
        echo ""
        
        if [ "$FORCE" = false ]; then
            read -p "Are you sure you want to drop and recreate the collection and re-embed all documents? [y/N] " -n 1 -r
            echo ""
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                echo "Migration cancelled."
                exit 0
            fi
        fi
        
        echo "Step 1: Dropping and recreating collection with new dimension..."
        echo ""
        
        # Set the EMBEDDING_DIMENSION env var for the script
        ssh root@$MILVUS_HOST "EMBEDDING_DIMENSION=$CONFIGURED_DIM /opt/milvus-tools/bin/python /root/hybrid_schema.py --drop" || {
            echo -e "${RED}ERROR: Failed to recreate collection${NC}"
            exit 2
        }
        
        echo ""
        echo -e "${GREEN}✓ Collection recreated with dimension $CONFIGURED_DIM${NC}"
        echo ""
        
        # Step 2: Trigger re-embedding via the ingest API
        echo -e "${YELLOW}Step 2: Triggering re-embedding of all documents...${NC}"
        echo ""
        
        # Determine ingest IP
        if [ -n "$INGEST_IP" ]; then
            INGEST_HOST="$INGEST_IP"
        else
            # Default based on Milvus IP network
            if [[ "$MILVUS_HOST" == 10.96.201.* ]]; then
                INGEST_HOST="10.96.201.206"  # Staging
            else
                INGEST_HOST="10.96.200.206"  # Production
            fi
        fi
        
        echo "Calling ingest API at: $INGEST_HOST"
        echo ""
        
        # Call the bulk reprocess endpoint
        # Note: This endpoint may require authentication in production
        REEMBED_RESULT=$(ssh root@$MILVUS_HOST "curl -s -X POST \
            -H 'Content-Type: application/json' \
            -d '{\"start_stage\": \"embedding\"}' \
            'http://$INGEST_HOST:8002/api/files/reprocess-all'" 2>/dev/null || echo '{"error": "curl_failed"}')
        
        # Check if the call succeeded
        if echo "$REEMBED_RESULT" | grep -q '"queued"'; then
            QUEUED_COUNT=$(echo "$REEMBED_RESULT" | grep -o '"queued":[0-9]*' | grep -o '[0-9]*')
            echo -e "${GREEN}✓ Queued $QUEUED_COUNT documents for re-embedding${NC}"
            echo ""
            echo "Re-embedding is now running in the background."
            echo "Monitor progress with:"
            echo "  ssh root@$INGEST_HOST 'journalctl -u ingest-worker -f'"
            echo ""
        elif echo "$REEMBED_RESULT" | grep -q '"count": 0\|"queued": 0'; then
            echo -e "${YELLOW}No documents found to re-embed${NC}"
            echo "This is normal for a fresh installation."
            echo ""
        elif echo "$REEMBED_RESULT" | grep -q "Unauthorized\|401\|403\|Missing"; then
            echo -e "${YELLOW}WARNING: Authentication required for re-embedding${NC}"
            echo ""
            echo "The collection has been recreated, but automatic re-embedding"
            echo "requires authentication. Please trigger manually:"
            echo ""
            echo "  Option 1: Use the Admin UI"
            echo "    Go to Documents > Re-index All"
            echo ""
            echo "  Option 2: Use curl with authentication"
            echo "    curl -X POST -H 'Authorization: Bearer <token>' \\"
            echo "      -H 'Content-Type: application/json' \\"
            echo "      -d '{\"start_stage\": \"embedding\"}' \\"
            echo "      'http://$INGEST_HOST:8002/api/files/reprocess-all'"
            echo ""
        else
            echo -e "${YELLOW}WARNING: Could not trigger automatic re-embedding${NC}"
            echo "Response: $REEMBED_RESULT"
            echo ""
            echo "Please trigger re-embedding manually:"
            echo "  curl -X POST -H 'Content-Type: application/json' \\"
            echo "    -d '{\"start_stage\": \"embedding\"}' \\"
            echo "    'http://$INGEST_HOST:8002/api/files/reprocess-all'"
            echo ""
        fi
        
        echo -e "${GREEN}Migration complete!${NC}"
        exit 0
    fi
fi
