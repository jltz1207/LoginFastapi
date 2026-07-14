from fastapi import APIRouter
from fastapi.params import Depends
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func
from app.agent.dependencies import get_compiled_graph
from app.agent.persistance.agent_config import get_agent_config
from app.agent.state import AgentState
from app.db.session import get_db
from app.models.asistantMessage import AsistantMessage, MsgStatusEnum, RoleEnum
from app.models.user import User
from app.schemas.chats import ChatRequestModel
from app.services.jwtService import getCurrentUser

from langgraph.graph.state import CompiledStateGraph
from sqlalchemy import select
router = APIRouter(tags=["Chats"])

@router.post("/chat", response_model={})
async def chat(
    requestModel: ChatRequestModel,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(getCurrentUser),
    graph: CompiledStateGraph = Depends(get_compiled_graph)
):
    clean_user_question = requestModel.question.strip()
    msg = [HumanMessage(content=clean_user_question)]
    init_state = AgentState(
        user_id=current_user.id,
        knoweledge_base_id=requestModel.knowledge_base_id,
        question=requestModel.question,
        chat_messages=msg # merge with the old
    )
    config = get_agent_config(user_id=current_user.id, knowledge_base_id=requestModel.knowledge_base_id)
    final_state = await graph.ainvoke(input=init_state, config=config)


    # worker pattern: save record to db (1. user 2. ai)
    result_ai_message = final_state["chat_messages"][-1]
    result_ai_message_usage_metadata = getattr(result_ai_message, "usage_metadata", {})
    result_ai_message_input_tokens = result_ai_message_usage_metadata.get("input_tokens", 0)
    result_ai_message_output_tokens = result_ai_message_usage_metadata.get("output_tokens", 0)
    formatted_source_str = "\n".join([f"Document {doc_number}: " + doc.page_content for doc_number, doc in enumerate(final_state.documents, start=1)])
    
    get_max_seq_num = select(func.max(AsistantMessage.sequence_number)).where(
        AsistantMessage.user_id == current_user.id,
        AsistantMessage.knowledge_base_id == requestModel.knowledge_base_id
    )
    max_seq_num = (await db.execute(get_max_seq_num)).scalars() or 0
    
    new_user_msg =  AsistantMessage(
        user_id=current_user.id, 
        knowledge_base_id=requestModel.knowledge_base_id, 
        sequence_number=max_seq_num+1, 
        role=RoleEnum.USER,
        content=clean_user_question,
    )
    new_ai_msg =  AsistantMessage(
        user_id=current_user.id, 
        knowledge_base_id=requestModel.knowledge_base_id, 
        sequence_number=max_seq_num+2, 
        role=RoleEnum.AI,
        content=result_ai_message.content,
        model=final_state["model_used"],
        prompt_tokens=result_ai_message_input_tokens,
        completion_tokens=result_ai_message_output_tokens,
        sources=formatted_source_str
    )
    db.add(new_user_msg)
    db.add(new_ai_msg)
    await db.commit()
    return {}
