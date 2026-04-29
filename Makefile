BUNDLE_NAME = synapse-astro-editor
VERSION ?= 0.1.0

.PHONY: help install dev build-ui format lint typecheck check run run-http bundle clean clean-bundle bump

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install Python deps + UI deps
	uv sync
	cd ui && npm install

dev: ## Start UI dev server (Vite + MCP server via synapse plugin)
	cd ui && npm run dev

build-ui: ## Build UI for production (single-file index.html)
	cd ui && npm install && npm run build

format: ## Format Python code with ruff
	uv run ruff format src/

lint: ## Lint Python code with ruff
	uv run ruff check src/

typecheck: ## Type check Python with ty
	uv run ty check src/

check: lint typecheck ## Run all static checks

run: ## Run MCP server in stdio mode
	uv run python -m mcp_astro_editor.server

bundle: build-ui clean-bundle ## Build MCPB bundle (includes ui/dist + deps/)
	@uv pip install --target ./deps --only-binary :all: . 2>/dev/null || uv pip install --target ./deps .
	mcpb validate manifest.json
	mcpb pack . nimblebraininc-astro-editor-$(VERSION)-$$(uname -s | tr '[:upper:]' '[:lower:]')-$$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/').mcpb

clean-bundle: ## Remove build artifacts
	rm -rf deps/ *.mcpb

clean: clean-bundle ## Clean everything (caches, build outputs, ui/dist)
	rm -rf ui/dist/ ui/node_modules/
	find . -type d -name "__pycache__" -not -path "./ui/*" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ty_cache" -exec rm -rf {} + 2>/dev/null || true

bump: ## Bump version (usage: make bump VERSION=0.2.0)
	@if [ -z "$(VERSION)" ] || [ "$(VERSION)" = "0.1.0" ]; then \
		echo "Usage: make bump VERSION=X.Y.Z"; exit 1; \
	fi
	@sed -i.bak 's/"version": "[^"]*"/"version": "$(VERSION)"/' manifest.json && rm manifest.json.bak
	@sed -i.bak 's/"version": "[^"]*"/"version": "$(VERSION)"/' server.json && rm server.json.bak
	@sed -i.bak 's/^version = "[^"]*"/version = "$(VERSION)"/' pyproject.toml && rm pyproject.toml.bak
	@sed -i.bak 's/^__version__ = "[^"]*"/__version__ = "$(VERSION)"/' src/mcp_astro_editor/__init__.py && rm src/mcp_astro_editor/__init__.py.bak
	@echo "Bumped to $(VERSION). Review with 'git diff', then commit and tag."
