import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import DB_PATH


@contextmanager
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def initialize_db() -> None:
    with get_db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                source_type TEXT NOT NULL,
                category TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS news_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                external_id TEXT,
                title TEXT NOT NULL,
                summary TEXT,
                url TEXT,
                published_at TEXT,
                fingerprint TEXT NOT NULL UNIQUE,
                raw_payload TEXT,
                story_group TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(source_id) REFERENCES sources(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS story_clusters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                summary TEXT,
                score INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'discovered',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                story_group TEXT,
                title TEXT NOT NULL,
                summary TEXT,
                body TEXT,
                language TEXT NOT NULL DEFAULT 'en',
                status TEXT NOT NULL DEFAULT 'draft',
                seo_title TEXT,
                slug TEXT,
                meta_description TEXT,
                focus_keyphrase TEXT,
                tags TEXT NOT NULL DEFAULT '[]',
                wordpress_post_id INTEGER,
                wordpress_edit_url TEXT,
                wordpress_status TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        article_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(articles)").fetchall()
        }
        for column, column_type in (
            ("seo_title", "TEXT"),
            ("slug", "TEXT"),
            ("meta_description", "TEXT"),
            ("focus_keyphrase", "TEXT"),
            ("tags", "TEXT NOT NULL DEFAULT '[]'"),
            ("wordpress_post_id", "INTEGER"),
            ("wordpress_edit_url", "TEXT"),
            ("wordpress_status", "TEXT"),
        ):
            if column not in article_columns:
                conn.execute(f"ALTER TABLE articles ADD COLUMN {column} {column_type}")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO app_settings (key, value) VALUES ('auto_publish', 'false')"
        )
        conn.commit()


def seed_default_sources(sources):
    with get_db_connection() as conn:
        for source in sources:
            conn.execute(
                """
                INSERT OR IGNORE INTO sources (name, url, source_type, category, active)
                VALUES (?, ?, ?, ?, 1)
                """,
                (
                    source.get("name"),
                    source.get("url"),
                    source.get("source_type", "rss"),
                    source.get("category"),
                ),
            )
        conn.commit()
