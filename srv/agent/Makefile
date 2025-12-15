.PHONY: help install install-dev test test-unit test-integration test-cov lint format clean run

help:
	@echo "Agent Server - Development Commands"
	@echo ""
	@echo "Setup:"
	@echo "  make install       Install production dependencies"
	@echo "  make install-dev   Install dev/test dependencies"
	@echo ""
	@echo "Testing:"
	@echo "  make test          Run all tests"
	@echo "  make test-unit     Run unit tests only"
	@echo "  make test-integration  Run integration tests only"
	@echo "  make test-cov      Run tests with coverage report"
	@echo ""
	@echo "Code Quality:"
	@echo "  make lint          Run linter (ruff)"
	@echo "  make format        Format code (ruff)"
	@echo ""
	@echo "Development:"
	@echo "  make run           Run development server"
	@echo "  make clean         Clean cache and build files"

install:
	pip install -r requirements.txt

install-dev:
	pip install -r requirements.test.txt

test:
	pytest tests/ -v

test-unit:
	pytest tests/unit/ -v

test-integration:
	pytest tests/integration/ -v

test-cov:
	pytest tests/ -v --cov=app --cov-report=html --cov-report=term

lint:
	ruff check app/ tests/

format:
	ruff format app/ tests/

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf htmlcov/ .coverage

run:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000





