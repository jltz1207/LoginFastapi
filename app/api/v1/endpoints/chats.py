from fastapi import APIRouter
from fastapi.params import Depends
from sqlalchemy.orm import Session

from app.agent.dependencies import get_compiled_graph
from app.agent.persistance.agent_config import get_agent_config
from app.agent.state import AgentState
from app.db.session import get_db
from app.models.user import User
from app.schemas.chats import ChatRequestModel
from app.services.jwtService import getCurrentUser

from langgraph.graph.state import CompiledStateGraph

router = APIRouter(tags=["Chats"])
@router.post("/chat", response_model={})
async def chat(
    requestModel: ChatRequestModel,
    db:Session = Depends(get_db),
    current_user: User = Depends(getCurrentUser),
    graph: CompiledStateGraph = Depends(get_compiled_graph)
):
    curr_chat_messages = []
    init_state: AgentState = AgentState(
        user_id=current_user.id,
        knoweledge_base_id=requestModel.knoweledge_base_id,
        question=requestModel.question,
        chat_messages=curr_chat_messages
    )
    config = get_agent_config(user_id=current_user.id,knowledge_base_id=requestModel.knowledge_base_id )
    graph.invoke(
        state=init_state,
        config=config
    )
