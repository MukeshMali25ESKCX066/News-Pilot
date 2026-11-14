import json
from collections import defaultdict
from difflib import SequenceMatcher

from ..db import get_db_connection
from .news_collector import normalize_title


def find_related_news(seed: dict, candidates: list[dict]) -> list[dict]:
    seed_title = normalize_title(seed.get("title", ""))
    seed_words = set(seed_title.split())
    related = []
    for candidate in candidates:
        if candidate.get("id") == seed.get("id"):
            continue
        candidate_title = normalize_title(candidate.get("title", ""))
        candidate_words = set(candidate_title.split())
        shared_words = seed_words & candidate_words
        overlap = len(shared_words) / max(1, min(len(seed_words), len(candidate_words)))
        similarity = SequenceMatcher(None, seed_title, candidate_title).ratio()
        if similarity >= 0.58 or (len(shared_words) >= 2 and overlap >= 0.65):
            related.append((max(similarity, overlap), candidate))
    related.sort(key=lambda result: result[0], reverse=True)
    return [candidate for _, candidate in related]


def score_story(news_items: list[dict]) -> int:
    score = len(news_items) * 15
    for item in news_items:
        if item.get("summary"):
            score += 10
        if item.get("url"):
            score += 5
    return score


def detect_story_clusters(limit: int = 20) -> list[dict]:
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT n.id, n.title, n.summary, n.url, n.created_at
            FROM news_items n
            ORDER BY n.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        title = row["title"]
        key = None
        for existing_key in grouped:
            if SequenceMatcher(None, normalize_title(title), normalize_title(existing_key)).ratio() >= 0.7:
                key = existing_key
                break
        if key is None:
            key = title
        grouped[key].append(dict(row))

    result = []
    for title, items in grouped.items():
        result.append(
            {
                "title": title,
                "items": items,
                "summary": items[0].get("summary") or "Auto-discovered news cluster",
                "score": score_story(items),
            }
        )
    return sorted(result, key=lambda item: item["score"], reverse=True)


def research_story(story: dict) -> dict:
    items = story.get("items", [])
    urls = [item.get("url") for item in items if item.get("url")]
    return {
        "story_title": story.get("title"),
        "summary": story.get("summary"),
        "evidence_sources": urls[:5],
        "article_ideas": [
            "What was announced or reported?",
            "Which facts are supported by multiple sources?",
            "What remains unverified or uncertain?",
        ],
    }


def save_story_clusters() -> list[dict]:
    clusters = detect_story_clusters()
    with get_db_connection() as conn:
        for cluster in clusters:
            conn.execute(
                "INSERT OR IGNORE INTO story_clusters (title, summary, score, status) VALUES (?, ?, ?, 'discovered')",
                (cluster["title"], cluster["summary"], cluster["score"]),
            )
        conn.commit()
    return clusters
