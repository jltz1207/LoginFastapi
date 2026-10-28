import asyncio

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from app.llm.usage import count_llm_tokens


def _model(total_tokens, n=10):
    # No `model_name` in response_metadata: the counter must not depend on it.
    return GenericFakeChatModel(
        messages=iter(
            AIMessage(
                content="x",
                usage_metadata={"input_tokens": 0, "output_tokens": total_tokens, "total_tokens": total_tokens},
            )
            for _ in range(n)
        )
    )


def test_counts_calls_inside_the_block_only():
    model = _model(7)

    async def run():
        await model.ainvoke("before")
        with count_llm_tokens() as counter:
            await model.ainvoke("a")
            await asyncio.gather(model.ainvoke("b"), model.ainvoke("c"))
        await model.ainvoke("after")
        return counter

    counter = asyncio.run(run())

    assert counter.total_tokens == 21


def test_nested_blocks_count_separately():
    model = _model(5)

    async def run():
        with count_llm_tokens() as outer:
            await model.ainvoke("a")
            with count_llm_tokens() as inner:
                await model.ainvoke("b")
            await model.ainvoke("c")
        return outer, inner

    outer, inner = asyncio.run(run())

    assert inner.total_tokens == 5
    assert outer.total_tokens == 10
