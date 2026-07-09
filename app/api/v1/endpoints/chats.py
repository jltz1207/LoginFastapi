from fastapi import APIRouter
from fastapi.params import Depends
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

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

    msg = [HumanMessage(content=requestModel.question)]
    init_state = AgentState(
        user_id=current_user.id,
        knoweledge_base_id=requestModel.knowledge_base_id,
        question=requestModel.question,
        chat_messages=msg # merge with the old
    )
    config = get_agent_config(user_id=current_user.id, knowledge_base_id=requestModel.knowledge_base_id)
    final_state = await graph.ainvoke(input=init_state, config=config)
    
    # save record to db
    return {}
