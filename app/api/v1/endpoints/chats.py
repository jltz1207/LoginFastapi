import json
from typing import AsyncGenerator

from fastapi import APIRouter
from fastapi.params import Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select

from app.agent.dependencies import build_initial_state, get_compiled_graph
from app.agent.runtime_config import get_agent_config
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.asistantMessage import AsistantMessage, RoleEnum
from app.models.user import User
from app.routing.branches.common import FINAL_ANSWER_TAG
from app.schemas.chats import ChatRequestModel
from app.services.jwtService import getCurrentUser
from app.services.knowledgeBaseService import get_owned_knowledge_base

from langgraph.graph.state import CompiledStateGraph
import traceback

router = APIRouter(tags=["Chats"])
logger = get_logger(__name__)
# node names whose start we surface to the client as a lightweight status update
STATUS_NODES = {"retrieve_docs", "grade_docs", "rewrite_question", "web_searcher", "generate", "finalize"}
# nodes that produce the user-facing answer; `finalize` takes over when the tool budget runs out
ANSWER_NODES = ("generate", "finalize")
# routed graph: every branch ends in `respond`, whose output `answer` is the final answer for any route
RESPOND_NODE = "respond"


# nodes whose output `documents` (Chunks) are the sources of the answer; the last one to finish wins
DOCUMENT_NODES = ("retrieve_docs", "web_searcher", "global", "multi_hop", "no_retrieval", "meta")
# METADATA answers from SQL rows (`sql_documents`), not Chunks
SQL_DOCUMENT_NODE = "metadata"


def _format_sources(documents: list, sql_documents: list[dict]) -> str:
    # `documents` are `Chunk`s (app/agent/state.py): the text is `content`, not langchain's `page_content`
    lines = [f"Document {n}: " + doc.content for n, doc in enumerate(documents, start=1)]
    lines += [
        f"Document {n}: {row.get('filename', '')} ({row.get('id', '')})"
        for n, row in enumerate(sql_documents, start=len(lines) + 1)
    ]
    return "\n".join(lines)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _is_answer_token(event: dict, node_name: str | None) -> bool:
    """LOOKUP streams from its answer nodes; routed branches tag their final-answer LLM call
    (MULTI_HOP decompose / GLOBAL map calls run in the same node and must not be streamed)."""
    return node_name in ANSWER_NODES or FINAL_ANSWER_TAG in event.get("tags", [])


@router.post("/chat")
async def chat(
    requestModel: ChatRequestModel,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(getCurrentUser),
    graph: CompiledStateGraph = Depends(get_compiled_graph)
):
    await get_owned_knowledge_base(db, requestModel.knowledge_base_id, current_user)
    clean_user_question = requestModel.question.strip()
    init_state = build_initial_state(
        user_id=str(current_user.id),
        tenant_id=str(current_user.tenant_id),
        knowledge_base_id=str(requestModel.knowledge_base_id),
        question=clean_user_question,
    )
    config = get_agent_config(user_id=current_user.id, knowledge_base_id=requestModel.knowledge_base_id)

    async def event_stream() -> AsyncGenerator[str, None]:
        full_answer = ""
        model_used = "unknown model"
        documents = []
        sql_documents = []
        input_tokens = 0
        output_tokens = 0
        try:
            async for event in graph.astream_events(input=init_state, config=config, version="v2"):
                kind = event["event"]
                node_name = event.get("metadata", {}).get("langgraph_node")

                if kind == "on_chain_start" and event["name"] in STATUS_NODES:
                    yield f"data: {json.dumps({'type': 'status', 'node': event['name']})}\n\n"

                elif kind == "on_chat_model_stream" and _is_answer_token(event, node_name):
                    chunk_content = event["data"]["chunk"].text
                    if chunk_content:
                        full_answer += chunk_content
                        yield _sse({"type": "token", "content": chunk_content})

                elif kind == "on_chain_end" and event["name"] == RESPOND_NODE:
                    answer = (event["data"]["output"] or {}).get("answer", "")
                    if not full_answer and answer:
                        # META / METADATA answer from a template or a SQL result, with no LLM to stream
                        yield _sse({"type": "token", "content": answer})
                    # `respond` holds the whole answer; prefer it to the concatenated tokens
                    full_answer = answer or full_answer

                elif kind == "on_chain_end" and event["name"] in DOCUMENT_NODES:
                    documents = (event["data"]["output"] or {}).get("documents", documents)

                elif kind == "on_chain_end" and event["name"] == SQL_DOCUMENT_NODE:
                    sql_documents = (event["data"]["output"] or {}).get("sql_documents", sql_documents)

                elif kind == "on_chain_end" and event["name"] in ANSWER_NODES:
                    output = event["data"]["output"] or {}
                    model_used = output.get("model_used", model_used)
                    ai_message = (output.get("chat_history") or [None])[-1]
                    usage = getattr(ai_message, "usage_metadata", None) or {}
                    # accumulate: a tool loop means several LLM round-trips per request
                    input_tokens += usage.get("input_tokens", 0)
                    output_tokens += usage.get("output_tokens", 0)
        except Exception as e:
            print(f"Exception Type: {type(e).__name__}")
            print(f"Exception Details: {e}")
            print(traceback.format_exc())  # Prints file paths, line numbers, and call stack
        # stream finished — persist both turns now that we have the full answer
        formatted_source_str = _format_sources(documents, sql_documents)

        get_max_seq_num = select(func.max(AsistantMessage.sequence_number)).where(
            AsistantMessage.user_id == current_user.id,
            AsistantMessage.knowledge_base_id == requestModel.knowledge_base_id
        )
        max_seq_num = (await db.execute(get_max_seq_num)).scalar() or 0

        new_user_msg = AsistantMessage(
            user_id=current_user.id,
            tenant_id=current_user.tenant_id,
            knowledge_base_id=requestModel.knowledge_base_id,
            sequence_number=max_seq_num + 1,
            role=RoleEnum.USER,
            content=clean_user_question,
        )
        new_ai_msg = AsistantMessage(
            user_id=current_user.id,
            tenant_id=current_user.tenant_id,
            knowledge_base_id=requestModel.knowledge_base_id,
            sequence_number=max_seq_num + 2,
            role=RoleEnum.AI,
            content=full_answer,
            model=model_used,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            sources=formatted_source_str
        )
        db.add(new_user_msg)
        db.add(new_ai_msg)
        await db.commit()

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
