"""Generate `documents.summary` for documents ingested before summaries existed.

Targets rows that are `INDEXED`, not deleted, and have no summary. For each one the
original file is read back from storage and loaded again, so `raw_text` is also rewritten
in the new format (cleaned loader output, instead of chunks joined with "\\n."). The
summary is then generated exactly as the ingestion worker does.

Chroma is not touched. Chunks ingested before `document_id` was added to their metadata
keep lacking it; re-ingesting them would leave the old chunks behind as duplicates.

A document whose summary fails is reported and left as it was; re-run the script to
retry. Rows that got a summary in the meantime are not overwritten.

Usage (from the repo root; dry run unless --apply, and a dry run makes no LLM calls):

    uv run python -m scripts.backfill_document_summaries
    uv run python -m scripts.backfill_document_summaries --apply
"""

import argparse
import asyncio
import os
import selectors
import sys
from collections import Counter


async def backfill(session_factory, summarizer, download, load, *, apply: bool, limit: int | None) -> Counter:
    from sqlalchemy import select, update

    from app.ingestion.processor import build_raw_text, load_pages
    from app.ingestion.repository import DocumentRecord
    from app.models.document import Document, IngestionStatus

    stmt = (
        select(Document)
        .where(
            Document.status == IngestionStatus.INDEXED,
            Document.summary.is_(None),
            Document.deleted_at.is_(None),
        )
        .order_by(Document.created_dt)
    )
    if limit:
        stmt = stmt.limit(limit)
    async with session_factory() as db:
        docs = (await db.execute(stmt)).scalars().all()

    counts = Counter(candidates=len(docs))
    for doc in docs:
        label = f"{doc.id}  tenant={doc.tenant_id}  kb={doc.knowledge_base_id}  {doc.filename}"
        if not apply:
            print(f"WOULD SUMMARIZE  {label}")
            continue
        record = DocumentRecord(
            id=doc.id,
            tenant_id=doc.tenant_id,
            user_id=doc.user_id,
            knowledge_base_id=doc.knowledge_base_id,
            file_extension=doc.file_extension,
            storage_bucket=doc.storage_bucket,
            storage_key=doc.storage_key,
            summary=None,
        )
        try:
            raw_text = build_raw_text(await load_pages(record, download, load))
            summary = await summarizer.summarize(raw_text)
        except Exception as exc:
            counts["failed"] += 1
            print(f"FAILED  {label}  {type(exc).__name__}: {exc}")
            continue
        async with session_factory() as db:
            result = await db.execute(
                update(Document)
                .where(Document.id == doc.id, Document.summary.is_(None))
                .values(raw_text=raw_text, summary=summary)
            )
            await db.commit()
        if result.rowcount == 1:
            counts["summarized"] += 1
            print(f"SUMMARIZED  {label}  ({len(summary)} chars)")
        else:
            counts["skipped"] += 1
            print(f"SKIPPED (summary appeared meanwhile)  {label}")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="generate and write summaries (default: dry run)")
    parser.add_argument("--limit", type=int, default=None, help="process at most this many documents")
    args = parser.parse_args()

    # Same env loading as app/main.py, before settings is imported.
    from dotenv import load_dotenv

    load_dotenv(f".env.{os.getenv('APP_ENV', 'dev')}")

    from app.core.storage.fileStorage import download_file
    from app.db.session import AsyncSessionLocal
    from app.ingestion.summarizer import DocumentSummarizer
    from app.rag.loaders.base_loader import base_loader

    coro = backfill(
        AsyncSessionLocal, DocumentSummarizer(), download_file, base_loader.load, apply=args.apply, limit=args.limit
    )
    # psycopg async cannot run on Windows' default ProactorEventLoop.
    if sys.platform == "win32":
        counts = asyncio.run(coro, loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    else:
        counts = asyncio.run(coro)
    print(dict(counts))


if __name__ == "__main__":
    main()
