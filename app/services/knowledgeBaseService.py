import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models.asistantKnowledgeBase import AsistantKnowledgeBase


async def get_owned_knowledge_base(
    db: AsyncSession, knowledge_base_id: uuid.UUID | str, current_user: User
) -> AsistantKnowledgeBase:
    """Load a knowledge base only if it belongs to the current user's tenant and user.

    `knowledge_base_id` always comes from the client, so every endpoint that takes one
    must call this before touching documents, vectors or messages. 404 (not 403) for
    someone else's KB, so the response does not reveal that the id exists.
    """
    try:
        kb_uuid = knowledge_base_id if isinstance(knowledge_base_id, uuid.UUID) else uuid.UUID(str(knowledge_base_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    stmt = select(AsistantKnowledgeBase).where(
        AsistantKnowledgeBase.id == kb_uuid,
        AsistantKnowledgeBase.tenant_id == current_user.tenant_id,
        AsistantKnowledgeBase.user_id == current_user.id,
        AsistantKnowledgeBase.deleted_at.is_(None),
    )
    kb = (await db.execute(stmt)).scalars().first()
    if kb is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    return kb
