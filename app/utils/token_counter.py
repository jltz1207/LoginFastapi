
from functools import lru_cache

import tiktoken

@lru_cache(maxsize=8)
def _get_encoding(model: str) -> tiktoken.Encoding:
    """Cache the encoding object — expensive to load, cheap to reuse."""
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        # fallback if model name isn't recognised by tiktoken
        return tiktoken.get_encoding("cl100k_base")

def count_tokens(texts: list[str], model: str) -> int:
    """Count total tokens across a list of texts."""
    enc = _get_encoding(model)
    return sum(len(enc.encode(text)) for text in texts)