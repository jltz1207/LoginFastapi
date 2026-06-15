# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AsistAi is a FastAPI backend providing user authentication and AI-powered document management. It uses PostgreSQL for user data and ChromaDB for semantic vector search powered by OpenAI embeddings.

## Commands

### Setup
```powershell
uv sync           # Install dependencies
```

### Run Development Server
```powershell
uv run uvicorn app.main:app --reload
# Server at http://127.0.0.1:8000
# API docs at http://127.0.0.1:8000/docs
```

### Database Migrations (Alembic)
```powershell
uv run alembic revision --autogenerate -m "description"
uv run alembic upgrade head
```

## Environment Variables

The `.env` file is currently empty — the app falls back to defaults in `app/core/config.py`. For a working setup:

```
DATABASE_URL=postgresql://postgres:P%40ssw0rd@localhost:5432/AsistAi
SECRET_KEY=a-string-secret-at-least-256-bits-long
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
CHROMA_PERSIST_DIRECTORY=./chroma_data
CHROMA_COLLECTION_NAME=users_knowledge
CHROMA_OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_API_KEY=your_openai_api_key
```

`OPENAI_API_KEY` is optional — ChromaDB falls back to no embeddings if absent.

## Architecture

### Directory Layout

- `app/api/v1/endpoints/` — Route handlers (`users.py`, `chroma.py`)
- `app/schemas/` — SQLAlchemy ORM models (database tables)
- `app/models/` — Pydantic request/response DTOs
- `app/services/` — Business logic (`jwtService.py`, `chroma_service.py`)
- `app/core/config.py` — Pydantic `Settings` class, reads from `.env`
- `app/db/session.py` — SQLAlchemy engine, `SessionLocal`, `Base`

### Request Lifecycle

FastAPI routes use two key `Depends()` injections:
- `get_db` (from `app/db/session.py`) — yields a SQLAlchemy session per request
- `getCurrentUser` (from `app/services/jwtService.py`) — validates JWT Bearer token and returns the User ORM object

### Authentication

JWT HS256 tokens with 30-minute expiration. Registration and login (`POST /api/v1/users/register`, `POST /api/v1/users/login`) are the only unauthenticated endpoints. Login accepts email **or** username.

### ChromaDB

`ChromaService` in `app/services/chroma_service.py` uses `@lru_cache` for a singleton persistent client. The default collection is `users_knowledge`. Upsert and query operations both require auth. OpenAI embeddings are configured at client initialization time — if `OPENAI_API_KEY` is missing, embeddings default to `None`.

### Naming Convention

- `app/schemas/` holds SQLAlchemy models (ORM/table definitions)
- `app/models/` holds Pydantic models (validation/serialization)

This is the reverse of common conventions — keep it consistent when adding new resources.
