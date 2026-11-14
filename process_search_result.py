"""Format retrieved text with stable source IDs and an explicit context budget."""
from promptflow_compat import tool
from wiki_retrieval import MAX_RESULTS, Document, article_url, build_context, title_from_url


@tool
def process_search_result(search_result: list, max_chars: int = 12000) -> str:
    if not isinstance(search_result, list) or len(search_result) > MAX_RESULTS:
        raise ValueError(f"search_result must be a list of at most {MAX_RESULTS} (URL, text) pairs")
    documents = []
    for result in search_result:
        if not isinstance(result, (list, tuple)) or len(result) != 2:
            raise ValueError("each search result must be a (URL, text) pair")
        url, content = result
        title = title_from_url(url)
        if not isinstance(content, str) or not content.strip():
            raise ValueError("source content must be nonempty text")
        documents.append(Document(title, article_url(title), content, 0))
    return build_context(documents, max_chars)
