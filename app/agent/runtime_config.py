
from typing import Optional
from uuid import UUID


def get_agent_config(user_id:UUID, knowledge_base_id:UUID, tools:Optional[list] = None):
    thread_id = f"{str(user_id)}::{str(knowledge_base_id)}"
    config = {
        "configurable":{
            "thread_id": thread_id
        }
    }
    return config