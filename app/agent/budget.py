from pydantic import BaseModel
from app.core.config import settings
from langchain_core.runnables import RunnableConfig

class BudgetLimits(BaseModel):
    max_tool_calls: int = settings.MAX_TOOL_CALLS
    max_tokens: int = settings.MAX_TOKENS
    max_tool_result_chars: int = settings.MAX_TOOL_RESULT_CHARS

def _limits(config: RunnableConfig) -> BudgetLimits:
    raw = config.get("configurable", {}).get("budget_limits", None) or None
    return BudgetLimits.model_validate(raw) if raw else BudgetLimits()