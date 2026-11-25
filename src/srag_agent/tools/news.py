"""Notícias recentes sobre SRAG via RSS, tratadas como dado não confiável.

Guardrails: allowlist de hosts dos feeds, links só http(s), texto sem HTML e truncado, filtros de
relevância e recência e descarte (contado) de manchetes com padrão de injeção de prompt.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urlparse

import feedparser
import httpx


@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    topical: bool  # False: feed geral de saúde, só entram manchetes sobre doenças respiratórias


FEEDS: tuple[Feed, ...] = (
    Feed(
        "Google News",
        "https://news.google.com/rss/search?q=SRAG+OR+%22s%C3%ADndrome+respirat%C3%B3ria+aguda+grave%22"
        "&hl=pt-BR&gl=BR&ceid=BR:pt-419",
        topical=True,
    ),
    Feed("Agência Brasil", "https://agenciabrasil.ebc.com.br/rss/saude/feed.xml", topical=False),
)
# Um feed novo exige incluir o host aqui, de propósito.
ALLOWED_FEED_HOSTS = frozenset({"news.google.com", "agenciabrasil.ebc.com.br"})

MAX_AGE_DAYS = 30
MAX_ITEMS = 10
MIN_TITLE_CHARS = 20
MAX_TITLE_CHARS = 200
MAX_SUMMARY_CHARS = 300
REQUEST_TIMEOUT_SECONDS = 15.0

# Aplicados ao texto normalizado (minúsculo, sem acento). Formas imperativas, para não marcar
# manchetes legítimas como "hospitais ignoram alerta".
_INJECTION_PATTERNS = re.compile(
    "|".join(
        [
            r"\b(ignore|ignorar|esqueca|esquecer|desconsidere|disregard|forget)\b.{0,40}"
            r"\b(instruc\w*|regras?|rules?|prompt|tudo|acima|anteriores?|previous|prior|above)\b",
            r"\b(novas instrucoes|new instructions?)\b",
            r"\b(responda apenas|respond only|answer only)\b",
            r"\b(system|developer)\s*(prompt|message|mensagem)\b",
            r"\b(you are now|voce agora e|act as|aja como|finja ser|pretend to be)\b",
            r"<\s*/?\s*(system|assistant|user|instructions?)\b",
            r"\b(revele|reveal|mostre)\b.{0,40}\b(prompt|chave|key|senha|password)\b",
        ]
    )
)
# Checagem extra no texto sem espaços nem pontuação: pega "IG N O R E", "ignore.previous" etc.
_COMPACT_MARKERS = (
    "ignoreprevious",
    "ignoreallprevious",
    "ignoreasinstrucoes",
    "ignoretodasasinstrucoes",
    "esquecaasinstrucoes",
    "desconsidereasinstrucoes",
    "novasinstrucoes",
    "newinstructions",
    "systemprompt",
    "youarenow",
    "voceagorae",
)
_LETTER_SPACING = re.compile(r"(?<!\w)(?:\w[\s.\-_]){3,}\w(?!\w)")
_INVISIBLE = re.compile(r"[​-‏⁠-⁤﻿­]")
_RESPIRATORY = re.compile(
    r"\b(srag|sindrome respiratoria|sindromes respiratorias|influenza|gripe|covid|coronavirus|"
    r"virus respiratorio|virus respiratorios|sincicial|vsr|h1n1|h3n2|metapneumovirus)\b"
)


@dataclass(frozen=True)
class NewsItem:
    title: str
    source: str
    published: str  # AAAA-MM-DD
    link: str
    summary: str


@dataclass(frozen=True)
class FeedFailure:
    feed: str
    error: str
    status_code: int | None = None


@dataclass(frozen=True)
class NewsResult:
    items: list[NewsItem]
    failed_feeds: list[FeedFailure] = field(default_factory=list)
    dropped_suspicious: int = 0
    dropped_irrelevant: int = 0
    dropped_old: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    def format_for_prompt(self) -> str:
        if not self.items:
            return "Nenhuma notícia recente disponível."
        return "\n".join(
            f"[{index}] {item.title} ({item.source}, {item.published}): {item.summary}"
            for index, item in enumerate(self.items, start=1)
        )


class NewsProvider(Protocol):
    """Interface para trocar a fonte de notícias (ex.: Tavily) sem mexer no agente."""

    def fetch(self, client: httpx.Client, now: datetime) -> NewsResult: ...


class RssNewsProvider:
    def __init__(self, feeds: Sequence[Feed] = FEEDS) -> None:
        self.feeds = tuple(feeds)

    def fetch(self, client: httpx.Client, now: datetime) -> NewsResult:
        return fetch_news(client, now, self.feeds)


def fetch_news(client: httpx.Client, now: datetime, feeds: Sequence[Feed] = FEEDS) -> NewsResult:
    """Busca todos os feeds; a falha de um feed fica registrada e não descarta os outros."""
    candidates: list[NewsItem] = []
    failures: list[FeedFailure] = []
    counters = {"suspicious": 0, "irrelevant": 0, "old": 0}
    oldest = now - timedelta(days=MAX_AGE_DAYS)

    for feed in feeds:
        try:
            content = _download_feed(client, feed)
        except _FeedError as error:
            failures.append(FeedFailure(feed.name, error.message, error.status_code))
            continue
        for entry in feedparser.parse(content).entries:
            item, reason = _to_item(entry, feed, oldest)
            if item is None:
                counters[reason] += 1
            else:
                candidates.append(item)

    return NewsResult(
        items=_deduplicate(candidates)[:MAX_ITEMS],
        failed_feeds=failures,
        dropped_suspicious=counters["suspicious"],
        dropped_irrelevant=counters["irrelevant"],
        dropped_old=counters["old"],
    )


def normalize(text: str) -> str:
    """Minúsculo, sem acentos, sem caracteres invisíveis e sem letras espaçadas ("i g n o r e")."""
    text = _INVISIBLE.sub("", unicodedata.normalize("NFKC", text))
    text = _LETTER_SPACING.sub(lambda match: re.sub(r"[\s.\-_]", "", match.group()), text)
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_accents.lower().split())


def looks_like_injection(text: str) -> bool:
    normalized = normalize(text)
    compact = re.sub(r"[\W_]", "", normalized)
    return _INJECTION_PATTERNS.search(normalized) is not None or any(
        marker in compact for marker in _COMPACT_MARKERS
    )


def is_respiratory(text: str) -> bool:
    return _RESPIRATORY.search(normalize(text)) is not None


def clean_text(text: str, limit: int) -> str:
    """Remove HTML, junta espaços e trunca."""
    parser = _TextExtractor()
    parser.feed(text or "")
    cleaned = " ".join(" ".join(parser.parts).split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


class _FeedError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _download_feed(client: httpx.Client, feed: Feed) -> bytes:
    parsed = urlparse(feed.url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_FEED_HOSTS:
        raise _FeedError("host fora da allowlist")
    try:
        response = client.get(feed.url, follow_redirects=False, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        raise _FeedError("HTTPStatusError", error.response.status_code) from error
    except httpx.HTTPError as error:
        raise _FeedError(type(error).__name__) from error
    return response.content


def _to_item(entry, feed: Feed, oldest: datetime) -> tuple[NewsItem | None, str]:
    source = clean_text(entry.get("source", {}).get("title") or feed.name, 80)
    title = _strip_source_suffix(clean_text(entry.get("title", ""), MAX_TITLE_CHARS), source)
    summary = clean_text(entry.get("summary", ""), MAX_SUMMARY_CHARS)
    link = entry.get("link", "")
    published = entry.get("published_parsed") or entry.get("updated_parsed")

    if not title or looks_like_injection(f"{title} {summary} {source}"):
        return None, "suspicious"
    if urlparse(link).scheme not in ("http", "https"):
        return None, "suspicious"
    if published is None or datetime(*published[:6], tzinfo=UTC) < oldest:
        return None, "old"
    if len(title.strip(" -")) < MIN_TITLE_CHARS or normalize(title.strip(" -")) == normalize(source):
        return None, "irrelevant"  # título vazio ou só o nome do veículo
    if not feed.topical and not is_respiratory(f"{title} {summary}"):
        return None, "irrelevant"

    if summary == title or summary.startswith(title):
        summary = ""  # o Google News repete o título no resumo
    published_on = datetime(*published[:6], tzinfo=UTC).date().isoformat()
    return NewsItem(title, source, published_on, link, summary), ""


def _strip_source_suffix(title: str, source: str) -> str:
    """O Google News publica 'Manchete - Veículo': o veículo já vai em `source`."""
    suffix = f" - {source}"
    return title[: -len(suffix)] if source and title.endswith(suffix) else title


def _deduplicate(items: list[NewsItem]) -> list[NewsItem]:
    unique: dict[str, NewsItem] = {}
    for item in sorted(items, key=lambda item: item.published, reverse=True):
        unique.setdefault(normalize(item.title), item)
    return list(unique.values())


class _TextExtractor(HTMLParser):
    _SKIPPED_TAGS = frozenset({"script", "style"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIPPED_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIPPED_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)
