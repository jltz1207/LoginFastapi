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
PARTITION_METHOD=3                 # 1=global collection, 2=one-per-user, 3=one-per-tenant
GRAPH_STRATEGY=4                   # 4=ROUTED (default), 2=SEARCH (LOOKUP only, kill switch)
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

The agent is a compiled `StateGraph` built at request time via `get_compiled_graph()` in `app/agent/dependencies.py`. The graph variant is selected by `GRAPH_STRATEGY`; `build_initial_state()` in the same file builds the matching input state, so `POST /chat` never imports a state class.

**`RoutedGraphFactory` (strategy 4, default) — `app/agent/graphs/routed_rag.py`:**
```
START → resolve → route ─┬→ lookup / global / metadata / multi_hop / no_retrieval / meta → respond → END
                         └ a decision that is not accepted falls back to lookup
```

- `resolve`: `QueryResolver` (`app/routing/resolver.py`) rewrites a follow-up into a standalone question from the last 6 human/ai messages before this turn. No history → no LLM call. A rewrite failure falls back to the original query.
- `route`: `CascadeRouter` (rule → embedding → LLM, `app/routing/router/`). If it raises, `FallbackChainRouter` runs. Routers are process-wide singletons (`router/defaults.py`).
- Branches live in `app/routing/branches/`. `lookup` is the strategy 2 graph below, used as a subgraph. Every branch must call `enforce_knowledge_base_id()` for tenant scope and never trust `state.filters`.
- `respond`: every route ends here. It puts the answer in both `answer` and `chat_history`, so the next turn's resolver sees it, and logs `route` / `confidence` / `trace`. `trace` is never sent to the user.
- The checkpointer is attached to the parent graph only; the `lookup` subgraph inherits it.
- `router/speculative.py` and `cache/semantic_cache.py` are experimental and not wired into the graph.

**SSE contract (`app/api/v1/endpoints/chats.py`):** tokens come from LOOKUP's `generate` / `finalize` nodes, or from any LLM call tagged `FINAL_ANSWER_TAG` (`app/routing/branches/common.py`). A branch must tag only the call that produces the user-facing answer (`ainvoke(..., config={"tags": [FINAL_ANSWER_TAG]})`), never decompose / map calls. Branches without an LLM (META, METADATA) are sent in one piece from `respond`'s `answer`. Stored token counts sum every LLM call of the turn.

**`LookupRagGraphFactory` (strategy 2) — `app/agent/graphs/lookup_rag.py`:**
```
START → retrieve_docs → grade_docs
                              ↓ grade > 5 → generate → END
                              ↓ grade ≤ 5 → rewrite_question → web_searcher → grade_docs (loop)
```

- State is `LookupAgentState` / `RoutedAgentState` (`app/agent/state.py`, Pydantic). An input built from the model overwrites every non-reducer field in the checkpoint, so `trace`, `route`, `documents` and the budget counters start fresh each turn; only `chat_history` (`add_messages`) accumulates.
- The checkpointer is `AsyncPostgresSaver` (Postgres-backed), initialized at app startup in `checkpointer_lifespan` and accessed via `get_checkpointer()`. Thread ID for conversation continuity is `"{user_id}::{knowledge_base_id}"`.
- `LLMFactory.get_model()` returns a cached model (primary + two fallbacks via `with_fallbacks`) built from `settings`. Every LLM call in `app/agent/` and `app/routing/` goes through it.

### RAG Pipeline (`app/rag/`)

Two parallel RAG paths exist:
1. **Agent path** (active): nodes in `app/agent/nodes/` call `app/rag/retriever.py`, `app/rag/chains/grade_source_chain.py`, `app/rag/chains/qa_prompt_chain.py`, etc.
2. **Legacy LCEL path** (`app/rag/pipelines.py`): a simple `RunnablePassthrough` chain used by `POST /api/v1/knowledgeBases/query`. This path hardcodes the model and does not use the checkpointer.

Prompt objects live in `app/rag/prompts/` as module-level constants (e.g. `CONDENSE_PROMPT`, `QA_PROMPT`, `GRADE_SOURCE_PROMPT`). Always import the constant from `app.rag.prompts`, not the module itself — importing the module causes a LangChain `TypeError`.

