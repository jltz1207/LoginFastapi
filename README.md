# LoginFastAPI

## ChromaDB setup in current structure

This project now includes a ChromaDB integration using these modules:

- app/core/config.py: Chroma settings
- app/services/chroma_service.py: persistent client + collection access
- app/schemas/chroma.py: request/response models
- app/api/v1/endpoints/chroma.py: API endpoints
- app/api/v1/api.py: router registration

### 1) Install dependencies

Install project dependencies after the new Chroma package was added:

uv sync

If you use pip instead of uv:

pip install -e .

### 2) Set environment variables

Create or update .env in project root:

DATABASE_URL=postgresql://postgres:P%40ssw0rd@localhost:5432/AsistAi
SECRET_KEY=a-string-secret-at-least-256-bits-long
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30

CHROMA_PERSIST_DIRECTORY=./chroma_data
CHROMA_COLLECTION_NAME=users_knowledge
CHROMA_OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_API_KEY=your_openai_api_key

OPENAI_API_KEY is optional in code, but required if you want OpenAI embeddings.

### 3) Available endpoints

Base prefix: /api/v1/chroma

- GET /health
- POST /upsert
- POST /query

All routes currently require authentication using the existing bearer token flow.

### 4) Example upsert payload

POST /api/v1/chroma/upsert

{
	"ids": ["doc-1", "doc-2"],
	"documents": [
		"FastAPI is a modern Python web framework.",
		"ChromaDB is a vector database for embeddings."
	],
	"metadatas": [
		{"topic": "fastapi"},
		{"topic": "vectordb"}
	]
}

### 5) Example query payload

POST /api/v1/chroma/query

{
	"query_text": "python web framework",
	"n_results": 3,
	"where": {"topic": "fastapi"}
}

### 6) Run app

uv run uvicorn app.main:app --reload

Then open docs at:

http://127.0.0.1:8000/docs
