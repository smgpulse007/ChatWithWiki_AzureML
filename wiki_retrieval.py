"""Small, read-only English Wikipedia evidence retriever. Standard library only."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
import re
import sys
import unicodedata
from http.client import HTTPException as HTTPProtocolError
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ORIGIN = "https://en.wikipedia.org"
USER_AGENT = "WikiEvidence/1.0 (https://github.com/smgpulse007/ChatWithWiki_AzureML)"
MAX_RESPONSE_BYTES = 512 * 1024
MAX_RESULTS = 5


class RetrievalError(RuntimeError):
    """A bounded, user-readable failure without response bodies or query logging."""


def bounded_integer(value: int, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer from {low} to {high}")
    return value


def _text(value: object, name: str, multiline: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    if any(unicodedata.category(c) in ("Cc", "Cs") and not (multiline and c in "\n\r\t") for c in value):
        raise ValueError(f"{name} contains unsupported control or surrogate characters")
    return value


def _title(value: object) -> str:
    value = _text(value, "title")
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError("title must contain 1 to 512 characters")
    return value.strip().replace("_", " ")


def _path(url: str):
    parsed = urlsplit(_text(url, "URL"))
    decoded = unquote(parsed.path, errors="strict")
    if "\\" in decoded or any(part in (".", "..") for part in decoded.split("/")):
        raise ValueError("URL contains path traversal segments")
    return parsed


def _revision(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, (str, int)) or isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]{0,19}", str(value)):
        raise ValueError("invalid revision")
    return str(value)


def article_url(title: str) -> str:
    return ORIGIN + "/wiki/" + quote(_title(title).replace(" ", "_"), safe="")


def title_from_url(url: str) -> str:
    """Accept article URLs, never arbitrary fetch targets or search/index URLs."""
    if not isinstance(url, str) or len(url) > 4096 or any(c.isspace() for c in url):
        raise ValueError("expected an HTTPS English Wikipedia article URL")
    parsed = _path(url)
    if (parsed.scheme != "https" or parsed.netloc != "en.wikipedia.org"
            or not parsed.path.startswith("/wiki/") or parsed.query or parsed.fragment):
        raise ValueError("expected an HTTPS English Wikipedia article URL")
    return _title(unquote(parsed.path[len("/wiki/"):], errors="strict"))


def _check_api_url(url: str) -> None:
    try:
        parsed = _path(url)
    except ValueError:
        raise RetrievalError("Wikipedia returned an invalid redirect URL") from None
    if not url.isascii():
        raise RetrievalError("Wikipedia API URLs must be ASCII with percent-encoded text")
    if (parsed.scheme != "https" or parsed.netloc != "en.wikipedia.org"
            or not parsed.path.startswith(("/w/rest.php/v1/", "/api/rest_v1/"))):
        raise RetrievalError("Wikipedia redirected outside the permitted API origin")


class _WikipediaRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_api_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def request_json(url: str, timeout: float = 10.0) -> dict:
    """One GET with a socket timeout, byte cap, and same-origin redirects."""
    _check_api_url(url)
    if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise ValueError("timeout must be finite, greater than 0 and at most 30 seconds")
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with build_opener(_WikipediaRedirects()).open(request, timeout=timeout) as response:
            _check_api_url(response.geturl())
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        status = exc.code
        exc.close()
        raise RetrievalError(f"Wikipedia returned HTTP {status}; retry later if rate limited") from None
    except (URLError, TimeoutError, OSError, HTTPProtocolError):
        raise RetrievalError("Wikipedia request failed or timed out") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise RetrievalError("Wikipedia response exceeded the 512 KiB limit")
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError):
        raise RetrievalError("Wikipedia returned invalid JSON") from None
    if not isinstance(result, dict):
        raise RetrievalError("Wikipedia returned an unexpected response")
    return result


@dataclass(frozen=True)
class Source:
    title: str
    url: str
    page_id: int


@dataclass(frozen=True)
class Document:
    title: str
    url: str
    text: str
    page_id: int
    revision: str | None = None
    truncated: bool = False


@dataclass(frozen=True)
class Failure:
    title: str
    reason: str


class WikiClient:
    def __init__(self, transport: Callable[[str], dict] | None = None):
        if transport is not None and not callable(transport):
            raise ValueError("transport must be callable or None")
        self._get = request_json if transport is None else transport

    def search(self, query: str, limit: int = 3) -> list[Source]:
        bounded_integer(limit, "limit", 1, MAX_RESULTS)
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ValueError("query must contain 1 to 500 characters")
        _text(query, "query")
        query = query.strip()
        data = self._get(ORIGIN + "/w/rest.php/v1/search/page?" + urlencode({"q": query, "limit": limit}))
        pages = data.get("pages") if isinstance(data, dict) else None
        if not isinstance(pages, list):
            raise RetrievalError("Wikipedia search response has no pages list")
        results, seen = [], set()
        for page in pages[:MAX_RESULTS]:
            try:
                page_id = bounded_integer(page["id"], "page ID", 1, 2**63 - 1)
                title = _title(page["title"])
            except (KeyError, TypeError, ValueError):
                raise RetrievalError("Wikipedia returned an invalid search result") from None
            if page_id not in seen:
                results.append(Source(title, article_url(title), page_id))
                seen.add(page_id)
            if len(results) == limit:
                break
        return results

    def summary(self, title: str, max_chars: int = 4000) -> Document:
        bounded_integer(max_chars, "max_chars", 1, 8000)
        data = self._get(ORIGIN + "/api/rest_v1/page/summary/" + quote(_title(title).replace(" ", "_"), safe=""))
        if not isinstance(data, dict):
            raise RetrievalError("Wikipedia returned an invalid summary response")
        if data.get("type") == "disambiguation":
            raise RetrievalError("Disambiguation page: use a more specific query")
        try:
            page_id = bounded_integer(data["pageid"], "page ID", 1, 2**63 - 1)
            canonical_title = _title(data["title"])
            extract = _text(data["extract"], "extract", multiline=True)
            if not extract.strip():
                raise ValueError("empty extract")
            revision = _revision(data.get("revision"))
        except (KeyError, TypeError, ValueError):
            raise RetrievalError("Wikipedia returned no usable summary") from None
        text = extract.strip()
        return Document(canonical_title, article_url(canonical_title), text[:max_chars], page_id, revision, len(text) > max_chars)

    def retrieve(self, query: str, limit: int = 3, max_chars: int = 4000) -> tuple[list[Document], list[Failure]]:
        bounded_integer(max_chars, "max_chars", 1, 8000)
        documents, failures, seen = [], [], set()
        # Sequential requests respect API etiquette and avoid surprise fan-out.
        for source in self.search(query, limit):
            try:
                document = self.summary(source.title, max_chars)
                if document.page_id not in seen:
                    documents.append(document)
                    seen.add(document.page_id)
            except RetrievalError as exc:
                failures.append(Failure(source.title, str(exc)))
        return documents, failures


def build_context(documents: list[Document], max_chars: int = 12000) -> str:
    """Budget the entire JSON string, including citation metadata and escaping.

    Text is untrusted evidence, not instructions. JSON escaping prevents a source
    from creating new structural fields; it does not solve prompt injection.
    """
    bounded_integer(max_chars, "context_chars", 256, 50000)
    if not isinstance(documents, list) or len(documents) > MAX_RESULTS:
        raise ValueError(f"at most {MAX_RESULTS} documents can form one context")
    for doc in documents:
        if not isinstance(doc, Document):
            raise ValueError("each context item must be a Document")
        title = _title(doc.title)
        if title_from_url(doc.url) != title:
            raise ValueError("context title and Wikipedia URL must identify the same article")
        if not _text(doc.text, "source text", multiline=True).strip():
            raise ValueError("each context document must have nonempty text")
        _revision(doc.revision)
        bounded_integer(doc.page_id, "page ID", 0, 2**63 - 1)
        if type(doc.truncated) is not bool:
            raise ValueError("truncated must be a boolean")
    sources = []

    def render(items):
        return json.dumps({"sources": items, "omitted": len(documents) - len(items)}, ensure_ascii=False, separators=(",", ":"))

    for doc in documents:
        item = {"id": f"S{len(sources) + 1}", "title": _title(doc.title), "url": article_url(doc.title),
                "revision": _revision(doc.revision), "text": doc.text, "truncated": doc.truncated}
        if len(render(sources + [item])) <= max_chars:
            sources.append(item)
            continue
        item["truncated"] = True
        low, high = 0, len(doc.text)
        while low < high:
            mid = (low + high + 1) // 2
            item["text"] = doc.text[:mid]
            if len(render(sources + [item])) <= max_chars:
                low = mid
            else:
                high = mid - 1
        item["text"] = doc.text[:low]
        if item["text"].strip() and len(render(sources + [item])) <= max_chars:
            sources.append(item)
        # Do not skip ahead to imply a lower-ranked page was a higher-ranked hit.
        break
    return render(sources)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Retrieve English Wikipedia summaries with source-linked context. No LLM or credentials.")
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=3, help="1 to 5 results (default: 3)")
    parser.add_argument("--context-chars", type=int, default=12000, help="Full JSON context budget, 256 to 50000 characters")
    args = parser.parse_args(argv)
    try:
        bounded_integer(args.context_chars, "context_chars", 256, 50000)
        documents, failures = WikiClient().retrieve(args.query, args.limit)
        context = json.loads(build_context(documents, args.context_chars))
        print(json.dumps({"query": args.query, "context": context,
                          "failures": [asdict(f) for f in failures]}, ensure_ascii=False, indent=2))
        return 0 if context["sources"] else 3
    except (ValueError, RetrievalError) as exc:
        print(f"Retrieval failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
