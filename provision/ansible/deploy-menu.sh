#!/bin/bash
# Interactive deployment menu for Busibox
# Run with: make deploy

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Vault password file (optional)
VAULT_PASS_FILE=~/.vault_pass
if [ -f "$VAULT_PASS_FILE" ]; then
    VAULT_FLAGS="--vault-password-file $VAULT_PASS_FILE"
else
    VAULT_FLAGS="--ask-vault-pass"
fi

# vLLM mode for test environment
VLLM_MODE="alias"  # "alias" (default) or "deploy"

show_header() {
    echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║     Busibox Deployment Menu            ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
    echo ""
}

# ========================================================================
# Environment Selection
# ========================================================================

select_environment() {
    if [[ -z "${INV:-}" ]]; then
        show_header
        echo "Select environment:"
        echo "  1) Test"
        echo "  2) Production"
        echo ""
        read -p "Choice [1-2]: " env_choice
        echo ""
        
        case "$env_choice" in
            1)
                INV="inventory/test"
                ENV_NAME="TEST"
                select_vllm_mode
                ;;
            2)
                INV="inventory/production"
                ENV_NAME="PRODUCTION"
                VLLM_MODE="deploy"  # Production always deploys its own vLLM
                ;;
            *)
                echo -e "${YELLOW}Invalid choice, defaulting to test${NC}"
                INV="inventory/test"
                ENV_NAME="TEST"
                select_vllm_mode
                ;;
        esac
    else
        if [[ "$INV" == *"test"* ]]; then
            ENV_NAME="TEST"
        else
            ENV_NAME="PRODUCTION"
            VLLM_MODE="deploy"
        fi
    fi
    
    export INV
    export ENV_NAME
    export VLLM_MODE
}

select_vllm_mode() {
    echo ""
    echo -e "${CYAN}vLLM Configuration for Test Environment:${NC}"
    echo ""
    echo "  The test environment can either:"
    echo "  1) Alias to production vLLM (default, saves resources)"
    echo "  2) Deploy its own vLLM container (for isolated testing)"
    echo ""
    read -p "Choice [1-2, default=1]: " vllm_choice
    echo ""
    
    case "$vllm_choice" in
        2)
            VLLM_MODE="deploy"
            echo -e "${YELLOW}⚠ Test environment will deploy its own vLLM container${NC}"
            echo "  This requires GPU resources and significant memory"
            ;;
        *)
            VLLM_MODE="alias"
            echo -e "${GREEN}✓ Test environment will use production vLLM${NC}"
            ;;
    esac
}

# ========================================================================
# Deployment Functions
# ========================================================================

deploy_infrastructure() {
    echo -e "${CYAN}Deploying core infrastructure to $ENV_NAME...${NC}"
    echo ""
    
    echo -e "${BLUE}[1/4] PostgreSQL${NC}"
    make pg INV="$INV"
    
    echo ""
    echo -e "${BLUE}[2/4] MinIO (Files)${NC}"
    make files INV="$INV"
    
    echo ""
    echo -e "${BLUE}[3/4] Milvus (Vector DB)${NC}"
    make milvus INV="$INV"
    
    echo ""
    echo -e "${BLUE}[4/4] Search API${NC}"
    make search-api INV="$INV"
    
    echo ""
    echo -e "${GREEN}✓ Core infrastructure deployed${NC}"
}

deploy_llm_services() {
    echo -e "${CYAN}Deploying LLM services to $ENV_NAME...${NC}"
    echo ""
    
    if [ "$ENV_NAME" == "TEST" ] && [ "$VLLM_MODE" == "alias" ]; then
        echo -e "${YELLOW}Skipping vLLM deployment (aliased to production)${NC}"
        echo ""
        echo "Configuring LiteLLM to use production vLLM endpoints..."
        
        # Deploy LiteLLM with production vLLM configuration
        make litellm INV="$INV" EXTRA_ARGS="-e vllm_use_production=true"
    else
        echo -e "${BLUE}[1/4] vLLM (Main LLM)${NC}"
        make vllm INV="$INV"
        
        echo ""
        echo -e "${BLUE}[2/4] vLLM Embedding${NC}"
        make vllm-embedding INV="$INV"
        
        echo ""
        echo -e "${BLUE}[3/4] ColPali${NC}"
        make colpali INV="$INV"
        
        echo ""
        echo -e "${BLUE}[4/4] LiteLLM${NC}"
        make litellm INV="$INV"
    fi
    
    echo ""
    echo -e "${GREEN}✓ LLM services deployed${NC}"
}

deploy_application_services() {
    echo -e "${CYAN}Deploying application services to $ENV_NAME...${NC}"
    echo ""
    
    echo -e "${BLUE}[1/3] Ingest Service${NC}"
    make ingest INV="$INV"
    
    echo ""
    echo -e "${BLUE}[2/3] Agent Service${NC}"
    make agent INV="$INV"
    
    echo ""
    echo -e "${BLUE}[3/3] Apps (nginx + PM2)${NC}"
    make apps INV="$INV"
    
    echo ""
    echo -e "${GREEN}✓ Application services deployed${NC}"
}

