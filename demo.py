"""Exercise retrieval and context assembly using synthetic API responses, offline."""
import json
import sys
from urllib.parse import unquote, urlsplit

from wiki_retrieval import RetrievalError, WikiClient, build_context


def main():
    calls = []

    def fixture_transport(url):
        calls.append(url)
        path = unquote(urlsplit(url).path)
        if path.endswith("/search/page"):
            return {"pages": [{"id": 1, "title": "Étude (synthetic fixture)"},
                              {"id": 2, "title": "Ambiguous fixture"},
                              {"id": 3, "title": "Unavailable fixture"}]}
        if path.endswith("Étude_(synthetic_fixture)"):
            return {"type": "standard", "pageid": 1, "title": "Étude (synthetic fixture)",
                    "revision": "101", "extract": "Synthetic text: café, naïve and 東京 survive retrieval. " * 8}
        if path.endswith("Ambiguous_fixture"):
            return {"type": "disambiguation"}
        raise RetrievalError("Synthetic HTTP 503 fixture")

    documents, failures = WikiClient(fixture_transport).retrieve("synthetic observatory", limit=3)
    context = build_context(documents, max_chars=400)
    bundle = json.loads(context)
    assert len(documents) == 1 and len(failures) == 2
    assert len(context) <= 400 and bundle["sources"][0]["truncated"]
    assert all(value in bundle["sources"][0]["text"] for value in ("café", "naïve", "東京"))
    print("WIKIPEDIA EVIDENCE / OFFLINE DEMO")
    print("Synthetic responses only. No Wikipedia pages or network were accessed.")
    print("Fixture titles and IDs do not identify real articles.\n")
    print(f"Search + summary calls through the injected fixture transport: {len(calls)}")
    print(f"Usable summaries: {len(documents)} | Reported failures: {len(failures)}")
    for failure in failures:
        print(f"  {failure.title}: {failure.reason}")
    print(f"\nContext: {len(context)}/400 characters, including citation metadata")
    print(f"Source ID: {bundle['sources'][0]['id']} | Truncated: {bundle['sources'][0]['truncated']}")
    print("Unicode preserved: café, naïve + Japanese text")
    print("Error messages were kept outside source text.")
    print("\nLive retrieval: python wiki_retrieval.py \"Marie Curie\" --limit 2")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
