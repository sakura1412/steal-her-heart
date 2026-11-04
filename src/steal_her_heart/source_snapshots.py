"""Local, immutable source snapshots. Captured text is untrusted, not Evidence.

Text lines are a reproducible view of static source bytes, not a rendered browser
DOM or a factual interpretation. No scripts, network requests or models run here.
"""

import codecs
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Annotated, Literal, Optional
from uuid import uuid4

from pydantic import Field, HttpUrl, ValidationError, model_validator

from .schemas import Contract, Source, Text

MAX_BYTES = 1048576
MediaType = Literal["text/html", "application/xhtml+xml", "text/plain"]
Digest = Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{64}$")]
_MESSAGES = {
    "FETCH_SNAPSHOT_INVALID": "Snapshot arguments or reference are invalid.",
    "FETCH_SNAPSHOT_UNAVAILABLE": "Source snapshot could not be stored or read safely.",
    "FETCH_SNAPSHOT_CORRUPT": "Source snapshot failed its integrity checks.",
    "FETCH_UNSUPPORTED_ENCODING": "The source encoding is unsupported or the text is invalid.",
    "FETCH_TEXT_ERROR": "Static source text exceeded the parser's structural limits.",
    "FETCH_NO_TEXT": "The source contains no usable static body text.",
    "FETCH_ACCESS_RESTRICTED": "The source requires verification; collection stopped.",
    "FETCH_RESPONSE_TOO_LARGE": "Source bytes or normalized text exceeded the size limit.",
}


class SnapshotError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code if code in _MESSAGES else "FETCH_SNAPSHOT_INVALID"
        self.message = _MESSAGES[self.code]
        super().__init__(self.message)


class SnapshotDocument(Contract):
    source: Source
    title: Optional[Text] = None
    media_type: MediaType
    charset: Annotated[str, Field(strict=True, min_length=1, max_length=80)]
    text_sha256: Digest
    line_count: Annotated[int, Field(strict=True, ge=1, le=MAX_BYTES)]
    parser_version: Literal["1"] = "1"


class _Manifest(Contract):
    schema_version: Literal[1] = 1
    parser_version: Literal["1"] = "1"
    url: HttpUrl
    source_name: Text
    fetched_at: datetime
    title: Optional[Text] = None
    media_type: MediaType
    charset: Annotated[str, Field(strict=True, min_length=1, max_length=80)]
    content_sha256: Digest
    text_sha256: Digest
    line_count: Annotated[int, Field(strict=True, ge=1, le=MAX_BYTES)]

    @model_validator(mode="after")
    def check_metadata(self) -> "_Manifest":
        if self.fetched_at.tzinfo is None or self.fetched_at.utcoffset() is None:
            raise ValueError("Acquisition time must have a timezone")
        if (
            self.url.username is not None
            or self.url.password is not None
            or not self.url.host
            or self.source_name != self.url.host
            or len(str(self.url)) > 2048
        ):
            raise ValueError("Invalid source metadata")
        return self


@dataclass(frozen=True)
class StoredSnapshot:
    document: SnapshotDocument
    raw: bytes
    text: str


_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_BLOCK = frozenset(
    "address article aside blockquote body br dd div dl dt fieldset figcaption figure "
    "footer form h1 h2 h3 h4 h5 h6 header hr li main nav ol p pre section table tbody "
    "td th thead tr ul".split()
)
_HIDDEN = frozenset({"head", "title", "script", "style", "template", "noscript"})


def _line_text(value: str) -> str:
    return " ".join(value.split())


def _lines(value: str) -> str:
    return "\n".join(line for part in value.splitlines() if (line := _line_text(part))) + "\n"


class _StaticText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []
        self.chunks: list[str] = []
        self.titles: list[str] = []
        self.nodes = 0
        self.restricted = False

    def count(self) -> None:
        self.nodes += 1
        if self.nodes > 50000 or len(self.stack) > 256:
            raise SnapshotError("FETCH_TEXT_ERROR")

    def handle_starttag(self, tag: str, attrs) -> None:
        self.count()
        values = {}
        for key, value in attrs:
            values.setdefault(key, value or "")
        tokens = (values.get("id", "") + " " + values.get("class", "")).lower().split()
        if (
            any(
                token in {"challenge-form", "g-recaptcha", "h-captcha", "cf-turnstile"}
                or token.startswith(("cf-chl", "cf-challenge", "anomaly-modal"))
                for token in tokens
            )
            or (tag == "form" and "/anomaly.js" in values.get("action", "").lower())
            or "/cdn-cgi/challenge-platform/" in values.get("src", "").lower()
        ):
            self.restricted = True
        hidden = (
            (self.stack[-1][1] if self.stack else False)
            or tag in _HIDDEN
            or "hidden" in values
            or values.get("aria-hidden", "").strip().lower() == "true"
            or bool(
                re.search(
                    r"(?:display\s*:\s*none|visibility\s*:\s*hidden)\b",
                    values.get("style", "").lower(),
                )
            )
        )
        if tag in _BLOCK and not hidden:
            self.chunks.append("\n")
        if tag not in _VOID:
            self.stack.append((tag, hidden))
            self.count()

    def handle_endtag(self, tag: str) -> None:
        self.count()
        for position in range(len(self.stack) - 1, -1, -1):
            if self.stack[position][0] == tag:
                hidden = self.stack[position][1]
                del self.stack[position:]
                if tag in _BLOCK and not hidden:
                    self.chunks.append("\n")
                break

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        # HTML raw-text elements do not become safe just because a malformed
        # source uses a self-closing slash. Conservatively suppress their body.
        if tag not in _VOID and tag not in {"script", "style"}:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        self.count()
        if any(tag == "title" for tag, _ in self.stack):
            self.titles.append(data)
        if not self.stack or not self.stack[-1][1]:
            self.chunks.append(data)


