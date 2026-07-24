# ============================================================
# Liz Coder Plus — Makefile
# Unified build, run, test, and lint commands
# ============================================================

.PHONY: help install dev run test lint format clean docker-build docker-up docker-down docker-logs

# Default target
help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ============================================================
# Install & Setup
# ============================================================

install: ## Install all package dependencies
	pip install -r apps/backend/requirements.txt
	pip install -e packages/core
	pip install -e packages/memory
	pip install -e packages/llm
	pip install -e packages/tools
	pip install -e packages/agents
	pip install -e packages/multiagent
	pip install -e packages/shared

install-dev: install ## Install dev dependencies
	pip install pytest pytest-asyncio ruff mypy

# ============================================================
# Development
# ============================================================

dev: ## Run backend in development mode (hot reload)
	cd apps/backend && uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --reload

run: ## Run backend in production mode
	cd apps/backend && uvicorn src.api.main:app --host 0.0.0.0 --port 8000

# ============================================================
# Testing
# ============================================================

test: ## Run all tests
	pytest tests/ -v --tb=short

test-unit: ## Run unit tests only
	pytest tests/unit/ -v --tb=short

test-integration: ## Run integration tests only
	pytest tests/integration/ -v --tb=short

test-fast: ## Run tests with minimal output
	pytest tests/ -q

# ============================================================
# Quality
# ============================================================

lint: ## Run ruff linter
	ruff check packages/ apps/backend/src/

format: ## Auto-format code with ruff
	ruff format packages/ apps/backend/src/

typecheck: ## Run mypy type checking
	mypy packages/core/src packages/llm/src packages/memory/src

# ============================================================
# Docker
# ============================================================

docker-build: ## Build Docker image
	docker build -t liz-coder-plus:latest .

docker-up: ## Start containers with docker-compose
	docker compose up -d

docker-down: ## Stop containers
	docker compose down

docker-logs: ## View container logs
	docker compose logs -f backend

docker-restart: ## Restart backend container
	docker compose restart backend

# ============================================================
# Cleanup
# ============================================================

clean: ## Remove generated files
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf data/*.db logs/*.log

# ============================================================
# Git
# ============================================================

push: ## Commit all changes and push (quick)
	git add -A && git commit -m "chore: quick push" && git push

status: ## Show git status
	git status
