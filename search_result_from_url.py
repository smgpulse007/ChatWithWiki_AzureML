"""Prompt Flow-compatible article summaries; errors are never passed as evidence."""
import re

from promptflow_compat import tool
from wiki_retrieval import MAX_RESULTS, WikiClient, bounded_integer, title_from_url


def get_page_sentence(page: str, count: int = 10) -> str:
    """Approximate sentence cap for the legacy helper; preserve Unicode/punctuation."""
    bounded_integer(count, "count", 1, 10)
    return " ".join(re.split(r"(?<=[.!?])\s+", page.strip(), maxsplit=count)[:count])


def fetch_text_content_from_url(url: str, count: int = 10) -> tuple[str, str]:
    bounded_integer(count, "count", 1, 10)
    document = WikiClient().summary(title_from_url(url))
    return document.url, get_page_sentence(document.text, count)


@tool
def search_result_from_url(url_list: list, count: int = 10) -> list[tuple[str, str]]:
    bounded_integer(count, "count", 1, 10)
    if not isinstance(url_list, list) or len(url_list) > MAX_RESULTS:
        raise ValueError(f"url_list must be a list of at most {MAX_RESULTS} Wikipedia article URLs")
    # Validate every entry before any network request. Keep order; deduplicate URLs.
    for url in url_list:
        title_from_url(url)
    return [fetch_text_content_from_url(url, count) for url in dict.fromkeys(url_list)]
