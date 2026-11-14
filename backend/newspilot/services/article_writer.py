import json
import re
import time
from typing import Any

import httpx
from bs4 import BeautifulSoup

from ..config import GEMINI_API_KEY, GEMINI_MODEL


def _gemini_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        error = {}
    status = error.get("status")
    message = error.get("message") or "No error details provided."
    status_text = f" ({status})" if status else ""
    return f"Gemini API returned HTTP {response.status_code}{status_text}: {message}"


def detect_language(text: str) -> str:
    if any("\u0900" <= character <= "\u097f" for character in text):
        return "hi"
    if any("\u3040" <= character <= "\u30ff" for character in text):
        return "ja"
    if any("\uac00" <= character <= "\ud7af" for character in text):
        return "ko"
    if any("\u4e00" <= character <= "\u9fff" for character in text):
        return "zh"
    if any("\u0600" <= character <= "\u06ff" for character in text):
        return "ar"
    if any("\u0400" <= character <= "\u04ff" for character in text):
        return "ru"
    if text and text.isascii():
        return "en"
    return "und"


def generate_article_from_story(story: dict[str, Any]) -> dict[str, Any]:
    if not GEMINI_API_KEY:
        raise RuntimeError(
            "AI article generation is not configured. Set GEMINI_API_KEY in .env and restart NewsPilot."
        )

    title = story.get("story_title") or story.get("title") or "Untitled story"
    raw_summary = str(story.get("summary") or "")
    summary = BeautifulSoup(raw_summary, "html.parser").get_text(" ", strip=True)
    summary = re.sub(r"\s+", " ", summary)[:500]
    source_reports = story.get("source_reports", [])
    language = detect_language(f"{title} {summary}")
    model_reports = [
        {
            "title": report.get("title", ""),
            "excerpt": report.get("excerpt", ""),
        }
        for report in source_reports
    ]

    try:
        request_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{GEMINI_MODEL}:generateContent"
        )
        request_headers = {
            "x-goog-api-key": GEMINI_API_KEY,
            "Content-Type": "application/json",
        }
        request_body = {
            "systemInstruction": {
                "parts": [
                    {
                        "text": (
                            "Create an original news article in the same language as the supplied title and excerpts. "
                            "Write approximately 1,000 words (aim for 950-1,050), not a brief summary. Create a fresh, "
                            "accurate headline that is meaningfully different from the supplied headline. "
                            "Use only facts in the supplied excerpts; do not invent facts or claim to have read full articles. "
                            "Do not include publisher/source names, URLs, citations, attribution, or a sources/references section "
                            "anywhere in the article body. Use the source information only as private research context. "
                            "Do not copy or closely paraphrase source wording. "
                            "Format the body with ## subheadings on separate lines and blank lines between paragraphs. "
                            "Do not repeat the headline in the body. Avoid filler and repeated points; if evidence is limited, "
                            "be transparent about unknowns rather than making details up. "
                            "Return only a JSON object matching the response schema. Include: title, body, excerpt, "
                            "seo_title, slug, meta_description, focus_keyphrase, and tags. "
                            "Write a concise excerpt, a search-friendly SEO title, a short meta description, "
                            "a lowercase Latin-character slug with hyphens, one focus keyphrase, and up to five relevant tags. "
                            "Keep SEO fields in the article language except the slug."
                        )
                    }
                ]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": json.dumps(
                                {
                                    "supplied_headline_for_context_only": title,
                                    "summary": summary,
                                    "related_reports": model_reports,
                                },
                                ensure_ascii=False,
                            )
                        }
                    ],
                }
            ],
            "generationConfig": {
                "maxOutputTokens": 4096,
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {
                        "title": {"type": "STRING"},
                        "body": {"type": "STRING"},
                        "excerpt": {"type": "STRING"},
                        "seo_title": {"type": "STRING"},
                        "slug": {"type": "STRING"},
                        "meta_description": {"type": "STRING"},
                        "focus_keyphrase": {"type": "STRING"},
                        "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
                    },
                    "required": [
                        "title",
                        "body",
                        "excerpt",
                        "seo_title",
                        "slug",
                        "meta_description",
                        "focus_keyphrase",
                        "tags",
                    ],
                },
            },
        }
        for attempt in range(3):
            response = httpx.post(
                request_url,
                headers=request_headers,
                json=request_body,
                timeout=90,
            )
            try:
                response.raise_for_status()
                break
            except httpx.HTTPStatusError as exc:
                retryable_statuses = {429, 500, 502, 503, 504}
                if exc.response.status_code not in retryable_statuses or attempt == 2:
                    raise RuntimeError(_gemini_error_message(exc.response)) from exc
                time.sleep(2**attempt)

        response_payload = response.json()
        parts = response_payload["candidates"][0]["content"]["parts"]
        response_text = "".join(
            part.get("text", "") for part in parts if not part.get("thought")
        ).strip()
        if not response_text:
            raise ValueError("Gemini returned no article text.")
        article_data = json.loads(response_text)
        if not isinstance(article_data, dict):
            raise ValueError("Gemini returned an invalid article object.")

        generated_title = str(article_data.get("title") or "").strip()
        body = str(article_data.get("body") or "").strip()
        excerpt = str(article_data.get("excerpt") or "").strip()
        seo_title = str(article_data.get("seo_title") or "").strip()
        meta_description = str(article_data.get("meta_description") or "").strip()
        focus_keyphrase = str(article_data.get("focus_keyphrase") or "").strip()
        raw_slug = str(article_data.get("slug") or "").strip().lower()
        slug = re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]+", "-", raw_slug)).strip("-")
        tags = article_data.get("tags")
        if not all((generated_title, body, excerpt, seo_title, slug, meta_description, focus_keyphrase)):
            raise ValueError("Gemini omitted required article or SEO fields.")
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise ValueError("Gemini returned invalid SEO tags.")

        normalized_input_title = re.sub(r"\s+", " ", title).strip().casefold()
        normalized_generated_title = re.sub(r"\s+", " ", generated_title).strip().casefold()
        if normalized_generated_title == normalized_input_title:
            raise ValueError("Gemini did not create a new headline.")

        body = re.sub(r"https?://\S+", "", body).strip()
        body = re.sub(
            r"(?im)^\s*(?:sources?|references?|citations?|स्रोत|संदर्भ)\s*[:：].*$",
            "",
            body,
        ).strip()
        return {
            "title": generated_title,
            "summary": excerpt,
            "body": body,
            "language": language,
            "status": "draft",
            "seo_title": seo_title,
            "slug": slug,
            "meta_description": meta_description,
            "focus_keyphrase": focus_keyphrase,
            "tags": [tag.strip() for tag in tags if tag.strip()][:5],
        }
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach the Gemini API: {exc}") from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Gemini returned an invalid article response.") from exc
