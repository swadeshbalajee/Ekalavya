"""Readable text of a web page (blog post, article, documentation) as source chunks.

Finds the page's main content the way reader views do: the container holding
the most paragraph text wins, and navigation, sidebars, comments and scripts
are dropped. The result has the same shape as a parsed PDF so it is indexed,
retrieved and cited exactly like an uploaded paper.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, NavigableString, Tag

from scholarmotion.retrieval.paper_ingestion import ParsedPaper, ParsedPaperChunk, clean_text

# Elements that never hold an article's text.
_DROP_TAGS = (
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "iframe",
    "form",
    "button",
    "nav",
    "header",
    "footer",
    "aside",
)
# Page furniture marked by class or id (comments, share bars, sidebars, menus).
# Only small elements inside the chosen article are removed this way, because
# some platforms put the same words on the article's own wrappers.
_FURNITURE = re.compile(
    r"comment|sidebar|share|social|related|menu|breadcrumb|cookie|banner|"
    r"subscribe|newsletter|popup|advert|footer|navbar",
    re.IGNORECASE,
)
_FURNITURE_MAX_SHARE = 0.3
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_BLOCKS = _HEADINGS | {
    "p",
    "li",
    "blockquote",
    "pre",
    "div",
    "section",
    "article",
    "main",
    "ul",
    "ol",
    "table",
    "tr",
    "td",
    "th",
    "figure",
    "figcaption",
    "dl",
    "dt",
    "dd",
}
MIN_PAGE_TEXT = 200
_CHUNK_TARGET = 900


def _strip_furniture(container: Tag) -> None:
    total = len(container.get_text(" ", strip=True)) or 1
    for tag in container.find_all(True):
        if tag.decomposed:
            continue
        marker = " ".join([*(tag.get("class") or []), str(tag.get("id") or "")])
        if (
            marker.strip()
            and _FURNITURE.search(marker)
            and len(tag.get_text(" ", strip=True)) / total < _FURNITURE_MAX_SHARE
        ):
            tag.decompose()


def _paragraph_text(node: Tag) -> int:
    return sum(
        len(paragraph.get_text(" ", strip=True))
        for paragraph in node.find_all(["p", "pre", "blockquote"])
    )


def _main_container(soup: BeautifulSoup) -> Tag:
    """The element whose paragraphs hold the most text (readability scoring)."""
    scores: dict[int, tuple[float, Tag]] = {}
    for paragraph in soup.find_all(["p", "pre", "blockquote"]):
        length = len(paragraph.get_text(" ", strip=True))
        if length < 25:
            continue
        parent = paragraph.parent
        for weight, ancestor in ((1.0, parent), (0.5, getattr(parent, "parent", None))):
            if isinstance(ancestor, Tag):
                score, _ = scores.get(id(ancestor), (0.0, ancestor))
                scores[id(ancestor)] = (score + weight * length, ancestor)
    if not scores:
        # Pages built from bare <div> lines: the article/main element, else the body.
        return soup.find("article") or soup.find("main") or soup.body or soup
    best = max(scores.values(), key=lambda item: item[0])[1]
    # An article split into sections scores one section highest; widen to the
    # element that holds its sibling sections too.
    text = _paragraph_text(best)
    while isinstance(best.parent, Tag) and best.parent.name not in {"body", "html"}:
        wider = _paragraph_text(best.parent)
        if wider < 1.5 * text:
            break
        best, text = best.parent, wider
    return best


def _blocks(node: Tag):
    """Yield (kind, text) for each heading and text block, in reading order."""
    inline: list[str] = []

    def flush():
        text = " ".join(" ".join(inline).split())
        inline.clear()
        return text

    for child in node.children:
        if isinstance(child, NavigableString):
            inline.append(str(child))
            continue
        if not isinstance(child, Tag):
            continue
        if child.name == "br":
            if text := flush():
                yield "text", text
            continue
        if child.name not in _BLOCKS:
            inline.append(child.get_text(" "))
            continue
        if text := flush():
            yield "text", text
        if child.name in _HEADINGS:
            if heading := " ".join(child.get_text(" ").split()):
                yield "heading", heading
        elif child.name in {"p", "pre"} or not child.find(list(_BLOCKS)):
            if text := " ".join(child.get_text(" ").split()):
                yield "text", text
        else:
            yield from _blocks(child)
    if text := flush():
        yield "text", text


def _title(soup: BeautifulSoup, url: str) -> str:
    meta = soup.find("meta", attrs={"property": "og:title"})
    for value in (
        meta.get("content") if meta else None,
        soup.title.get_text(" ") if soup.title else None,
        soup.h1.get_text(" ") if soup.h1 else None,
    ):
        if value and value.strip():
            return " ".join(clean_text(value).split())[:300]
    return urlsplit(url).hostname or url


def parse_web_page(html: str, url: str) -> ParsedPaper:
    """Main text of a page, chunked by paragraph under its section headings.

    Raises ValueError when the page has no readable article text, for example
    one that is rendered by JavaScript or sits behind a login.
    """
    soup = BeautifulSoup(html, "html.parser")
    title = _title(soup, url)
    for tag in soup.find_all(_DROP_TAGS):
        tag.decompose()
    container = _main_container(soup)
    _strip_furniture(container)
    chunks: list[ParsedPaperChunk] = []
    section: str | None = None
    pending = ""
    for kind, raw in _blocks(container):
        text = clean_text(raw).strip()
        if not text:
            continue
        if kind == "heading":
            if pending:
                chunks.append(ParsedPaperChunk(1, section, "paragraph", pending))
                pending = ""
            section = text[:200]
            chunks.append(ParsedPaperChunk(1, section, "section", section))
            continue
        # Short lines (one-sentence paragraphs, list items) join their neighbours.
        pending = f"{pending} {text}".strip() if pending else text
        if len(pending) >= _CHUNK_TARGET or (len(pending) >= 120 and pending[-1] in ".!?\"'”"):
            chunks.append(ParsedPaperChunk(1, section, "paragraph", pending))
            pending = ""
    if pending:
        chunks.append(ParsedPaperChunk(1, section, "paragraph", pending))
    body = sum(len(chunk.text) for chunk in chunks if chunk.content_type == "paragraph")
    if body < MIN_PAGE_TEXT:
        raise ValueError(
            "the page has no readable article text (it may need JavaScript or a login)"
        )
    abstract = next((c.text for c in chunks if c.content_type == "paragraph"), None)
    return ParsedPaper(title, [], abstract, chunks, {"pages": 1, "source_url": url})
