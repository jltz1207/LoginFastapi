import abc
from langgraph.graph.state import CompiledStateGraph


class BaseGraphFactory(abc.ABC):
    @staticmethod
    @abc.abstractmethod
    def build() -> CompiledStateGraph:
        pass