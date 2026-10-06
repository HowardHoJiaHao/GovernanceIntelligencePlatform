# EGIP - common commands. Run `make` (or `make help`) to list them.
# Recipes avoid shell-specific syntax so they work with GNU make on Windows (cmd)
# as well as on macOS/Linux (sh).

APP_URL := http://localhost:5000
API_URL := http://localhost:5001

.DEFAULT_GOAL := help
.PHONY: help up build down restart logs ps install run env models health reindex open check

define HELP
EGIP - Enterprise Governance Intelligence Platform

Docker (recommended)
  make up        Start the containers (builds images only if they are missing)
  make build     Rebuild the images and restart (after changing frontend code or requirements)
  make down      Stop and remove the containers
  make restart   Restart the backend after editing backend code
  make logs      Follow the container logs (Ctrl+C to stop)
  make ps        Show container status

Local run (without Docker)
  make install   Install the Python dependencies with uv
  make run       Start backend and frontend with uv (run 'make down' first)

Setup and tools
  make env       Create .env from .env.example if it does not exist
  make models    Pull the Ollama models (gemma2:2b and nomic-embed-text)
  make health    Check that the backend is responding
  make reindex   Re-embed all documents into the vector database
  make open      Open the web app in your browser
  make check     Compile-check the Python code and validate docker-compose.yml

endef

help:
	$(info $(HELP))
	@echo App: $(APP_URL)   API: $(API_URL)

# ---------- Docker ----------
# No --build here: rebuilding on every start re-runs pip install whenever Docker
# has evicted its build cache. Backend code is mounted, so it never needs a rebuild.
up: .env
	docker compose up -d
	@echo EGIP is starting. Open $(APP_URL) and log in with an account from .env

build: .env
	docker compose up -d --build
	@echo EGIP was rebuilt. Open $(APP_URL) and log in with an account from .env

down:
	docker compose down

restart:
	docker compose restart backend

logs:
	docker compose logs -f

ps:
	docker compose ps

# ---------- Local run ----------
install:
	uv sync

run: .env
	uv run main.py

# ---------- Setup and tools ----------
env: .env

# Only created when missing, so an existing .env is never overwritten.
# The SECRET_KEY placeholder is replaced with a random key.
.env:
	uv run --no-project python -c "import secrets; t = open('.env.example').read(); open('.env', 'w', newline='').write(t.replace('SECRET_KEY=change-me-to-a-long-random-string', 'SECRET_KEY=' + secrets.token_hex(32)))"
	@echo Created .env with a random SECRET_KEY. Change the passwords before sharing the app.

models:
	ollama pull gemma2:2b
	ollama pull nomic-embed-text

health:
	curl -s $(API_URL)/api/health

reindex:
	curl -s -X POST $(API_URL)/api/reindex

open:
	uv run --no-project python -m webbrowser $(APP_URL)

check: .env
	uv run python -m py_compile backend/api.py backend/database_logic.py frontend/app.py main.py
	docker compose config --quiet
	@echo Python files compile and docker-compose.yml is valid.
