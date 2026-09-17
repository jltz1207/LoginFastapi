from abc import abstractmethod
import abc

from langchain_core.runnables import Runnable

class RetrieverFactory(abc.ABC):
    # Sync on purpose: implementations only assemble a pipeline. Anything expensive
    # (e.g. building a BM25 index) must be deferred to invoke time, not done here.
    @abstractmethod
    def get_retriever(
        self, tenant_id: str, user_id: str, knowledge_base_id: str, top_k: int = 8
    ) -> Runnable: ...