deploy_applications() {
    echo -e "${CYAN}Deploying applications to $ENV_NAME...${NC}"
    echo ""
    
    echo "Select application to deploy:"
    echo "  1) All applications"
    echo "  2) AI Portal"
    echo "  3) Agent Client"
    echo "  4) Doc Intel"
    echo "  5) Foundation"
    echo "  6) Project Analysis"
    echo "  7) Innovation"
    echo "  8) Back to main menu"
    echo ""
    read -p "Choice: " app_choice
    
    case "$app_choice" in
        1)
            echo -e "${GREEN}Deploying all applications...${NC}"
            make deploy-apps INV="$INV"
            ;;
        2)
            echo -e "${GREEN}Deploying AI Portal...${NC}"
            make deploy-ai-portal INV="$INV"
            ;;
        3)
            echo -e "${GREEN}Deploying Agent Client...${NC}"
            make deploy-agent-client INV="$INV"
            ;;
        4)
            echo -e "${GREEN}Deploying Doc Intel...${NC}"
            make deploy-doc-intel INV="$INV"
            ;;
        5)
            echo -e "${GREEN}Deploying Foundation...${NC}"
            make deploy-foundation INV="$INV"
            ;;
        6)
            echo -e "${GREEN}Deploying Project Analysis...${NC}"
            make deploy-project-analysis INV="$INV"
            ;;
        7)
            echo -e "${GREEN}Deploying Innovation...${NC}"
            make deploy-innovation INV="$INV"
            ;;
        8)
            return
            ;;
        *)
            echo -e "${RED}Invalid choice${NC}"
            ;;
    esac
    
    echo ""
    echo -e "${GREEN}✓ Application deployment complete${NC}"
}

deploy_nginx_proxy() {
    echo -e "${CYAN}Deploying nginx proxy to $ENV_NAME...${NC}"
    echo ""
    
    make nginx INV="$INV"
    
    echo ""
    echo -e "${GREEN}✓ Nginx proxy deployed${NC}"
}

deploy_all() {
    echo ""
    echo -e "${MAGENTA}═══════════════════════════════════════════${NC}"
    echo -e "${MAGENTA}  Full Deployment to $ENV_NAME${NC}"
    if [ "$ENV_NAME" == "TEST" ]; then
        echo -e "${MAGENTA}  vLLM Mode: $VLLM_MODE${NC}"
    fi
    echo -e "${MAGENTA}═══════════════════════════════════════════${NC}"
    echo ""
    
    echo -e "${YELLOW}This will deploy ALL services to $ENV_NAME.${NC}"
    echo ""
    read -p "Continue? [y/N]: " confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        echo "Aborted."
        return
    fi
    
    echo ""
    echo -e "${BLUE}Phase 1: Core Infrastructure${NC}"
    echo "────────────────────────────────────────────"
    deploy_infrastructure
    
    echo ""
    echo -e "${BLUE}Phase 2: LLM Services${NC}"
    echo "────────────────────────────────────────────"
    deploy_llm_services
    
    echo ""
    echo -e "${BLUE}Phase 3: Application Services${NC}"
    echo "────────────────────────────────────────────"
    deploy_application_services
    
    echo ""
    echo -e "${BLUE}Phase 4: Applications${NC}"
    echo "────────────────────────────────────────────"
    make deploy-apps INV="$INV"
    
    echo ""
    echo -e "${BLUE}Phase 5: Nginx Proxy${NC}"
    echo "────────────────────────────────────────────"
    deploy_nginx_proxy
    
    echo ""
    echo -e "${MAGENTA}═══════════════════════════════════════════${NC}"
    echo -e "${GREEN}✓ Full deployment to $ENV_NAME complete!${NC}"
    echo -e "${MAGENTA}═══════════════════════════════════════════${NC}"
}