### Vector Store (`app/vectorstore/`)

- `get_chroma_db()` — `@lru_cache` singleton `chromadb.PersistentClient`
- `Collection_manager` — wraps the client; `get_or_create_collection(user_id)` returns a LangChain `Chroma` object using `EmbeddingFactory`
- `PARTITION_METHOD=3` (default): each tenant gets its own collection named `{COLLECTION_NAME}_{EMBEDDING_VERSION}_{tenant_id}`
- `PARTITION_METHOD=2`: each user gets their own collection named `{COLLECTION_NAME}_{EMBEDDING_VERSION}_{user_id}`
- `PARTITION_METHOD=1`: all users share a single global collection `{COLLECTION_NAME}_{EMBEDDING_VERSION}`
- Under every strategy, isolation is enforced by the tenant / user / kb metadata filter in `StoreIndexer.scope_filter()`, not by the collection name

### Document Ingestion (`POST /api/v1/knowledgeBases/upload`)

Ingestion runs in the background (`app/ingestion/`). The upload endpoint only validates, stores the file and queues it:

- **Upload** (returns **202**): `get_owned_knowledge_base()` → extension check (`base_loader.SUPPORTED_EXTENSIONS`: `pdf`, `md`; no `docx` loader exists) → 10 MB limit → `upload_file()` → insert `Document(status=PENDING)` → `notify_ingestion_worker()`.
- **Status**: `GET /api/v1/knowledgeBases/documents/{document_id}`, scoped to the caller's tenant + user (404 otherwise). Returns `status`, `error_message`, `chunk_count`, `indexed_at`, `has_summary`.
- **Queue**: the `documents` table itself (`app/ingestion/repository.py`). The worker claims a `PENDING` row with `FOR UPDATE SKIP LOCKED` and bumps `attempt_count`. Every later write is fenced on `status = PROCESSING AND attempt_count = <claimed>`. A `PROCESSING` row older than `INGESTION_LEASE_SECONDS` is requeued, or set to `FAILED` after `INGESTION_MAX_ATTEMPTS`.
- **Worker** (`app/ingestion/worker.py`): an asyncio loop started by `ingestion_worker_lifespan()` **inside the API process**. It runs there on purpose: BM25 invalidation (`app/vectorstore/events.py`) only reaches the same process, and Chroma is a local `PersistentClient`. Set `INGESTION_WORKER_ENABLED=false` to turn it off.
- **Processing** (`process_document()` in `app/ingestion/processor.py`): takes only `document_id` and reads tenant / user / kb / storage key from the DB row. Steps: download → `base_loader.load()` → `raw_text` (cleaned loader output, not joined chunks) → chunk → summary → `StoreIndexer.delete_document_chunks()` → `StoreIndexer.add_documents(ids=["{document_id}:{i}"])` → `INDEXED` + `indexed_at`.
  - Any exception sets `FAILED` with `error_message` and deletes the document's chunks.
  - Chroma is written last, so chunks are searchable before `INDEXED` only for one DB commit.
  - Chunk metadata carries `document_id`. Re-runs delete and then upsert, so they never duplicate chunks.
- **Summary** (`app/ingestion/summarizer.py`): map-reduce over `raw_text` with `LLMFactory.get_model()` and the `DOCUMENT_SUMMARY_*_PROMPT` constants. A summary failure does **not** fail the document: it is still `INDEXED`, with `summary` NULL (GLOBAL skips it) and `error_message` set to `summary failed: ...`. If a summary already exists, a retry skips generating it.
- **Backfill**: `uv run python -m scripts.backfill_document_summaries [--apply]` generates summaries for `INDEXED` rows that have none. It does not touch Chroma.

### Startup Sequence (`app/main.py`)

1. `load_dotenv(".env.{APP_ENV}")` — must run first so `LANGCHAIN_*` vars reach `os.environ` before LangChain imports
2. `setup_logging()` — configures structlog
3. `checkpointer_lifespan()` — opens the async Postgres checkpointer connection and runs `saver.setup()` (creates LangGraph checkpoint tables)
4. `ingestion_worker_lifespan()` — starts the background ingestion loop; on shutdown it is cancelled, and a document cut off mid-run is requeued once its lease expires
