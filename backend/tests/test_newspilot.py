from newspilot.services.news_collector import normalize_title, title_similarity
from fastapi.testclient import TestClient
from newspilot.api import create_app
from newspilot.db import get_db_connection
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import json
import pytest
from newspilot.services.news_collector import fetch_source_news
from newspilot.services.story_engine import find_related_news
from newspilot.services.article_writer import generate_article_from_story
from newspilot.services.wordpress import (
    check_wordpress_connection,
    push_article_to_wordpress,
    text_to_wordpress_html,
)


def _generated_article_text(title="Fresh headline", body="Original article text"):
    return json.dumps(
        {
            "title": title,
            "body": body,
            "excerpt": "A concise article excerpt.",
            "seo_title": "Fresh headline | News",
            "slug": "fresh-headline",
            "meta_description": "A concise description for search results.",
            "focus_keyphrase": "fresh headline",
            "tags": ["News", "Technology"],
        },
        ensure_ascii=False,
    )


def test_normalize_title_strips_special_chars():
    assert normalize_title("Google Launches New AI Model!") == "google launches new ai model"


def test_title_similarity_detects_duplicates():
    assert title_similarity("Google launches new AI model", "Google Launches New AI Model") > 0.9


def test_hindi_titles_are_preserved_and_compared():
    assert normalize_title("भारत में नई खबर!") == "भारत में नई खबर"
    assert title_similarity("भारत में नई खबर", "अलग मौसम समाचार") < 0.9


def test_homepage_source_discovers_its_rss_feed():
    page_response = httpx.Response(
        200,
        headers={"content-type": "text/html"},
        text='<html><head><link rel="alternate" type="application/rss+xml" href="/news.xml"></head></html>',
        request=httpx.Request("GET", "https://example.test/"),
    )
    feed_response = httpx.Response(
        200,
        headers={"content-type": "application/rss+xml"},
        text=(
            '<?xml version="1.0"?><rss version="2.0"><channel><title>Hindi</title>'
            '<item><title>नई खबर</title><link>https://example.test/story</link>'
            '<description>पूरा सारांश</description></item></channel></rss>'
        ),
        request=httpx.Request("GET", "https://example.test/news.xml"),
    )
    with patch("newspilot.services.news_collector.httpx.get", side_effect=[page_response, feed_response]):
        items = fetch_source_news({"id": 7, "url": "https://example.test/"})

    assert len(items) == 1
    assert items[0]["title"] == "नई खबर"
    assert items[0]["url"] == "https://example.test/story"


def test_known_publisher_homepage_uses_its_verified_feed():
    page_response = httpx.Response(
        200,
        headers={"content-type": "text/html"},
        text="<html><head></head><body>News</body></html>",
        request=httpx.Request("GET", "https://indianexpress.com/"),
    )
    feed_response = httpx.Response(
        200,
        headers={"content-type": "application/rss+xml"},
        text='<?xml version="1.0"?><rss version="2.0"><channel><title>News</title></channel></rss>',
        request=httpx.Request("GET", "https://indianexpress.com/feed/"),
    )
    with patch("newspilot.services.news_collector.httpx.get", side_effect=[page_response, feed_response]) as get:
        items = fetch_source_news({"id": 8, "url": "https://indianexpress.com/"})

    assert items == []
    assert get.call_args_list[1].args[0] == "https://indianexpress.com/feed/"


def test_related_news_matches_similar_titles_across_sources():
    seed = {"id": 1, "title": "Google launches a new AI model"}
    related = {"id": 2, "title": "Google unveils a new AI model"}
    unrelated = {"id": 3, "title": "Local weather forecast for the weekend"}

    assert find_related_news(seed, [related, unrelated]) == [related]


def test_full_article_generation_requires_ai_key(monkeypatch):
    monkeypatch.setattr("newspilot.services.article_writer.GEMINI_API_KEY", None)

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        generate_article_from_story({"story_title": "Headline", "summary": "Feed excerpt"})


