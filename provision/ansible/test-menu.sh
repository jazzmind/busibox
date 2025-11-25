#!/bin/bash
# Interactive test menu for Busibox

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Prompt for environment if not set
if [[ -z "${INV:-}" ]]; then
    echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║     Busibox Test Runner Menu          ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
    echo ""
    echo "Select environment:"
    echo "  1) Test"
    echo "  2) Production"
    echo ""
    read -p "Choice [1-2]: " env_choice
    echo ""
    
    case "$env_choice" in
        1)
            INV="inventory/test"
            ;;
        2)
            INV="inventory/production"
            ;;
        *)
            echo -e "${YELLOW}Invalid choice, defaulting to test${NC}"
            INV="inventory/test"
            ;;
    esac
else
    echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║     Busibox Test Runner Menu          ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
    echo ""
fi

# Export INV so test-llm.sh can use it
export INV

echo -e "${CYAN}Environment: ${INV}${NC}"
echo ""

# Source LLM test functions
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/test-llm.sh"

# Main menu
echo "Select service to test:"
echo "  1) LLM Models (LiteLLM/vLLM)"
echo "  2) Ingest Service"
echo "  3) Search Service"
echo "  4) Agent Service"
echo "  5) Apps (AI Portal)"
echo "  6) All Services"
echo "  Q) Quit"
echo ""
read -p "Choice: " main_choice

case "$main_choice" in
    1)
        echo ""
        echo -e "${BLUE}LLM Model Tests${NC}"
        echo "Select test type:"
        echo "  1) List models by purpose"
        echo "  2) Test fast model (quick chat)"
        echo "  3) Test embedding"
        echo "  4) Test analysis (math/physics problem)"
        echo "  5) Test default model"
        echo "  6) Test chat model"
        echo "  7) Test research model"
        echo "  8) Test cleanup model"
        echo "  9) Test parsing model"
        echo " 10) Test classify model"
        echo " 11) Test vision model"
        echo " 12) Test AWS Bedrock (if configured)"
        echo " 13) Test OpenAI (if configured)"
        echo " 14) Back to main menu"
        echo ""
        read -p "Choice: " llm_choice
        
        case "$llm_choice" in
            1)
                list_models_by_purpose
                ;;
            2)
                test_fast
                ;;
            3)
                test_purpose_embedding
                ;;
            4)
                test_analysis
                ;;
            5)
                test_purpose "default"
                ;;
            6)
                test_purpose "chat"
                ;;
            7)
                test_purpose "research"
                ;;
            8)
                test_purpose "cleanup"
                ;;
            9)
                test_purpose "parsing"
                ;;
            10)
                test_purpose "classify"
                ;;
            11)
                test_purpose "vision"
                ;;
            12)
                test_bedrock
                ;;
            13)
                test_openai
                ;;
            14)
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
    3)
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
    4)
        echo -e "${GREEN}Running agent service tests...${NC}"
        make test-agent INV="$INV"
        ;;
    5)
        echo -e "${GREEN}Running apps (AI Portal) tests...${NC}"
        make test-apps INV="$INV"
        ;;
    6)
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

