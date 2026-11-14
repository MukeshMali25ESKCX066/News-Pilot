from __future__ import annotations

import json
import sqlite3
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .config import APP_NAME, SCHEDULER_INTERVAL_MINUTES
from .db import get_db_connection, initialize_db
from .services.article_writer import generate_article_from_story
from .services.news_collector import collect_news_for_all_sources, parse_datetime
from .services.story_engine import detect_story_clusters, find_related_news, research_story, save_story_clusters
from .services.wordpress import (
    check_wordpress_connection,
    push_article_to_wordpress,
)

logger = logging.getLogger(__name__)


class SourcePayload(BaseModel):
    name: str
    url: str
    source_type: str = "rss"
    category: str | None = None


class StoryDraftPayload(BaseModel):
    story_title: str
    summary: str
    evidence_sources: list[str] = []


class ArticleUpdatePayload(BaseModel):
    title: str
    body: str
    summary: str
    seo_title: str
    slug: str
    meta_description: str
    focus_keyphrase: str
    tags: list[str] = Field(default_factory=list)


class AutoPublishPayload(BaseModel):
    auto_publish: bool


def _article_data(row) -> dict:
    article = dict(row)
    article["tags"] = json.loads(article.get("tags") or "[]")
    return article


def _auto_publish_enabled() -> bool:
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'auto_publish'"
        ).fetchone()
    return bool(row and row["value"] == "true")


def _publish_automatically(article_id: int, article: dict) -> dict | None:
    if not _auto_publish_enabled():
        return None

    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT wordpress_post_id FROM articles WHERE id = ?",
            (article_id,),
        ).fetchone()
    try:
        post = push_article_to_wordpress(
            title=article["title"],
            body=article["body"],
            summary=article["summary"],
            seo_title=article["seo_title"],
            slug=article["slug"],
            meta_description=article["meta_description"],
            focus_keyphrase=article["focus_keyphrase"],
            tags=article["tags"],
            existing_post_id=row["wordpress_post_id"] if row else None,
            publish=True,
        )
    except RuntimeError as exc:
        logger.error("Automatic WordPress publishing failed for article %s: %s", article_id, exc)
        return {"status": "failed", "error": str(exc)}

    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE articles
            SET wordpress_post_id = ?, wordpress_edit_url = ?, wordpress_status = ?
            WHERE id = ?
            """,
            (post["id"], post["edit_url"], post["wordpress_status"], article_id),
        )
        conn.commit()
    return {
        "status": "published",
        "wordpress_post_id": post["id"],
        "wordpress_edit_url": post["edit_url"],
    }


def run_news_collection() -> dict:
    result = collect_news_for_all_sources()
    if result["errors"]:
        logger.warning("Some news sources could not be collected: %s", result["errors"])
    return {
        "status": "partial" if result["errors"] else "success",
        **result,
        "interval_minutes": SCHEDULER_INTERVAL_MINUTES,
    }


def get_recent_news() -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT n.id, n.title, n.summary, n.url, n.published_at, n.created_at,
                   n.source_id, s.name AS source_name, s.category AS source_category
            FROM news_items n
            JOIN sources s ON s.id = n.source_id
            ORDER BY n.created_at DESC, n.id DESC
            """
        ).fetchall()
    recent = []
    for row in rows:
        article = dict(row)
        published = parse_datetime(article["published_at"]) or parse_datetime(article["created_at"])
        if published and published >= cutoff:
            recent.append(article)
    return recent


def build_story_from_news(seed: dict, recent_articles: list[dict]) -> dict:
    related = [seed, *find_related_news(seed, recent_articles)]
    source_reports = []
    evidence_sources = []
    for report in related:
        excerpt = BeautifulSoup(report["summary"] or "", "html.parser").get_text(" ", strip=True)
        source_reports.append(
            {
                "source": report["source_name"],
                "title": report["title"],
                "excerpt": excerpt[:300],
                "url": report["url"],
            }
        )
        if report["url"]:
            evidence_sources.append(report["url"])
    return {
        "story_title": seed["title"],
        "summary": source_reports[0]["excerpt"],
        "evidence_sources": evidence_sources,
        "source_reports": source_reports,
    }


