from typing import Any

from pydantic import BaseModel, Field, model_validator


class ChromaUpsertRequest(BaseModel):
    ids: list[str] = Field(..., min_length=1)
    documents: list[str] = Field(..., min_length=1)
    metadatas: list[dict[str, Any]] | None = None

    @model_validator(mode="after")
    def validate_lengths(self):
        if len(self.ids) != len(self.documents):
            raise ValueError("ids and documents must have the same length")
        if self.metadatas is not None and len(self.metadatas) != len(self.ids):
            raise ValueError("metadatas length must match ids length")
        return self


class ChromaQueryRequest(BaseModel):
    query_text: str = Field(..., min_length=1)
    n_results: int = Field(default=5, ge=1, le=20)
    where: dict[str, Any] | None = None


class ChromaQueryResponse(BaseModel):
    ids: list[list[str]]
    documents: list[list[str]] | None = None
    metadatas: list[list[dict[str, Any] | None]] | None = None
    distances: list[list[float]] | None = None
