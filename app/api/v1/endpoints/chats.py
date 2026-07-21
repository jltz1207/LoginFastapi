import json
from typing import AsyncGenerator

from fastapi import APIRouter
from fastapi.params import Depends
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select

from app.agent.dependencies import get_compiled_graph
from app.agent.persistance.agent_config import get_agent_config
from app.agent.state import AgentState
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.asistantMessage import AsistantMessage, RoleEnum
from app.models.user import User
from app.schemas.chats import ChatRequestModel
from app.services.jwtService import getCurrentUser

from langgraph.graph.state import CompiledStateGraph
import traceback

router = APIRouter(tags=["Chats"])
logger = get_logger(__name__)
# node names whose start we surface to the client as a lightweight status update
STATUS_NODES = {"retrieve_docs", "grade_docs", "rewrite_question", "web_searcher", "generate"}


@router.post("/chat")
async def chat(
    requestModel: ChatRequestModel,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(getCurrentUser),
    graph: CompiledStateGraph = Depends(get_compiled_graph)
):
    clean_user_question = requestModel.question.strip()
    init_state = AgentState(
        user_id=current_user.id,
        knoweledge_base_id=requestModel.knowledge_base_id,
        question=requestModel.question,
        chat_messages=[HumanMessage(content=clean_user_question)]
    )
    config = get_agent_config(user_id=current_user.id, knowledge_base_id=requestModel.knowledge_base_id)

    async def event_stream() -> AsyncGenerator[str, None]:
        full_answer = ""
        model_used = "unknown model"
        documents = []
        input_tokens = 0
        output_tokens = 0
        try:
            async for event in graph.astream_events(input=init_state, config=config, version="v2"):
                kind = event["event"]
                node_name = event.get("metadata", {}).get("langgraph_node")

                if kind == "on_chain_start" and event["name"] in STATUS_NODES:
                    yield f"data: {json.dumps({'type': 'status', 'node': event['name']})}\n\n"

                elif kind == "on_chat_model_stream" and node_name == "generate":
                    chunk_content = event["data"]["chunk"].text
                    if chunk_content:
                        full_answer += chunk_content
                        yield f"data: {json.dumps({'type': 'token', 'content': chunk_content})}\n\n"

                elif kind == "on_chain_end" and event["name"] in ("retrieve_docs", "web_searcher"):
                    documents = event["data"]["output"].get("documents", documents)

                elif kind == "on_chain_end" and event["name"] == "generate":
                    output = event["data"]["output"] or {}
                    model_used = output.get("model_used", model_used)
                    ai_message = (output.get("chat_messages") or [None])[-1]
                    usage = getattr(ai_message, "usage_metadata", None) or {}
                    input_tokens = usage.get("input_tokens", input_tokens)
                    output_tokens = usage.get("output_tokens", output_tokens)
        except Exception as e:
            print(f"Exception Type: {type(e).__name__}")
            print(f"Exception Details: {e}")
            print(traceback.format_exc())  # Prints file paths, line numbers, and call stack
        # stream finished — persist both turns now that we have the full answer
        formatted_source_str = "\n".join(
            f"Document {doc_number}: " + doc.page_content for doc_number, doc in enumerate(documents, start=1)
        )

        get_max_seq_num = select(func.max(AsistantMessage.sequence_number)).where(
            AsistantMessage.user_id == current_user.id,
            AsistantMessage.knowledge_base_id == requestModel.knowledge_base_id
        )
        max_seq_num = (await db.execute(get_max_seq_num)).scalar() or 0

        new_user_msg = AsistantMessage(
            user_id=current_user.id,
            knowledge_base_id=requestModel.knowledge_base_id,
            sequence_number=max_seq_num + 1,
            role=RoleEnum.USER,
            content=clean_user_question,
        )
        new_ai_msg = AsistantMessage(
            user_id=current_user.id,
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
