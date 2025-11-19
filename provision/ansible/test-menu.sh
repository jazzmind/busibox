#!/bin/bash
# Interactive test menu for Busibox

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get inventory from environment or default to test
INV="${INV:-inventory/test}"

echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     Busibox Test Runner Menu          ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
echo ""

# Main menu
echo "Select service to test:"
echo "  1) Ingest Service"
echo "  2) Search Service"
echo "  3) Agent Service"
echo "  4) Apps (AI Portal)"
echo "  5) All Services"
echo "  Q) Quit"
echo ""
read -p "Choice: " main_choice

case "$main_choice" in
    1)
        echo ""
        echo -e "${BLUE}Ingest Service Tests${NC}"
        echo "Select test type:"
        echo "  1) Simple extraction (no LLM cleanup)"
        echo "  2) Simple extraction (with LLM cleanup)"
        echo "  3) Marker extraction"
        echo "  4) ColPali visual extraction"
        echo "  5) Integration tests"
        echo "  6) All ingest tests"
        echo "  7) Back to main menu"
        echo ""
        read -p "Choice: " ingest_choice
        
        case "$ingest_choice" in
            1)
                echo -e "${GREEN}Running simple extraction tests...${NC}"
                make test-extraction-simple INV="$INV"
                ;;
            2)
                echo -e "${GREEN}Running simple extraction with LLM cleanup tests...${NC}"
                make test-extraction-llm INV="$INV"
                ;;
            3)
                echo -e "${GREEN}Running Marker extraction tests...${NC}"
                make test-extraction-marker INV="$INV"
                ;;
            4)
                echo -e "${GREEN}Running ColPali extraction tests...${NC}"
                make test-extraction-colpali INV="$INV"
                ;;
            5)
                echo -e "${GREEN}Running ingest integration tests...${NC}"
                make test-ingest-all INV="$INV"
                ;;
            6)
                echo -e "${GREEN}Running all ingest tests...${NC}"
                make test-extraction-simple INV="$INV" && \
                make test-extraction-llm INV="$INV" && \
                make test-extraction-marker INV="$INV" && \
                make test-extraction-colpali INV="$INV" && \
                make test-ingest-all INV="$INV"
                ;;
            7)
                exec "$0" "$@"
                ;;
            *)
                echo -e "${RED}Invalid choice${NC}"
                exit 1
                ;;
        esac
        ;;
    2)
        echo ""
        echo -e "${BLUE}Search Service Tests${NC}"
        echo "Select test type:"
        echo "  1) Unit tests"
        echo "  2) Integration tests"
        echo "  3) All search tests"
        echo "  4) Back to main menu"
        echo ""
        read -p "Choice: " search_choice
        
        case "$search_choice" in
            1)
                echo -e "${GREEN}Running search unit tests...${NC}"
                make test-search-unit INV="$INV"
                ;;
            2)
                echo -e "${GREEN}Running search integration tests...${NC}"
                make test-search-integration INV="$INV"
                ;;
            3)
                echo -e "${GREEN}Running all search tests...${NC}"
                make test-search INV="$INV"
                ;;
            4)
                exec "$0" "$@"
                ;;
            *)
                echo -e "${RED}Invalid choice${NC}"
                exit 1
                ;;
        esac
        ;;
    3)
        echo -e "${GREEN}Running agent service tests...${NC}"
        make test-agent INV="$INV"
        ;;
    4)
        echo -e "${GREEN}Running apps (AI Portal) tests...${NC}"
        make test-apps INV="$INV"
        ;;
    5)
        echo -e "${GREEN}Running all service tests...${NC}"
        make test-all INV="$INV"
        ;;
    Q|q)
        echo "Exiting..."
        exit 0
        ;;
    *)
        echo -e "${RED}Invalid choice${NC}"
        exit 1
        ;;
esac

