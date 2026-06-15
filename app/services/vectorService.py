from sqlalchemy.orm import Session
from sqlalchemy import select

from app.db import get_chroma_db
from app.models.user_collection import UserCollection
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings
from app.core import settings


class VectorService:
    def get_openai_embedding_function(self):
        if settings.OPENAI_API_KEY and settings.OPENAI_EMBEDDING_MODEL:
            return OpenAIEmbeddings(
                model=settings.OPENAI_EMBEDDING_MODEL,
                api_key=settings.OPENAI_API_KEY,
            )
        return None

    def get_or_create_vector(self, user_id: int, db: Session) -> Chroma:
        collection_name = f"user_{user_id}"
        chroma_db = get_chroma_db()

        # Ensure collection exists in Chroma
        chroma_db.client.get_or_create_collection(collection_name)

        # Upsert state record in PostgreSQL
        record = db.execute(
            select(UserCollection).where(UserCollection.user_id == user_id)
        ).scalars().first()

        if not record:
            record = UserCollection(user_id=user_id, collection_name=collection_name)
            db.add(record)
            db.commit()
            db.refresh(record)

        vector_store = Chroma(
            client=chroma_db.client,
            collection_name=collection_name,
            embedding_function=self.get_openai_embedding_function(),
        )
        return vector_store

    def add_documents(self, user_id:int, docs, db:Session):
        record = db.execute(select(UserCollection).where(UserCollection.user_id == user_id)).scalars().first()
        if not record:
            return None
        
        user_vector_store = self.get_or_create_vector(self, record.collection_name, db)
        if not user_vector_store:
            return None
        
        user_vector_store.add_documents(docs)
        record.document_count  = (record.document_count or 0) + len(docs)
        db.commit()
        db.refresh(record)
        return len(docs)
    
    

            

 

vector_service = VectorService()

  
