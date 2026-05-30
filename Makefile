# mcp-bookmarks — canonical developer entry points.
#
# Goals:
#   - `make install` brings up a working dev env from a clean clone.
#   - The four CI gates (lint, format-check, typecheck, test) all have
#     local-equivalent targets that run in seconds.
#   - Quick smoke target hits the running server.
#
# All targets are idempotent. `make help` lists them.

.DEFAULT_GOAL := help

# Pin the Python the project supports for everything here so behavior matches CI.
PYTHON ?= 3.12
UV     ?= uv

.PHONY: help
help:  ## Show this help.
	@awk 'BEGIN {FS = ":.*##"; print "Targets:"} /^[a-zA-Z_-]+:.*##/ { printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

.PHONY: install
install:  ## Sync dev deps + install ruff & mypy as uv tools.
	$(UV) sync --extra dev --python $(PYTHON)
	$(UV) tool install ruff
	$(UV) tool install mypy

.PHONY: test
test:  ## Run the default unit + integration suite (no live).
	$(UV) run --python $(PYTHON) python -m pytest tests/unit tests/integration -q

.PHONY: test-live
test-live:  ## Run the opt-in live suite (network / AWS).
	$(UV) run --python $(PYTHON) python -m pytest tests/live -q -m live

.PHONY: coverage
coverage:  ## Run the test suite with HTML coverage report (open htmlcov/index.html).
	$(UV) run --python $(PYTHON) python -m pytest tests/unit tests/integration \
		--cov=src/mcp_bookmarks --cov-report=term-missing --cov-report=html

.PHONY: lint
lint:  ## Lint without modifying files (Ruff).
	ruff check .

.PHONY: format
format:  ## Apply Ruff formatting + safe lint fixes.
	ruff format .
	ruff check --fix .

.PHONY: format-check
format-check:  ## Verify formatting is clean without writing (CI gate).
	ruff format --check .

.PHONY: typecheck
typecheck:  ## Type-check src/ with mypy (pragmatic config in pyproject).
	$(UV) run --python $(PYTHON) mypy src/mcp_bookmarks

.PHONY: ci
ci: lint format-check typecheck test  ## Run every CI gate locally in order.

.PHONY: dev
dev:  ## Start the server for local development (Ctrl-C to stop).
	$(UV) run --python $(PYTHON) mcp-bookmarks

.PHONY: smoke
smoke:  ## Verify a running local server (assumes :8000).
	@curl -fsS http://localhost:8000/health | head -1
	@curl -fsS http://localhost:8000/ready  | head -1

.PHONY: clean
clean:  ## Remove caches.
	rm -rf .pytest_cache .ruff_cache .mypy_cache build dist *.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

.PHONY: pre-commit-install
pre-commit-install:  ## Wire .git/hooks/pre-commit to .pre-commit-config.yaml.
	$(UV) tool install pre-commit
	pre-commit install