def create_app() -> FastAPI:
    app = FastAPI(title=APP_NAME, version="0.1.0")
    scheduler = BackgroundScheduler()

    @app.on_event("startup")
    def startup_event() -> None:
        initialize_db()
        if not scheduler.running:
            scheduler.add_job(run_news_collection, "interval", minutes=SCHEDULER_INTERVAL_MINUTES)
            scheduler.start()

    @app.on_event("shutdown")
    def shutdown_event() -> None:
        if scheduler.running:
            scheduler.shutdown(wait=False)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "app": APP_NAME}

    @app.get("/settings")
    def get_settings() -> dict:
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = 'auto_publish'"
            ).fetchone()
        return {
            "auto_publish": bool(row and row["value"] == "true"),
            **check_wordpress_connection(),
        }

    @app.put("/settings")
    def update_settings(payload: AutoPublishPayload) -> dict:
        if payload.auto_publish and not check_wordpress_connection()["connected"]:
            raise HTTPException(
                status_code=409,
                detail="Connect WordPress before enabling automatic publishing.",
            )
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO app_settings (key, value) VALUES ('auto_publish', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                ("true" if payload.auto_publish else "false",),
            )
            conn.commit()
        return get_settings()

    @app.get("/sources")
    def list_sources() -> list[dict]:
        with get_db_connection() as conn:
            rows = conn.execute(
                "SELECT id, name, url, source_type, category, active FROM sources ORDER BY created_at DESC"
            ).fetchall()
            return [dict(row) for row in rows]

    @app.post("/sources")
    def add_source(payload: SourcePayload) -> dict:
        with get_db_connection() as conn:
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO sources (name, url, source_type, category, active)
                    VALUES (?, ?, ?, ?, 1)
                    """,
                    (payload.name, payload.url, payload.source_type, payload.category),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise HTTPException(status_code=409, detail="A source with this URL already exists.") from exc
            source_id = cursor.lastrowid
        return {"id": source_id, "name": payload.name, "url": payload.url}

    @app.put("/sources/{source_id}")
    def update_source(source_id: int, payload: SourcePayload) -> dict:
        with get_db_connection() as conn:
            try:
                cursor = conn.execute(
                    """
                    UPDATE sources
                    SET name = ?, url = ?, source_type = ?, category = ?
                    WHERE id = ?
                    """,
                    (payload.name, payload.url, payload.source_type, payload.category, source_id),
                )
                if cursor.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Source not found.")
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise HTTPException(status_code=409, detail="A source with this URL already exists.") from exc
        return {"id": source_id, "name": payload.name, "url": payload.url}

    @app.delete("/sources/{source_id}")
    def delete_source(source_id: int) -> dict:
        with get_db_connection() as conn:
            source = conn.execute("SELECT id FROM sources WHERE id = ?", (source_id,)).fetchone()
            if source is None:
                raise HTTPException(status_code=404, detail="Source not found.")
            conn.execute("DELETE FROM news_items WHERE source_id = ?", (source_id,))
            conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
            conn.commit()
        return {"status": "deleted", "id": source_id}

    @app.post("/collect")
    def collect_news() -> dict:
        return run_news_collection()

    @app.get("/news")
    def list_news() -> list[dict]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        with get_db_connection() as conn:
            rows = conn.execute(
                """
                SELECT n.id, n.title, n.summary, n.url, n.published_at, n.created_at,
                       s.name AS source_name, s.category AS source_category
                FROM news_items n
                JOIN sources s ON s.id = n.source_id
                ORDER BY n.created_at DESC, n.id DESC
                """
            ).fetchall()
            news = []
            for row in rows:
                article = dict(row)
                published = parse_datetime(article["published_at"]) or parse_datetime(article["created_at"])
                if published and published >= cutoff:
                    news.append(article)
            news.sort(
                key=lambda article: parse_datetime(article["published_at"])
                or parse_datetime(article["created_at"])
                or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            return news

    @app.get("/stories")
    def stories() -> list[dict]:
        clusters = detect_story_clusters(limit=50)
        return clusters

    @app.post("/news/{news_id}/generate")
    def generate_article_from_news(news_id: int) -> dict:
        recent_articles = get_recent_news()
        seed = next((article for article in recent_articles if article["id"] == news_id), None)
        if seed is None:
            raise HTTPException(status_code=404, detail="News item not found in the last 24 hours.")

        story = build_story_from_news(seed, recent_articles)
        try:
            article = generate_article_from_story(story)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO articles (
                    story_group, title, summary, body, language, status,
                    seo_title, slug, meta_description, focus_keyphrase, tags
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    seed["title"],
                    article["title"],
                    article["summary"],
                    article["body"],
                    article["language"],
                    article["status"],
                    article.get("seo_title", ""),
                    article.get("slug", ""),
                    article.get("meta_description", ""),
                    article.get("focus_keyphrase", ""),
                    json.dumps(article.get("tags", []), ensure_ascii=False),
                ),
            )
            conn.commit()
            article_id = cursor.lastrowid
        automatic_publish = _publish_automatically(article_id, article)
        return {
            "status": "published" if automatic_publish and automatic_publish["status"] == "published" else "draft-created",
            "article_id": article_id,
            "article": article,
            "related_count": len(story["source_reports"]),
            "source_count": len({report["source"] for report in story["source_reports"]}),
            "automatic_publish": automatic_publish,
        }

    @app.post("/articles/{article_id}/regenerate")
    def regenerate_article(article_id: int) -> dict:
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT id, story_group FROM articles WHERE id = ?",
                (article_id,),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Article draft not found.")

        recent_articles = get_recent_news()
        seed = next(
            (article for article in recent_articles if article["title"] == row["story_group"]),
            None,
        )
        if seed is None:
            raise HTTPException(
                status_code=404,
                detail="No matching news from the last 24 hours is available to regenerate this draft.",
            )
        story = build_story_from_news(seed, recent_articles)
        try:
            article = generate_article_from_story(story)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        with get_db_connection() as conn:
            conn.execute(
                """
                UPDATE articles
                SET title = ?, summary = ?, body = ?, language = ?, status = 'draft',
                    seo_title = ?, slug = ?, meta_description = ?, focus_keyphrase = ?, tags = ?
                WHERE id = ?
                """,
                (
                    article["title"],
                    article["summary"],
                    article["body"],
                    article["language"],
                    article.get("seo_title", ""),
                    article.get("slug", ""),
                    article.get("meta_description", ""),
                    article.get("focus_keyphrase", ""),
                    json.dumps(article.get("tags", []), ensure_ascii=False),
                    article_id,
                ),
            )
            conn.commit()
        automatic_publish = _publish_automatically(article_id, article)
        return {
            "status": "published" if automatic_publish and automatic_publish["status"] == "published" else "regenerated",
            "article": article,
            "related_count": len(story["source_reports"]),
            "source_count": len({report["source"] for report in story["source_reports"]}),
            "automatic_publish": automatic_publish,
        }

    @app.post("/stories/research")
    def research_cluster() -> dict:
        clusters = save_story_clusters()
        research = [research_story(story) for story in clusters]
        return {"status": "ok", "stories": research}

    @app.post("/articles/generate")
    def create_article(payload: StoryDraftPayload) -> dict:
        story = {
            "story_title": payload.story_title,
            "summary": payload.summary,
            "evidence_sources": payload.evidence_sources,
        }
        try:
            article = generate_article_from_story(story)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO articles (
                    story_group, title, summary, body, language, status,
                    seo_title, slug, meta_description, focus_keyphrase, tags
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.story_title,
                    article["title"],
                    article["summary"],
                    article["body"],
                    article["language"],
                    article["status"],
                    article.get("seo_title", ""),
                    article.get("slug", ""),
                    article.get("meta_description", ""),
                    article.get("focus_keyphrase", ""),
                    json.dumps(article.get("tags", []), ensure_ascii=False),
                ),
            )
            conn.commit()
            article_id = cursor.lastrowid
        automatic_publish = _publish_automatically(article_id, article)
        return {
            "status": "published" if automatic_publish and automatic_publish["status"] == "published" else "draft-created",
            "article_id": article_id,
            "article": article,
            "automatic_publish": automatic_publish,
        }

    @app.get("/articles")
    def list_articles() -> list[dict]:
        with get_db_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, story_group, title, summary, language, status, created_at,
                       seo_title, slug, meta_description, focus_keyphrase, tags,
                       wordpress_post_id, wordpress_edit_url, wordpress_status
                FROM articles ORDER BY created_at DESC
                """
            ).fetchall()
            return [_article_data(row) for row in rows]

    @app.get("/articles/{article_id}")
    def get_article(article_id: int) -> dict:
        with get_db_connection() as conn:
            row = conn.execute(
                """
                SELECT id, story_group, title, summary, body, language, status, created_at,
                       seo_title, slug, meta_description, focus_keyphrase, tags,
                       wordpress_post_id, wordpress_edit_url, wordpress_status
                FROM articles
                WHERE id = ?
                """,
                (article_id,),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Article draft not found.")
        return _article_data(row)

    @app.put("/articles/{article_id}")
    def update_article(article_id: int, payload: ArticleUpdatePayload) -> dict:
        if not payload.title.strip():
            raise HTTPException(status_code=422, detail="Article title cannot be empty.")
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE articles
                SET title = ?, body = ?, summary = ?, seo_title = ?, slug = ?,
                    meta_description = ?, focus_keyphrase = ?, tags = ?
                WHERE id = ?
                """,
                (
                    payload.title.strip(),
                    payload.body,
                    payload.summary,
                    payload.seo_title,
                    payload.slug,
                    payload.meta_description,
                    payload.focus_keyphrase,
                    json.dumps(payload.tags, ensure_ascii=False),
                    article_id,
                ),
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Article draft not found.")
            conn.commit()
        with get_db_connection() as conn:
            row = conn.execute(
                """
                SELECT id, story_group, title, summary, body, language, status, created_at,
                       seo_title, slug, meta_description, focus_keyphrase, tags,
                       wordpress_post_id, wordpress_edit_url, wordpress_status
                FROM articles WHERE id = ?
                """,
                (article_id,),
            ).fetchone()
        return _article_data(row)

    @app.get("/wordpress/status")
    def wordpress_status() -> dict:
        return check_wordpress_connection()

    @app.post("/articles/{article_id}/push-to-wordpress")
    def push_article(article_id: int) -> dict:
        with get_db_connection() as conn:
            article = conn.execute(
                """
                SELECT id, title, summary, body, seo_title, slug, meta_description,
                       focus_keyphrase, tags, wordpress_post_id
                FROM articles WHERE id = ?
                """,
                (article_id,),
            ).fetchone()
        if article is None:
            raise HTTPException(status_code=404, detail="Article draft not found.")
        try:
            wordpress_post = push_article_to_wordpress(
                title=article["title"],
                body=article["body"] or "",
                summary=article["summary"] or "",
                seo_title=article["seo_title"] or "",
                slug=article["slug"] or "",
                meta_description=article["meta_description"] or "",
                focus_keyphrase=article["focus_keyphrase"] or "",
                tags=json.loads(article["tags"] or "[]"),
                existing_post_id=article["wordpress_post_id"],
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        with get_db_connection() as conn:
            conn.execute(
                """
                UPDATE articles
                SET wordpress_post_id = ?, wordpress_edit_url = ?, wordpress_status = ?
                WHERE id = ?
                """,
                (
                    wordpress_post["id"],
                    wordpress_post["edit_url"],
                    wordpress_post["wordpress_status"],
                    article_id,
                ),
            )
            conn.commit()
        return {
            "status": wordpress_post["status"],
            "wordpress_post_id": wordpress_post["id"],
            "wordpress_edit_url": wordpress_post["edit_url"],
        }

    return app


app = create_app()
