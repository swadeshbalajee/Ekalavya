"""SSRF-safe download of public HTTPS links (PDF or HTML)."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlsplit

import httpx

MAX_PAPER_BYTES = 100 * 1024 * 1024


def _validate_public_https_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("the link must be an HTTPS URL to a public host")
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("the link must use a public host")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            records = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
        except OSError as error:
            raise ValueError("the link's host could not be found") from error
        addresses = [ipaddress.ip_address(record[4][0]) for record in records]
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("the link must resolve only to public IP addresses")


def pdf_link(url: str) -> str:
    """Map a paper's landing page to its PDF where the pattern is known (arXiv)."""
    url = url.strip()
    match = re.match(
        r"^(?:https?://)?(?:www\.)?(?:export\.)?arxiv\.org/(?:abs|pdf)/([^?#\s]+?)(?:\.pdf)?/?(?:[?#].*)?$",
        url,
        re.IGNORECASE,
    )
    if match:
        return f"https://arxiv.org/pdf/{match.group(1)}"
    return url


MAX_PAGE_BYTES = 10 * 1024 * 1024
HTML_TYPES = {"text/html", "application/xhtml+xml"}


@dataclass(frozen=True)
class FetchedLink:
    """A downloaded source: a PDF document or an HTML page."""

    kind: str  # "pdf" or "html"
    data: bytes
    filename: str
    url: str  # after redirects

    def text(self) -> str:
        return self.data.decode(self.encoding, errors="replace")

    encoding: str = "utf-8"


async def fetch_link(url: str, *, max_bytes: int = MAX_PAPER_BYTES) -> FetchedLink:
    """Fetch a public HTTPS link that serves a PDF or a web page.

    Every hop of a redirect is re-validated so a link can never reach a local
    or private address, and downloads stop at a size limit.
    """
    current = pdf_link(url)
    headers = {
        "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.5",
        "User-Agent": "Mozilla/5.0 (compatible; Ekalavya source reader)",
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(30), follow_redirects=False) as client:
        for _ in range(5):
            await asyncio.to_thread(_validate_public_https_url, current)
            async with client.stream("GET", current, headers=headers) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("the server returned an invalid redirect")
                    current = urljoin(current, location)
                    continue
                if response.status_code in {401, 402, 403}:
                    raise ValueError("the page needs a login or subscription")
                if response.status_code == 404:
                    raise ValueError("the page was not found (404)")
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                limit = max_bytes if content_type not in HTML_TYPES else MAX_PAGE_BYTES
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise ValueError("the link is larger than the download limit")
                    chunks.append(chunk)
                data = b"".join(chunks)
                path_name = unquote(urlsplit(current).path.rstrip("/").rsplit("/", 1)[-1])
                if data.startswith(b"%PDF-"):
                    name = path_name or "paper.pdf"
                    if not name.lower().endswith(".pdf"):
                        name += ".pdf"
                    return FetchedLink("pdf", data, name[:180], current)
                if content_type in HTML_TYPES or data.lstrip()[:15].lower().startswith(
                    (b"<!doctype html", b"<html")
                ):
                    return FetchedLink(
                        "html",
                        data,
                        (path_name or urlsplit(current).hostname or "page")[:170] + ".html",
                        current,
                        response.encoding or "utf-8",
                    )
                raise ValueError(f"the link is neither a PDF nor a web page ({content_type})")
    raise ValueError("the link redirected too many times")


async def fetch_paper_pdf(url: str, *, max_bytes: int = MAX_PAPER_BYTES) -> tuple[bytes, str]:
    """Fetch a public PDF link while preventing local-network access and oversized downloads."""
    fetched = await fetch_link(url, max_bytes=max_bytes)
    if fetched.kind != "pdf":
        raise ValueError("link did not return a PDF document")
    return fetched.data, fetched.filename
