import abc
from enum import Enum
from langgraph.graph.state import CompiledStateGraph
from langgraph.checkpoint.base import BaseCheckpointSaver

class BaseGraphFactory(abc.ABC):
    @staticmethod
    @abc.abstractmethod
    def build(checkpointer: BaseCheckpointSaver, **kwargs) -> CompiledStateGraph:
        pass

class GraphStrategy(Enum):
    ADAPTIVE = 1
    SEARCH = 2
    SELF = 3
