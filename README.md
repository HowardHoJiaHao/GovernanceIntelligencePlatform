# Summary Program

Document management portal with role-based access, admin file editing, audit logs, and a local Gemma chatbot through Ollama.

## Run locally

1. Start Ollama and make sure Gemma is available:
	- `ollama serve`
	- `ollama pull gemma2:2b`
2. Run the app:
	- `uv run python main.py`
	- or `uv run python frontend/app.py`
3. Open `http://127.0.0.1:5001`

## Login roles

- Admin: full access to every category, file manager, user manager, and audit logs.
- Auditor: access to governance and important categories.
- User: access to procurement only.

## Features

- Dashboard cards with category counts and a live summary.
- Chat answers grounded in the allowed database documents.
- Admin can create, replace, and re-sync TXT documents.
- Every admin file change writes an audit log.
- New users can be created from the admin user page.

## Reset data

- Run `python3 scripts/setup_all.py` to reset data.