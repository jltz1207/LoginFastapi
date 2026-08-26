from abc import abstractmethod
import abc

from langchain_core.runnables import Runnable

class RetrieverFactory(abc.ABC):
    @abstractmethod
    async def get_retriever(
        self, user_id: str, knowledge_base_id: str, top_k: int = 8
    ) -> Runnable: ...

