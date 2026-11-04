"""Bounded, offline parsing of DuckDuckGo HTML discovery leads, never Evidence."""

import hashlib
import ipaddress
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Annotated, Optional, Union
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from pydantic import Field, HttpUrl, ValidationError

from .schemas import Contract, Identifier, Text

MAX_HTML_BYTES = 1048576
_VOID = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_PROVIDER_HOSTS = frozenset({"duckduckgo.com", "www.duckduckgo.com", "html.duckduckgo.com"})
_LOCAL_SUFFIXES = (
    ".localhost",
    ".local",
    ".localdomain",
    ".internal",
    ".intranet",
    ".lan",
    ".home",
    ".test",
    ".invalid",
    ".onion",
)


class SearchHit(Contract):
    """Unverified candidate URL and search snippet, not a fetched page or fact."""

    id: Identifier
    rank: Annotated[int, Field(strict=True, ge=1)]
    title: Text
    url: HttpUrl
    snippet: Annotated[str, Field(strict=True, max_length=4000)] = ""


class SearchPageError(Exception):
    """Safe, application-owned error text; never echo the provider HTML."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _public_ip(address: Union[ipaddress.IPv4Address, ipaddress.IPv6Address]) -> bool:
    if not address.is_global:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped and not address.ipv4_mapped.is_global:
            return False
        if address.sixtofour and not address.sixtofour.is_global:
            return False
        if address.teredo and any(not part.is_global for part in address.teredo):
            return False
        if address in ipaddress.IPv6Network("64:ff9b::/96"):
            return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF).is_global
    return True


def _candidate_url(href: str) -> Optional[HttpUrl]:
    if not href or len(href) > 8192 or any(ord(char) < 32 or ord(char) == 127 for char in href):
        return None
    try:
        absolute = urljoin("https://html.duckduckgo.com/html/", href.strip())
        parsed = urlsplit(absolute)
        host = (parsed.hostname or "").lower().rstrip(".")
        if host in _PROVIDER_HOSTS and parsed.path in {"/l", "/l/"}:
            targets = parse_qs(parsed.query, max_num_fields=50).get("uddg", [])
            if len(targets) != 1:
                return None
            absolute = targets[0]
            parsed = urlsplit(absolute)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 80, 443}
            or not parsed.hostname
            or any(ord(char) < 32 or ord(char) == 127 for char in absolute)
        ):
            return None
        # Normalize WHATWG IPv4 spellings and IDNA before checking destinations.
        value = HttpUrl(urlunsplit(parsed._replace(fragment="")))
        normalized = urlsplit(str(value))
        host = (normalized.hostname or "").lower().rstrip(".")
        if host in _PROVIDER_HOSTS and (
            normalized.path in {"/html", "/html/", "/lite", "/lite/", "/l", "/l/", "/y.js"}
            or (normalized.path == "/" and "q" in parse_qs(normalized.query, max_num_fields=50))
        ):
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host or host.endswith(_LOCAL_SUFFIXES) or host == "localhost":
                return None
        else:
            if not _public_ip(address):
                return None
        return value
    except (ValueError, UnicodeError, ValidationError):
        return None


@dataclass
class _Candidate:
    href: str = ""
    title: list[str] = field(default_factory=list)
    snippet: list[str] = field(default_factory=list)
    title_started: bool = False
    title_closed: bool = False
    snippet_started: bool = False
    snippet_closed: bool = False


@dataclass
class _Frame:
    tag: str
    candidate: Optional[_Candidate]
    suppressed: bool
    capture: Optional[str] = None


class _ResultsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.frames: list[_Frame] = []
        self.candidates: list[_Candidate] = []
        self.challenge = False
        self.no_results = False
        self.result_anchors = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attributes = {}
        for key, value in attrs:
            # HTML keeps the first duplicate attribute, not the final one.
            attributes.setdefault(key.lower(), value or "")
        classes = set(attributes.get("class", "").split())
        element_id = attributes.get("id", "").lower()
        # Structural signals only: a legitimate result about CAPTCHA is still a lead.
        action = attributes.get("action", "").lower()
        script = attributes.get("src", "").lower() if tag == "script" else ""
        if (
            element_id in {"challenge-form", "anomaly-form", "captcha-form"}
            or element_id.startswith("anomaly-")
            or any(value.startswith("anomaly-modal") for value in classes)
            or (
                tag == "form"
                and any(part in action for part in ("/anomaly.js", "/captcha", "/challenge"))
            )
            or "/anomaly.js" in script
            or "g-recaptcha" in classes
        ):
            self.challenge = True
        parent = self.frames[-1] if self.frames else None
        suppressed = bool(parent and parent.suppressed) or tag in {"script", "style", "template"}
        suppressed = suppressed or "hidden" in attributes or attributes.get("aria-hidden") == "true"
        ad = any(value.startswith("result--ad") for value in classes)
        suppressed = suppressed or ad or "result--sponsored" in classes
        candidate = parent.candidate if parent else None
        if not suppressed and (
            element_id == "no-results"
            or any(value == "no-results" or value.startswith("no-results__") for value in classes)
        ):
            self.no_results = True
        if not suppressed and "result" in classes:
            candidate = _Candidate()
            self.candidates.append(candidate)
        capture = None
        if tag == "a" and "result__a" in classes:
            if not suppressed:
                self.result_anchors += 1
                if candidate is None:
                    candidate = _Candidate()
                    self.candidates.append(candidate)
                if not candidate.title_started:
                    candidate.title_started = True
                    candidate.href = attributes.get("href", "")
                    capture = "title"
        if not suppressed and candidate and "result__snippet" in classes:
            if not candidate.snippet_started:
                candidate.snippet_started = True
                capture = "snippet"
        if len(self.frames) >= 1024 or len(self.candidates) > 500:
            raise SearchPageError("SEARCH_FORMAT_ERROR", "Search HTML structure exceeds limits")
        if tag == "br":
            self.handle_data(" ")
        if tag not in _VOID:
            self.frames.append(_Frame(tag, candidate, suppressed, capture))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.frames) - 1, -1, -1):
            frame = self.frames[index]
            if frame.tag == tag:
                if frame.candidate and frame.capture:
                    setattr(frame.candidate, frame.capture + "_closed", True)
                del self.frames[index:]
                return

    def handle_data(self, data: str) -> None:
        if self.frames and self.frames[-1].suppressed:
            return
        for frame in reversed(self.frames):
            if frame.capture and frame.candidate:
                getattr(frame.candidate, frame.capture).append(data)
                return


def parse_duckduckgo_html(body: bytes, max_results: int) -> tuple[SearchHit, ...]:
    """Return bounded organic leads; only an explicit no-results page yields ()."""
    if type(max_results) is not int or not 1 <= max_results <= 10:
        raise ValueError("max_results must be an integer from 1 to 10")
    if not isinstance(body, bytes) or not body or len(body) > MAX_HTML_BYTES:
        raise SearchPageError("SEARCH_FORMAT_ERROR", "Search HTML is empty or exceeds limits")
    parser = _ResultsParser()
    try:
        parser.feed(body.decode("utf-8", "replace"))
        parser.close()
    except SearchPageError:
        raise
    except Exception:
        raise SearchPageError("SEARCH_FORMAT_ERROR", "Search HTML could not be parsed") from None
    if parser.challenge:
        raise SearchPageError(
            "SEARCH_ACCESS_RESTRICTED", "Search provider requires human verification"
        )
    if parser.no_results:
        if parser.result_anchors:
            raise SearchPageError(
                "SEARCH_FORMAT_ERROR", "Search HTML contains contradictory result markers"
            )
        return ()
    hits = []
    seen = set()
    for candidate in parser.candidates:
        title = " ".join("".join(candidate.title).split())[:4000]
        url = _candidate_url(candidate.href)
        if not candidate.title_closed or not title or url is None or str(url) in seen:
            continue
        seen.add(str(url))
        snippet = (
            " ".join("".join(candidate.snippet).split())[:4000] if candidate.snippet_closed else ""
        )
        hits.append(
            SearchHit(
                id="search-" + hashlib.sha256(str(url).encode()).hexdigest()[:24],
                rank=len(hits) + 1,
                title=title,
                url=url,
                snippet=snippet,
            )
        )
        if len(hits) >= max_results:
            break
    if not hits:
        code = "SEARCH_NO_USABLE_RESULTS" if parser.result_anchors else "SEARCH_FORMAT_ERROR"
        raise SearchPageError(code, "Search HTML did not contain usable organic results")
    return tuple(hits)
