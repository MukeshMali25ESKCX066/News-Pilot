import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

import feedparser
import httpx
from bs4 import BeautifulSoup

from ..db import get_db_connection

KNOWN_FEED_PATHS = {
    "indianexpress.com": "/feed/",
    "hindustantimes.com": "/feeds/rss/india-news/rssfeed.xml",
    "timesofindia.indiatimes.com": "/rssfeeds/-2128936835.cms",
    "indiatoday.in": "/rss/home",
}


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_title(value: str) -> str:
    normalized = []
    for character in (value or "").casefold():
        category = unicodedata.category(character)
        if character.isspace() or category[0] in {"L", "M", "N"}:
            normalized.append(character)
        else:
            normalized.append(" ")
    return re.sub(r"\s+", " ", "".join(normalized)).strip()


def title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


def make_fingerprint(title: str, url: str) -> str:
    raw = f"{normalize_title(title)}|{url or ''}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def parse_feed_url(url: str):
    response = httpx.get(
        url,
        headers={"User-Agent": "NewsPilot AI RSS collector"},
        timeout=20,
        follow_redirects=True,
    )
    response.raise_for_status()
    parsed = feedparser.parse(response.content, response_headers=dict(response.headers))
    if parsed.version or parsed.entries:
        return parsed

    soup = BeautifulSoup(response.text, "html.parser")
    feed_urls = []
    for link in soup.find_all("link", href=True):
        relation = link.get("rel", [])
        if isinstance(relation, str):
            relation = relation.split()
        media_type = str(link.get("type", "")).lower()
        if "alternate" not in relation or not any(
            feed_type in media_type for feed_type in ("rss+xml", "atom+xml", "rdf+xml")
        ):
            continue
        feed_urls.append(urljoin(str(response.url), link["href"]))

    hostname = (urlsplit(str(response.url)).hostname or "").lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    known_feed_path = KNOWN_FEED_PATHS.get(hostname)
    if known_feed_path:
        feed_urls.append(urljoin(str(response.url), known_feed_path))

    if not feed_urls:
        raise ValueError(f"No RSS/Atom feed was advertised by {url}. Add its direct feed URL.")

    for feed_url in feed_urls:
        feed_response = httpx.get(
            feed_url,
            headers={"User-Agent": "NewsPilot AI RSS collector"},
            timeout=20,
            follow_redirects=True,
        )
        feed_response.raise_for_status()
        parsed = feedparser.parse(feed_response.content, response_headers=dict(feed_response.headers))
        if parsed.version or parsed.entries:
            return parsed
    raise ValueError(f"No readable RSS/Atom feed was found for {url}.")


def fetch_source_news(source: dict[str, Any]) -> list[dict[str, Any]]:
    parsed = parse_feed_url(source["url"])
    items: list[dict[str, Any]] = []
    for entry in getattr(parsed, "entries", []):
        title = entry.get("title") or "Untitled"
        url = entry.get("link") or ""
        published = parse_datetime(entry.get("updated") or entry.get("published"))
        summary = entry.get("summary") or entry.get("description") or ""
        items.append(
            {
                "source_id": source["id"],
                "title": title,
                "summary": summary,
                "url": url,
                "published_at": published.isoformat(timespec="seconds") if published else None,
                "payload": json.dumps(entry, default=str),
                "fingerprint": make_fingerprint(title, url),
            }
        )
    return items


def is_duplicate(title: str, url: str, existing_titles: list[str]) -> bool:
    for existing in existing_titles:
        if title_similarity(title, existing) >= 0.9:
            return True
    if url:
        for existing in existing_titles:
            if existing and url in existing:
                return True
    return False


def collect_news_for_all_sources() -> dict[str, Any]:
    total = 0
    errors = []
    with get_db_connection() as conn:
        sources = conn.execute("SELECT * FROM sources WHERE active = 1").fetchall()
        for source in sources:
            source_data = dict(source)
            try:
                fetched_items = fetch_source_news(source_data)
            except (httpx.HTTPError, ValueError) as exc:
                errors.append({"source": source_data["name"], "error": str(exc)})
                continue
            existing_titles = [
                row["title"] for row in conn.execute(
                    "SELECT title FROM news_items WHERE source_id = ?",
                    (source_data["id"],),
                ).fetchall()
            ]
            for item in fetched_items:
                if is_duplicate(item["title"], item["url"], existing_titles):
                    continue
                conn.execute(
                    """
                    INSERT INTO news_items (source_id, title, summary, url, published_at, fingerprint, raw_payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["source_id"],
                        item["title"],
                        item["summary"],
                        item["url"],
                        item["published_at"],
                        item["fingerprint"],
                        item["payload"],
                    ),
                )
                existing_titles.append(item["title"])
                total += 1
        conn.commit()
    return {"new_items": total, "errors": errors}
