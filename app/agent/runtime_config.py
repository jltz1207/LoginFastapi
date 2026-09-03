from typing import Optional
from uuid import UUID

from app.agent.budget import BudgetLimits


def get_agent_config(user_id:UUID, knowledge_base_id:UUID, tools:Optional[list] = None):
    thread_id = f"{str(user_id)}::{str(knowledge_base_id)}"
    budget_limits = BudgetLimits()
    config = {
        "configurable":{
            "thread_id": thread_id,
            "budget_limits": budget_limits.model_dump()
        }
    }
    return config