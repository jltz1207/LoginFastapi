# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Setup
```powershell
uv sync
```

### Run Development Server
```powershell
uv run uvicorn app.main:app --reload
# Server: http://127.0.0.1:8000
# Swagger: http://127.0.0.1:8000/docs
```

### Database Migrations (Alembic)
```powershell
uv run alembic revision --autogenerate -m "description"
uv run alembic upgrade head
```

## Environment Variables

Active env file is `.env.dev` (local) or `.env.prod` (set `APP_ENV=prod`). Loaded at startup via `load_dotenv` in `app/main.py` before any LangChain imports.

Key variables (all in `.env.dev`):
```
DATABASE_URL=postgresql+psycopg://...
GEMINI_API_KEY=...
LLM_PROVIDER_TYPE=GEMINI          # GEMINI | OPENAI
LLM_MODEL_NAME=gemini-3.5-flash
LLM_API_KEY=...
EMBEDDING_PROVIDE_TYPE=GEMINI
EMBEDDING_MODEL=gemini-embedding-001
COLLECTION_NAME=user_knowledge_base
PARTITION_METHOD=2                 # 1=global collection, 2=one-per-user
GRAPH_STRATEGY=2                   # selects which LangGraph to compile
TAVILY_API_KEY=...
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=...
LANGCHAIN_PROJECT=AsistAi
```

`PARTITION_METHOD` and `GRAPH_STRATEGY` are integer enum selectors — see `app/vectorstore/collection_manager.py` and `app/agent/graphs/base.py`.

## Architecture

### Naming Convention (non-standard — keep consistent)
- `app/models/` — SQLAlchemy ORM table definitions (not Pydantic)
- `app/schemas/` — Pydantic request/response DTOs (not SQLAlchemy)

### Request Lifecycle

All routes require two `Depends()` injections:
- `get_db` (`app/db/session.py`) — yields an `AsyncSession` per request
- `getCurrentUser` (`app/services/jwtService.py`) — validates JWT Bearer and returns the `User` ORM object

Auth endpoints (`POST /api/v1/users/register`, `POST /api/v1/users/login`) are the only unauthenticated routes.

### LangGraph Agent (`app/agent/`)

The agent is a compiled `StateGraph` built at request time via `get_compiled_graph()` in `app/agent/dependencies.py`. The graph variant is selected by `GRAPH_STRATEGY`.

**Currently implemented — `SearchingRagGraphFactory` (strategy 2):**
```
START → retrieve_docs → grade_docs
                              ↓ grade > 5 → generate → END
                              ↓ grade ≤ 5 → rewrite_question → web_searcher → grade_docs (loop)
```

- `AgentState` (Pydantic `BaseModel`) carries `user_id`, `knowledge_base_id`, `question`, `chat_messages`, `documents`, `grade`, and `loop_count` through the graph.
- The checkpointer is `AsyncPostgresSaver` (Postgres-backed), initialized at app startup in `checkpointer_lifespan` and accessed via `get_checkpointer()`. Thread ID for conversation continuity is `"{user_id}::{knowledge_base_id}"`.
- `LLMFactory.get_model()` constructs a new LLM instance on every call from `settings`.

### RAG Pipeline (`app/rag/`)

Two parallel RAG paths exist:
1. **Agent path** (active): nodes in `app/agent/nodes/` call `app/rag/retriever.py`, `app/rag/chains/grade_source_chain.py`, `app/rag/chains/qa_prompt_chain.py`, etc.
2. **Legacy LCEL path** (`app/rag/pipelines.py`): a simple `RunnablePassthrough` chain used by `POST /api/v1/knowledgeBases/query`. This path hardcodes the model and does not use the checkpointer.

Prompt objects live in `app/rag/prompts/` as module-level constants (e.g. `CONDENSE_PROMPT`, `QA_PROMPT`, `GRADE_SOURCE_PROMPT`). Always import the constant from `app.rag.prompts`, not the module itself — importing the module causes a LangChain `TypeError`.

### Vector Store (`app/vectorstore/`)

- `get_chroma_db()` — `@lru_cache` singleton `chromadb.PersistentClient`
- `Collection_manager` — wraps the client; `get_or_create_collection(user_id)` returns a LangChain `Chroma` object using `EmbeddingFactory`
- `PARTITION_METHOD=2` (default): each user gets their own collection named `{COLLECTION_NAME}_{user_id}`
- `PARTITION_METHOD=1`: all users share a single global collection; documents are distinguished by `knowledge_base_id` metadata filter

### Document Ingestion (`POST /api/v1/knowledgeBases/upload`)

Flow: upload → `base_loader.load()` → `get_recursive_chunks()` → `StoreIndexer.add_documents()` → `upload_file()` (local storage) → save `Document` ORM record. Supported extensions: `pdf`, `md`, `docx`. Limit: 10 MB.

### Startup Sequence (`app/main.py`)

1. `load_dotenv(".env.{APP_ENV}")` — must run first so `LANGCHAIN_*` vars reach `os.environ` before LangChain imports
2. `setup_logging()` — configures structlog
3. `checkpointer_lifespan()` — opens the async Postgres checkpointer connection and runs `saver.setup()` (creates LangGraph checkpoint tables)