def _charset(value: str) -> str:
    try:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value):
            raise ValueError("Invalid charset")
        codec = codecs.lookup(value)
        if not getattr(codec, "_is_text_encoding", False) or codec.name in {
            "unicode-escape",
            "raw-unicode-escape",
            "idna",
            "punycode",
            "utf-7",
        }:
            raise ValueError("Not a supported text codec")
        return codec.name
    except (LookupError, ValueError, TypeError):
        raise SnapshotError("FETCH_UNSUPPORTED_ENCODING") from None


def _normalize(body: bytes, media_type: str, charset: str) -> tuple[str, Optional[str]]:
    if type(body) is not bytes:
        raise SnapshotError("FETCH_SNAPSHOT_INVALID")
    if len(body) > MAX_BYTES:
        raise SnapshotError("FETCH_RESPONSE_TOO_LARGE")
    try:
        decoded = body.decode(charset, errors="strict").removeprefix("\ufeff")
    except (UnicodeError, LookupError, TypeError):
        raise SnapshotError("FETCH_UNSUPPORTED_ENCODING") from None
    if "\x00" in decoded or any(ord(char) < 32 and char not in "\t\r\n" for char in decoded):
        raise SnapshotError("FETCH_TEXT_ERROR")
    title = None
    if media_type in {"text/html", "application/xhtml+xml"}:
        parser = _StaticText()
        try:
            parser.feed(decoded)
            parser.close()
        except SnapshotError:
            raise
        except Exception:
            raise SnapshotError("FETCH_TEXT_ERROR") from None
        if parser.restricted:
            raise SnapshotError("FETCH_ACCESS_RESTRICTED")
        title = _line_text("".join(parser.titles)) or None
        if title is not None and len(title) > 4000:
            raise SnapshotError("FETCH_TEXT_ERROR")
        decoded = "".join(parser.chunks)
    elif media_type != "text/plain":
        raise SnapshotError("FETCH_SNAPSHOT_INVALID")
    text = _lines(decoded)
    if text == "\n":
        raise SnapshotError("FETCH_NO_TEXT")
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise SnapshotError("FETCH_RESPONSE_TOO_LARGE")
    return text, title


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _manifest_bytes(manifest: _Manifest) -> bytes:
    return (
        json.dumps(
            manifest.model_dump(mode="json"),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _document(manifest: _Manifest, digest: str) -> SnapshotDocument:
    return SnapshotDocument(
        source=Source(
            id="source-" + digest,
            source_name=manifest.source_name,
            url=manifest.url,
            published_at=None,
            fetched_at=manifest.fetched_at,
            content_sha256=manifest.content_sha256,
            snapshot_ref="sha256:" + digest,
        ),
        title=manifest.title,
        media_type=manifest.media_type,
        charset=manifest.charset,
        text_sha256=manifest.text_sha256,
        line_count=manifest.line_count,
    )


class SnapshotStore:
    """A configured local root; public references contain no filesystem paths.

    Directory file descriptors and O_NOFOLLOW keep snapshot children within the
    root. Complete directories are committed by rename, never exposed half-built.
    """

    def __init__(self, root: Path) -> None:
        path = Path(root).absolute()
        self.root = path.parent.resolve() / path.name

    @staticmethod
    def check_support() -> None:
        """Fail safely before collection on platforms without safe local APIs."""
        required_flags = ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")
        required_dir_fd = ("open", "mkdir", "stat", "rename", "unlink", "rmdir")
        supported = getattr(os, "supports_dir_fd", ())
        if (
            not all(hasattr(os, name) for name in required_flags)
            or not all(getattr(os, name, None) in supported for name in required_dir_fd)
            or getattr(os, "stat", None) not in getattr(os, "supports_follow_symlinks", ())
        ):
            raise SnapshotError("FETCH_SNAPSHOT_UNAVAILABLE")

    def _root_fd(self, *, create: bool) -> int:
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
        return os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

    @staticmethod
    def _read(directory: int, name: str, limit: int) -> bytes:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise SnapshotError("FETCH_SNAPSHOT_CORRUPT")
            content = stream.read(limit + 1)
        if len(content) > limit:
            raise SnapshotError("FETCH_SNAPSHOT_CORRUPT")
        return content

    def _load(self, root_fd: int, digest: str) -> StoredSnapshot:
        directory = os.open(digest, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        try:
            payload = self._read(directory, "manifest.json", 65536)
            raw = self._read(directory, "raw.bin", MAX_BYTES)
            text_bytes = self._read(directory, "text.txt", MAX_BYTES)
        finally:
            os.close(directory)
        try:
            if _sha256(payload) != digest:
                raise ValueError("Manifest digest mismatch")
            manifest = _Manifest.model_validate_json(payload)
            if (
                payload != _manifest_bytes(manifest)
                or _charset(manifest.charset) != manifest.charset
            ):
                raise ValueError("Noncanonical manifest")
            text = text_bytes.decode("utf-8", errors="strict")
            if (
                _sha256(raw) != manifest.content_sha256
                or _sha256(text_bytes) != manifest.text_sha256
                or len(text.splitlines()) != manifest.line_count
            ):
                raise ValueError("Content mismatch")
            normalized, title = _normalize(raw, manifest.media_type, manifest.charset)
            if normalized != text or title != manifest.title:
                raise ValueError("Text cannot be reconstructed from raw bytes")
            return StoredSnapshot(document=_document(manifest, digest), raw=raw, text=text)
        except (ValueError, TypeError, ValidationError, SnapshotError):
            raise SnapshotError("FETCH_SNAPSHOT_CORRUPT") from None

    def load(self, snapshot_ref: str) -> StoredSnapshot:
        self.check_support()
        if not isinstance(snapshot_ref, str) or not re.fullmatch(
            r"sha256:[a-f0-9]{64}", snapshot_ref
        ):
            raise SnapshotError("FETCH_SNAPSHOT_INVALID")
        fd = None
        try:
            fd = self._root_fd(create=False)
            return self._load(fd, snapshot_ref[7:])
        except OSError:
            raise SnapshotError("FETCH_SNAPSHOT_UNAVAILABLE") from None
        finally:
            if fd is not None:
                os.close(fd)

    def save(
        self, *, url: str, media_type: str, charset: str, body: bytes, fetched_at: datetime
    ) -> SnapshotDocument:
        self.check_support()
        try:
            if (
                not isinstance(url, str)
                or len(url) > 2048
                or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in url)
            ):
                raise ValueError("Invalid URL")
            encoding = _charset(charset)
            text, title = _normalize(body, media_type, encoding)
            manifest = _Manifest(
                url=url,
                source_name=HttpUrl(url).host,
                fetched_at=fetched_at,
                title=title,
                media_type=media_type,
                charset=encoding,
                content_sha256=_sha256(body),
                text_sha256=_sha256(text.encode("utf-8")),
                line_count=len(text.splitlines()),
            )
        except (ValueError, TypeError, ValidationError):
            raise SnapshotError("FETCH_SNAPSHOT_INVALID") from None
        payload = _manifest_bytes(manifest)
        digest, staging = _sha256(payload), ".tmp-" + uuid4().hex
        root_fd = directory = None
        created = False
        try:
            root_fd = self._root_fd(create=True)
            try:
                os.stat(digest, dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                return self._load(root_fd, digest).document
            os.mkdir(staging, mode=0o700, dir_fd=root_fd)
            created = True
            directory = os.open(
                staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd
            )
            for name, content in (
                ("raw.bin", body),
                ("text.txt", text.encode("utf-8")),
                ("manifest.json", payload),
            ):
                fd = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory,
                )
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
            os.fsync(directory)
            os.close(directory)
            directory = None
            try:
                os.rename(staging, digest, src_dir_fd=root_fd, dst_dir_fd=root_fd)
            except OSError:
                # A concurrent identical save can win the immutable commit.
                return self._load(root_fd, digest).document
            created = False
            os.fsync(root_fd)
            return _document(manifest, digest)
        except OSError:
            raise SnapshotError("FETCH_SNAPSHOT_UNAVAILABLE") from None
        finally:
            if directory is not None:
                os.close(directory)
            if created and root_fd is not None:
                try:
                    directory = os.open(
                        staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd
                    )
                    try:
                        for name in ("raw.bin", "text.txt", "manifest.json"):
                            try:
                                os.unlink(name, dir_fd=directory)
                            except FileNotFoundError:
                                pass
                    finally:
                        os.close(directory)
                    os.rmdir(staging, dir_fd=root_fd)
                except OSError:
                    pass
            if root_fd is not None:
                os.close(root_fd)
