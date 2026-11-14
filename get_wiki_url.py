"""Prompt Flow-compatible URL discovery using the Wikipedia search API."""
from promptflow_compat import tool
from wiki_retrieval import WikiClient


@tool
def get_wiki_url(entity: str, count: int = 2) -> list[str]:
    return [source.url for source in WikiClient().search(entity, limit=count)]
