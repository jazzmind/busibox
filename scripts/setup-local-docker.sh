#!/bin/bash
# =============================================================================
# Busibox Local Docker Setup Script
# =============================================================================
#
# This script sets up the local Docker development environment.
#
# Usage:
#   ./scripts/setup-local-docker.sh [command]
#
# Commands:
#   setup     - Initial setup (create .env.local, build images)
#   start     - Start all services
#   stop      - Stop all services
#   restart   - Restart all services
#   logs      - View logs (follow mode)
#   status    - Show service status
#   clean     - Remove all containers and volumes (WARNING: deletes data)
#   help      - Show this help message
#
# =============================================================================

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/docker-compose.local.yml"
ENV_FILE="$PROJECT_ROOT/.env.local"
ENV_EXAMPLE="$PROJECT_ROOT/env.local.example"

# Print colored message
print_info() { echo -e "${BLUE}ℹ${NC} $1"; }
print_success() { echo -e "${GREEN}✓${NC} $1"; }
print_warning() { echo -e "${YELLOW}⚠${NC} $1"; }
print_error() { echo -e "${RED}✗${NC} $1"; }

# Check prerequisites
check_prerequisites() {
    print_info "Checking prerequisites..."
    
    # Check Docker
    if ! command -v docker &> /dev/null; then
        print_error "Docker is not installed. Please install Docker Desktop."
        exit 1
    fi
    
    # Check Docker Compose
    if ! docker compose version &> /dev/null; then
        print_error "Docker Compose is not available. Please update Docker Desktop."
        exit 1
    fi
    
    # Check Docker daemon
    if ! docker info &> /dev/null; then
        print_error "Docker daemon is not running. Please start Docker Desktop."
        exit 1
    fi
    
    print_success "Prerequisites check passed"
}

# Setup environment file
setup_env() {
    if [[ -f "$ENV_FILE" ]]; then
        print_info ".env.local already exists"
        read -p "Do you want to overwrite it? (y/N) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            print_info "Keeping existing .env.local"
            return
        fi
    fi
    
    if [[ -f "$ENV_EXAMPLE" ]]; then
        cp "$ENV_EXAMPLE" "$ENV_FILE"
        print_success "Created .env.local from env.local.example"
        print_warning "Please edit .env.local and add your API keys (OPENAI_API_KEY, etc.)"
    else
        print_error "env.local.example not found"
        exit 1
    fi
}

# Build all Docker images
build_images() {
    print_info "Building Docker images (this may take a while)..."
    cd "$PROJECT_ROOT"
    
    docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" build --parallel
    
    print_success "Docker images built successfully"
}

# Start services
start_services() {
    print_info "Starting services..."
    cd "$PROJECT_ROOT"
    
    # Load env file if it exists
    ENV_OPT=""
    if [[ -f "$ENV_FILE" ]]; then
        ENV_OPT="--env-file $ENV_FILE"
    fi
    
    docker compose -f "$COMPOSE_FILE" $ENV_OPT up -d
    
    print_success "Services started"
    print_info ""
    print_info "Service URLs:"
    echo "  - AI Portal:      http://localhost:3000"
    echo "  - Agent Manager:  http://localhost:3001"
    echo "  - Agent API:      http://localhost:8000/docs"
    echo "  - Ingest API:     http://localhost:8002/docs"
    echo "  - Search API:     http://localhost:8003/docs"
    echo "  - AuthZ API:      http://localhost:8010/docs"
    echo "  - LiteLLM:        http://localhost:4000/docs"
    echo "  - MinIO Console:  http://localhost:9001 (minioadmin/minioadmin)"
    echo "  - PostgreSQL:     localhost:5432"
    echo "  - Milvus:         localhost:19530"
    echo "  - Redis:          localhost:6379"
    print_info ""
    print_info "Use './scripts/setup-local-docker.sh logs' to view logs"
}

# Stop services
stop_services() {
    print_info "Stopping services..."
    cd "$PROJECT_ROOT"
    
    docker compose -f "$COMPOSE_FILE" down
    
    print_success "Services stopped"
}

# Restart services
restart_services() {
    stop_services
    start_services
}

# View logs
view_logs() {
    print_info "Viewing logs (Ctrl+C to exit)..."
    cd "$PROJECT_ROOT"
    
    docker compose -f "$COMPOSE_FILE" logs -f "${@:-}"
}

# Show status
show_status() {
    print_info "Service status:"
    cd "$PROJECT_ROOT"
    
    docker compose -f "$COMPOSE_FILE" ps
}

# Clean up everything
clean_all() {
    print_warning "This will remove all containers and volumes (all data will be lost)!"
    read -p "Are you sure? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_info "Cancelled"
        exit 0
    fi
    
    print_info "Cleaning up..."
    cd "$PROJECT_ROOT"
    
    docker compose -f "$COMPOSE_FILE" down -v --remove-orphans
    
    # Remove dangling images
    docker image prune -f
    
    print_success "Cleanup complete"
}

# Initial setup
do_setup() {
    check_prerequisites
    setup_env
    build_images
    
    print_success ""
    print_success "Setup complete!"
    print_info ""
    print_info "Next steps:"
    echo "  1. Edit .env.local and add your API keys"
    echo "  2. Run './scripts/setup-local-docker.sh start' to start services"
}

# Show help
show_help() {
    echo "Busibox Local Docker Setup"
    echo ""
    echo "Usage: $0 [command]"
    echo ""
    echo "Commands:"
    echo "  setup     Initial setup (create .env.local, build images)"
    echo "  start     Start all services"
    echo "  stop      Stop all services"
    echo "  restart   Restart all services"
    echo "  logs      View logs (follow mode)"
    echo "  status    Show service status"
    echo "  clean     Remove all containers and volumes"
    echo "  help      Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 setup              # Initial setup"
    echo "  $0 start              # Start all services"
    echo "  $0 logs ai-portal     # View logs for ai-portal only"
    echo "  $0 logs -f agent-api  # Follow logs for agent-api"
}

# Main
case "${1:-help}" in
    setup)
        do_setup
        ;;
    start)
        check_prerequisites
        start_services
        ;;
    stop)
        stop_services
        ;;
    restart)
        restart_services
        ;;
    logs)
        shift || true
        view_logs "$@"
        ;;
    status)
        show_status
        ;;
    clean)
        clean_all
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        print_error "Unknown command: $1"
        show_help
        exit 1
        ;;
esac