deploy_single_service() {
    echo ""
    echo "Select service to deploy:"
    echo ""
    echo -e "${CYAN}Infrastructure:${NC}"
    echo "   1) PostgreSQL"
    echo "   2) MinIO (Files)"
    echo "   3) Milvus (Vector DB)"
    echo "   4) Redis"
    echo ""
    echo -e "${CYAN}LLM Services:${NC}"
    echo "   5) vLLM (Main LLM)"
    echo "   6) vLLM Embedding"
    echo "   7) ColPali"
    echo "   8) LiteLLM"
    echo ""
    echo -e "${CYAN}Application Services:${NC}"
    echo "   9) Ingest API"
    echo "  10) Ingest Worker"
    echo "  11) Search API"
    echo "  12) Agent Service"
    echo "  13) Apps Container"
    echo ""
    echo -e "${CYAN}Proxy:${NC}"
    echo "  14) Nginx"
    echo ""
    echo "  15) Back to main menu"
    echo ""
    read -p "Choice: " service_choice
    
    case "$service_choice" in
        1)  make pg INV="$INV" ;;
        2)  make files INV="$INV" ;;
        3)  make milvus INV="$INV" ;;
        4)  echo "Redis is deployed as part of ingest service" ;;
        5)
            if [ "$ENV_NAME" == "TEST" ] && [ "$VLLM_MODE" == "alias" ]; then
                echo -e "${YELLOW}vLLM is aliased to production in test mode${NC}"
                echo "To deploy a test vLLM, restart with 'make deploy' and select option 2"
            else
                make vllm INV="$INV"
            fi
            ;;
        6)
            if [ "$ENV_NAME" == "TEST" ] && [ "$VLLM_MODE" == "alias" ]; then
                echo -e "${YELLOW}vLLM embedding is aliased to production in test mode${NC}"
            else
                make vllm-embedding INV="$INV"
            fi
            ;;
        7)
            if [ "$ENV_NAME" == "TEST" ] && [ "$VLLM_MODE" == "alias" ]; then
                echo -e "${YELLOW}ColPali is aliased to production in test mode${NC}"
            else
                make colpali INV="$INV"
            fi
            ;;
        8)  make litellm INV="$INV" ;;
        9)  make ingest-api INV="$INV" ;;
        10) make ingest-worker INV="$INV" ;;
        11) make search-api INV="$INV" ;;
        12) make agent INV="$INV" ;;
        13) make apps INV="$INV" ;;
        14) make nginx INV="$INV" ;;
        15) return ;;
        *)
            echo -e "${RED}Invalid choice${NC}"
            ;;
    esac
}

run_database_migrations() {
    echo -e "${CYAN}Running database migrations on $ENV_NAME...${NC}"
    echo ""
    
    # Get PostgreSQL IP
    local pg_ip=$(ansible -i "$INV" localhost -m debug -a "var=pg_ip" 2>/dev/null | grep "pg_ip" | awk -F'"' '{print $2}')
    
    echo "Checking migration status..."
    ssh root@$pg_ip 'cd /srv/migrations && ./migrate.sh status' || true
    
    echo ""
    echo "Run pending migrations?"
    echo "  1) Run all pending migrations"
    echo "  2) Show migration files"
    echo "  3) Cancel"
    echo ""
    read -p "Choice: " mig_choice
    
    case "$mig_choice" in
        1)
            echo -e "${GREEN}Running migrations...${NC}"
            ssh root@$pg_ip 'cd /srv/migrations && ./migrate.sh up'
            echo -e "${GREEN}✓ Migrations complete${NC}"
            ;;
        2)
            ssh root@$pg_ip 'ls -la /srv/migrations/sql/'
            ;;
        3)
            return
            ;;
    esac
}

verify_deployment() {
    echo -e "${CYAN}Verifying deployment on $ENV_NAME...${NC}"
    echo ""
    
    make verify INV="$INV"
}

# ========================================================================
# Main Menu
# ========================================================================

show_deploy_menu() {
    echo ""
    echo -e "${CYAN}Environment: $ENV_NAME ($INV)${NC}"
    if [ "$ENV_NAME" == "TEST" ]; then
        if [ "$VLLM_MODE" == "alias" ]; then
            echo -e "${CYAN}vLLM Mode: ${GREEN}Aliased to Production${NC}"
        else
            echo -e "${CYAN}vLLM Mode: ${YELLOW}Deploy Test Container${NC}"
        fi
    fi
    echo ""
    echo "Deployment Options:"
    echo "  1) Deploy ALL (full deployment)"
    echo "  2) Deploy single service"
    echo "  3) Deploy applications only"
    echo ""
    echo "  4) Deploy infrastructure (PG, MinIO, Milvus)"
    echo "  5) Deploy LLM services (vLLM, LiteLLM)"
    echo "  6) Deploy application services (Ingest, Agent, Apps)"
    echo ""
    echo "  7) Run database migrations"
    echo "  8) Verify deployment"
    echo ""
    echo "  9) Change environment"
    if [ "$ENV_NAME" == "TEST" ]; then
        echo " 10) Change vLLM mode"
    fi
    echo "  Q) Quit"
    echo ""
    read -p "Choice: " deploy_choice
    
    case "$deploy_choice" in
        1)
            deploy_all
            ;;
        2)
            deploy_single_service
            ;;
        3)
            deploy_applications
            ;;
        4)
            deploy_infrastructure
            ;;
        5)
            deploy_llm_services
            ;;
        6)
            deploy_application_services
            ;;
        7)
            run_database_migrations
            ;;
        8)
            verify_deployment
            ;;
        9)
            unset INV
            unset ENV_NAME
            select_environment
            ;;
        10)
            if [ "$ENV_NAME" == "TEST" ]; then
                select_vllm_mode
            else
                echo -e "${RED}Invalid choice${NC}"
            fi
            ;;
        Q|q)
            echo "Exiting..."
            exit 0
            ;;
        *)
            echo -e "${RED}Invalid choice${NC}"
            ;;
    esac
    
    echo ""
    read -p "Press Enter to continue..."
    show_deploy_menu
}

# ========================================================================
# Main
# ========================================================================

select_environment
show_deploy_menu

