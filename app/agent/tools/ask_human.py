from langchain_core.tools import tool
from langgraph.types import interrupt


@tool
def ask_human(question: str) -> str:
    """Pause execution and ask the human a question before proceeding.

    Use this tool whenever you need clarification, confirmation, or additional
    input from the user before taking a significant action (e.g. deleting data,
    sending emails, making purchases, or any irreversible operation).

    Args:
        question: The question to ask the human.

    Returns:
        str: The human's response.
    """
    user_response = interrupt(question)
    return str(user_response)