def test_article_generation_uses_gemini_rest_api(monkeypatch):
    monkeypatch.setattr("newspilot.services.article_writer.GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setattr("newspilot.services.article_writer.GEMINI_MODEL", "gemini-test-model")
    generated_text = _generated_article_text(
        body="Original article text https://example.test/source"
    )
    response = httpx.Response(
        200,
        json={"candidates": [{"content": {"parts": [{"text": generated_text}]}}]},
        request=httpx.Request(
            "POST",
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-test-model:generateContent",
        ),
    )
    with patch("newspilot.services.article_writer.httpx.post", return_value=response) as post:
        article = generate_article_from_story(
            {
                "story_title": "Headline",
                "summary": "Feed excerpt",
                "source_reports": [
                    {
                        "source": "Example Publisher",
                        "title": "Headline from publisher",
                        "excerpt": "Report excerpt",
                        "url": "https://example.test/source",
                    }
                ],
            }
        )

    assert article["title"] == "Fresh headline"
    assert article["body"] == "Original article text"
    assert article["meta_description"] == "A concise description for search results."
    assert article["tags"] == ["News", "Technology"]
    assert post.call_args.kwargs["headers"]["x-goog-api-key"] == "test-gemini-key"
    assert post.call_args.args[0].endswith("gemini-test-model:generateContent")
    request_payload = post.call_args.kwargs["json"]
    instructions = request_payload["systemInstruction"]["parts"][0]["text"]
    assert "approximately 1,000 words" in instructions
    assert "publisher/source names, URLs, citations" in instructions
    assert request_payload["generationConfig"]["responseMimeType"] == "application/json"
    assert request_payload["generationConfig"]["maxOutputTokens"] >= 4000
    user_input = request_payload["contents"][0]["parts"][0]["text"]
    assert "Example Publisher" not in user_input
    assert "https://example.test/source" not in user_input


def test_article_generation_retries_temporary_gemini_overload(monkeypatch):
    monkeypatch.setattr("newspilot.services.article_writer.GEMINI_API_KEY", "test-gemini-key")
    busy_response = httpx.Response(
        503,
        json={"error": {"status": "UNAVAILABLE", "message": "The model is busy."}},
        request=httpx.Request("POST", "https://generativelanguage.googleapis.com/test"),
    )
    success_response = httpx.Response(
        200,
        json={"candidates": [{"content": {"parts": [{"text": _generated_article_text(body="Recovered article")} ]}}]},
        request=httpx.Request("POST", "https://generativelanguage.googleapis.com/test"),
    )
    with (
        patch(
            "newspilot.services.article_writer.httpx.post",
            side_effect=[busy_response, success_response],
        ) as post,
        patch("newspilot.services.article_writer.time.sleep") as sleep,
    ):
        article = generate_article_from_story({"story_title": "Headline", "summary": "Summary"})

    assert article["body"] == "Recovered article"
    assert post.call_count == 2
    sleep.assert_called_once_with(1)


def test_article_generation_reports_gemini_error_after_retries(monkeypatch):
    monkeypatch.setattr("newspilot.services.article_writer.GEMINI_API_KEY", "test-gemini-key")
    busy_response = httpx.Response(
        503,
        json={"error": {"status": "UNAVAILABLE", "message": "The model is busy."}},
        request=httpx.Request("POST", "https://generativelanguage.googleapis.com/test"),
    )
    with (
        patch(
            "newspilot.services.article_writer.httpx.post",
            return_value=busy_response,
        ) as post,
        patch("newspilot.services.article_writer.time.sleep"),
    ):
        with pytest.raises(
            RuntimeError,
            match=r"Gemini API returned HTTP 503 \(UNAVAILABLE\): The model is busy\.",
        ):
            generate_article_from_story({"story_title": "Headline", "summary": "Summary"})

    assert post.call_count == 3


def test_health_endpoint():
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_startup_does_not_add_example_sources(tmp_path, monkeypatch):
    monkeypatch.setattr("newspilot.db.DB_PATH", tmp_path / "test.db")

    with TestClient(create_app()) as client:
        assert client.get("/sources").json() == []


def test_source_can_be_edited_and_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr("newspilot.db.DB_PATH", tmp_path / "test.db")

    with TestClient(create_app()) as client:
        created = client.post(
            "/sources",
            json={
                "name": "Hindi RSS",
                "url": "https://example.test/hindi.xml",
                "category": "Hindi",
            },
        )
        source_id = created.json()["id"]

        updated = client.put(
            f"/sources/{source_id}",
            json={
                "name": "Updated Hindi RSS",
                "url": "https://example.test/updated-hindi.xml",
                "category": "Hindi",
            },
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Updated Hindi RSS"

        deleted = client.delete(f"/sources/{source_id}")
        assert deleted.status_code == 200
        assert all(source["id"] != source_id for source in client.get("/sources").json())


def test_collected_news_keeps_original_language(tmp_path, monkeypatch):
    monkeypatch.setattr("newspilot.db.DB_PATH", tmp_path / "test.db")

    with TestClient(create_app()) as client:
        source = client.post(
            "/sources",
            json={
                "name": "Hindi RSS",
                "url": "https://example.test/hindi.xml",
                "category": "Hindi",
            },
        ).json()
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO news_items (source_id, title, summary, url, fingerprint)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    source["id"],
                    "भारत में नई खबर",
                    "आज की खबर का सारांश",
                    "https://example.test/story",
                    "hindi-news-test",
                ),
            )
            conn.execute(
                """
                INSERT INTO news_items (source_id, title, summary, url, published_at, fingerprint)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    source["id"],
                    "पुरानी खबर",
                    "एक दिन से पुरानी",
                    "https://example.test/old-story",
                    (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
                    "old-hindi-news-test",
                ),
            )
            conn.commit()

        response = client.get("/news")

    assert response.status_code == 200
    article = next(item for item in response.json() if item["title"] == "भारत में नई खबर")
    assert article["summary"] == "आज की खबर का सारांश"
    assert all(item["title"] != "पुरानी खबर" for item in response.json())


def test_generate_article_uses_related_recent_source_reports(tmp_path, monkeypatch):
    monkeypatch.setattr("newspilot.db.DB_PATH", tmp_path / "test.db")
    now = datetime.now(timezone.utc).isoformat()

    with TestClient(create_app()) as client:
        seed_source = client.post(
            "/sources",
            json={"name": "Source One", "url": "https://example.test/one.xml"},
        ).json()
        related_source = client.post(
            "/sources",
            json={"name": "Source Two", "url": "https://example.test/two.xml"},
        ).json()
        with get_db_connection() as conn:
            seed_cursor = conn.execute(
                """
                INSERT INTO news_items (source_id, title, summary, url, published_at, fingerprint)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    seed_source["id"],
                    "Google launches a new AI model",
                    "<p>Official announcement summary</p>",
                    "https://example.test/one",
                    now,
                    "seed-news",
                ),
            )
            conn.execute(
                """
                INSERT INTO news_items (source_id, title, summary, url, published_at, fingerprint)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    related_source["id"],
                    "Google unveils a new AI model",
                    "Second source summary",
                    "https://example.test/two",
                    now,
                    "related-news",
                ),
            )
            conn.commit()
        seed_id = seed_cursor.lastrowid

        generated_drafts = [
            {
                "title": "Google launches a new AI model",
                "summary": "Official announcement summary",
                "body": "Original draft",
                "language": "en",
                "status": "draft",
            },
            {
                "title": "Google launches a new AI model",
                "summary": "Official announcement summary",
                "body": "Regenerated original draft",
                "language": "en",
                "status": "draft",
            },
        ]
        with patch("newspilot.api.generate_article_from_story", side_effect=generated_drafts) as generate:
            response = client.post(f"/news/{seed_id}/generate")
            article_id = response.json()["article_id"]
            regenerated = client.post(f"/articles/{article_id}/regenerate")
            articles_after_regenerate = client.get("/articles").json()
            details = client.get(f"/articles/{article_id}")

    assert response.status_code == 200
    assert response.json()["related_count"] == 2
    assert response.json()["source_count"] == 2
    reports = generate.call_args.args[0]["source_reports"]
    assert {report["source"] for report in reports} == {"Source One", "Source Two"}
    assert reports[0]["excerpt"] == "Official announcement summary"
    assert regenerated.status_code == 200
    assert regenerated.json()["status"] == "regenerated"
    assert regenerated.json()["article"]["body"] == "Regenerated original draft"
    assert len(articles_after_regenerate) == 1
    assert details.json()["body"] == "Regenerated original draft"


def test_generate_reports_missing_ai_key_without_creating_partial_post(tmp_path, monkeypatch):
    monkeypatch.setattr("newspilot.db.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("newspilot.services.article_writer.GEMINI_API_KEY", None)
    now = datetime.now(timezone.utc).isoformat()

    with TestClient(create_app()) as client:
        source = client.post(
            "/sources",
            json={"name": "Source", "url": "https://example.test/feed.xml"},
        ).json()
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO news_items (source_id, title, summary, url, published_at, fingerprint)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (source["id"], "New story", "Short feed excerpt", "https://example.test/story", now, "new-story"),
            )
            conn.commit()
        response = client.post(f"/news/{cursor.lastrowid}/generate")

        assert response.status_code == 503
        assert "GEMINI_API_KEY" in response.json()["detail"]
        assert client.get("/articles").json() == []


def test_wordpress_content_conversion_formats_and_escapes_text():
    body = "Intro <script>alert(1)</script>\n\n## Details\n\n- First item\n- Second item"

    assert text_to_wordpress_html(body) == (
        "<p>Intro &lt;script&gt;alert(1)&lt;/script&gt;</p>\n"
        "<h2>Details</h2>\n"
        "<ul><li>First item</li><li>Second item</li></ul>"
    )

    assert text_to_wordpress_html("Intro. ### Details\nMore information.") == (
        "<p>Intro.</p>\n<h3>Details</h3>\n<p>More information.</p>"
    )


def test_wordpress_push_creates_a_draft_post(monkeypatch):
    monkeypatch.setattr("newspilot.services.wordpress.WORDPRESS_SITE_URL", "https://news.example")
    monkeypatch.setattr("newspilot.services.wordpress.WORDPRESS_CONNECTOR_TOKEN", "test-token")
    response = httpx.Response(
        201,
        json={"id": 42, "status": "draft"},
        request=httpx.Request("POST", "https://news.example/wp-json/newspilot/v1/posts"),
    )
    with patch("newspilot.services.wordpress.httpx.post", return_value=response) as post:
        result = push_article_to_wordpress(
            title="New story",
            body="## Details\nA paragraph.",
            summary="Short excerpt",
            seo_title="New story | News",
            slug="new-story",
            meta_description="A search description.",
            focus_keyphrase="new story",
            tags=[],
        )

    request = post.call_args.kwargs
    assert post.call_args.args[0] == "https://news.example/wp-json/newspilot/v1/posts"
    assert request["headers"] == {"X-NewsPilot-Token": "test-token"}
    assert "auth" not in request
    assert request["json"]["status"] == "draft"
    assert request["json"]["content"] == "<h2>Details</h2>\n<p>A paragraph.</p>"
    assert request["json"]["slug"] == "new-story"
    assert request["json"]["newspilot_yoast"] == {
        "seo_title": "New story | News",
        "meta_description": "A search description.",
        "focus_keyphrase": "new story",
    }
    assert result == {
        "id": 42,
        "edit_url": "https://news.example/wp-admin/post.php?post=42&action=edit",
        "status": "created",
        "wordpress_status": "draft",
    }


def test_wordpress_push_includes_generated_tags(monkeypatch):
    monkeypatch.setattr("newspilot.services.wordpress.WORDPRESS_SITE_URL", "https://news.example")
    monkeypatch.setattr("newspilot.services.wordpress.WORDPRESS_CONNECTOR_TOKEN", "test-token")
    post_created = httpx.Response(
        201,
        json={"id": 42, "status": "publish"},
        request=httpx.Request("POST", "https://news.example/wp-json/newspilot/v1/posts"),
    )
    with patch("newspilot.services.wordpress.httpx.post", return_value=post_created) as post:
        result = push_article_to_wordpress(
            title="New story",
            body="Article body.",
            summary="Short excerpt",
            seo_title="New story | News",
            slug="new-story",
            meta_description="A search description.",
            focus_keyphrase="new story",
            tags=["News"],
            publish=True,
        )

    assert post.call_args.kwargs["json"]["tags"] == ["News"]
    assert post.call_args.kwargs["json"]["status"] == "publish"
    assert result["wordpress_status"] == "publish"


def test_wordpress_connection_check_uses_connector_token(monkeypatch):
    monkeypatch.setattr("newspilot.services.wordpress.WORDPRESS_SITE_URL", "https://news.example")
    monkeypatch.setattr("newspilot.services.wordpress.WORDPRESS_CONNECTOR_TOKEN", "test-token")
    response = httpx.Response(
        200,
        json={"connected": True, "site_url": "https://news.example/"},
        request=httpx.Request("GET", "https://news.example/wp-json/newspilot/v1/status"),
    )
    with patch("newspilot.services.wordpress.httpx.get", return_value=response) as get:
        result = check_wordpress_connection()

    assert get.call_args.args[0] == "https://news.example/wp-json/newspilot/v1/status"
    assert get.call_args.kwargs["headers"] == {"X-NewsPilot-Token": "test-token"}
    assert result == {
        "configured": True,
        "site_url": "https://news.example",
        "connected": True,
    }


def test_article_can_be_saved_and_pushed_to_wordpress(tmp_path, monkeypatch):
    monkeypatch.setattr("newspilot.db.DB_PATH", tmp_path / "test.db")

    with TestClient(create_app()) as client:
        with get_db_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO articles (title, body, language, status) VALUES (?, ?, ?, ?)",
                ("Original title", "Original body", "en", "draft"),
            )
            conn.commit()
        article_id = cursor.lastrowid
        wordpress_result = {
            "id": 42,
            "edit_url": "https://news.example/wp-admin/post.php?post=42&action=edit",
            "status": "created",
            "wordpress_status": "draft",
        }
        with patch("newspilot.api.check_wordpress_connection", return_value={
            "configured": True,
            "connected": True,
            "site_url": "https://news.example",
        }):
            status = client.get("/wordpress/status")
        with patch("newspilot.api.push_article_to_wordpress", return_value=wordpress_result) as push:
            saved = client.put(
                f"/articles/{article_id}",
                json={
                    "title": "Updated title",
                    "body": "## Details\nUpdated text.",
                    "summary": "Updated excerpt",
                    "seo_title": "Updated SEO title",
                    "slug": "updated-title",
                    "meta_description": "Updated meta description.",
                    "focus_keyphrase": "updated title",
                    "tags": ["News"],
                },
            )
            pushed = client.post(f"/articles/{article_id}/push-to-wordpress")
            details = client.get(f"/articles/{article_id}").json()

    assert status.json() == {
        "configured": True,
        "connected": True,
        "site_url": "https://news.example",
    }
    assert saved.status_code == 200
    assert pushed.status_code == 200
    assert push.call_args.kwargs["title"] == "Updated title"
    assert push.call_args.kwargs["body"] == "## Details\nUpdated text."
    assert push.call_args.kwargs["seo_title"] == "Updated SEO title"
    assert push.call_args.kwargs["tags"] == ["News"]
    assert details["wordpress_post_id"] == 42
    assert details["wordpress_edit_url"] == wordpress_result["edit_url"]


def test_auto_publish_is_off_by_default_and_requires_wordpress(tmp_path, monkeypatch):
    monkeypatch.setattr("newspilot.db.DB_PATH", tmp_path / "test.db")

    with TestClient(create_app()) as client:
        with patch(
            "newspilot.api.check_wordpress_connection",
            return_value={
                "configured": False,
                "connected": False,
                "site_url": None,
                "error": "Not configured",
            },
        ):
            settings = client.get("/settings")
            enable = client.put("/settings", json={"auto_publish": True})

    assert settings.status_code == 200
    assert settings.json()["auto_publish"] is False
    assert settings.json()["configured"] is False
    assert settings.json()["connected"] is False
    assert enable.status_code == 409
    assert "Connect WordPress" in enable.json()["detail"]


def test_auto_publish_sends_article_and_yoast_fields_to_wordpress(tmp_path, monkeypatch):
    monkeypatch.setattr("newspilot.db.DB_PATH", tmp_path / "test.db")
    now = datetime.now(timezone.utc).isoformat()
    generated = {
        "title": "Fresh article title",
        "summary": "Article excerpt",
        "body": "Original body",
        "language": "en",
        "status": "draft",
        "seo_title": "Fresh article title | News",
        "slug": "fresh-article-title",
        "meta_description": "A concise search description.",
        "focus_keyphrase": "fresh article",
        "tags": ["News"],
    }
    wordpress_result = {
        "id": 57,
        "edit_url": "https://news.example/wp-admin/post.php?post=57&action=edit",
        "status": "created",
        "wordpress_status": "publish",
    }

    with TestClient(create_app()) as client:
        source = client.post(
            "/sources",
            json={"name": "Source", "url": "https://example.test/feed.xml"},
        ).json()
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO news_items (source_id, title, summary, url, published_at, fingerprint)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (source["id"], "Original headline", "Source excerpt", "https://example.test/story", now, "auto-publish"),
            )
            conn.commit()
        with (
            patch("newspilot.api.check_wordpress_connection", return_value={
                "configured": True,
                "connected": True,
                "site_url": "https://news.example",
            }),
            patch("newspilot.api.generate_article_from_story", return_value=generated),
            patch("newspilot.api.push_article_to_wordpress", return_value=wordpress_result) as push,
        ):
            settings = client.put("/settings", json={"auto_publish": True})
            result = client.post(f"/news/{cursor.lastrowid}/generate")
            details = client.get(f"/articles/{result.json()['article_id']}").json()

    assert settings.json()["auto_publish"] is True
    assert result.json()["status"] == "published"
    assert result.json()["automatic_publish"]["status"] == "published"
    assert push.call_args.kwargs["publish"] is True
    assert push.call_args.kwargs["seo_title"] == generated["seo_title"]
    assert push.call_args.kwargs["focus_keyphrase"] == generated["focus_keyphrase"]
    assert details["wordpress_status"] == "publish"
