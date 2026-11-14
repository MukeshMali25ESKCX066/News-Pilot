import re
from html import escape
from urllib.parse import urlparse

import httpx

from ..config import WORDPRESS_CONNECTOR_TOKEN, WORDPRESS_SITE_URL


def get_wordpress_status() -> dict:
    configured = all((WORDPRESS_SITE_URL, WORDPRESS_CONNECTOR_TOKEN))
    return {
        "configured": configured,
        "site_url": WORDPRESS_SITE_URL if configured else None,
    }


def check_wordpress_connection() -> dict:
    status = get_wordpress_status()
    if not status["configured"]:
        return {
            **status,
            "connected": False,
            "error": (
                "Set WORDPRESS_SITE_URL and WORDPRESS_CONNECTOR_TOKEN in .env, "
                "then restart NewsPilot."
            ),
        }

    try:
        response = httpx.get(
            f"{WORDPRESS_SITE_URL}/wp-json/newspilot/v1/status",
            headers={"X-NewsPilot-Token": WORDPRESS_CONNECTOR_TOKEN},
            timeout=10,
        )
        _raise_for_wordpress_error(response)
        plugin_status = response.json()
    except RuntimeError as exc:
        return {**status, "connected": False, "error": str(exc)}
    except httpx.HTTPError as exc:
        return {
            **status,
            "connected": False,
            "error": f"Could not reach the NewsPilot WordPress plugin: {exc}",
        }
    except ValueError:
        return {
            **status,
            "connected": False,
            "error": "The NewsPilot WordPress plugin returned an invalid response.",
        }

    if not isinstance(plugin_status, dict) or plugin_status.get("connected") is not True:
        return {
            **status,
            "connected": False,
            "error": "The NewsPilot WordPress plugin did not confirm the connection.",
        }
    return {**status, "connected": True}


def text_to_wordpress_html(body: str) -> str:
    normalized_body = re.sub(r"\r\n?", "\n", body.strip())
    normalized_body = re.sub(r"\s+(?=#{2,3}\s)", "\n\n", normalized_body)
    blocks = re.split(r"\n\s*\n", normalized_body)
    formatted = []
    for block in blocks:
        lines = block.splitlines()
        if not lines:
            continue
        if lines[0].startswith("## "):
            formatted.append(f"<h2>{escape(lines[0][3:].strip())}</h2>")
            lines = lines[1:]
            if not lines:
                continue
        elif lines[0].startswith("### "):
            formatted.append(f"<h3>{escape(lines[0][4:].strip())}</h3>")
            lines = lines[1:]
            if not lines:
                continue

        if all(line.startswith(("- ", "* ")) for line in lines):
            items = "".join(f"<li>{escape(line[2:].strip())}</li>" for line in lines)
            formatted.append(f"<ul>{items}</ul>")
        else:
            text = "<br>\n".join(escape(line) for line in lines)
            text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
            formatted.append(f"<p>{text}</p>")
    return "\n".join(formatted)


def _raise_for_wordpress_error(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        try:
            error = exc.response.json()
        except ValueError:
            error = {}
        message = error.get("message") if isinstance(error, dict) else None
        detail = message or "No error details provided."
        raise RuntimeError(
            f"WordPress API returned HTTP {exc.response.status_code}: {detail}"
        ) from exc


def push_article_to_wordpress(
    title: str,
    body: str,
    summary: str,
    seo_title: str,
    slug: str,
    meta_description: str,
    focus_keyphrase: str,
    tags: list[str],
    existing_post_id: int | None = None,
    publish: bool = False,
) -> dict:
    if not get_wordpress_status()["configured"]:
        raise RuntimeError(
            "WordPress is not connected. Set WORDPRESS_SITE_URL and "
            "WORDPRESS_CONNECTOR_TOKEN in .env, then restart NewsPilot."
        )

    parsed_url = urlparse(WORDPRESS_SITE_URL)
    if parsed_url.scheme != "https" and parsed_url.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("The WordPress site URL must use HTTPS.")

    post_url = f"{WORDPRESS_SITE_URL}/wp-json/newspilot/v1/posts"
    payload = {
        "title": title,
        "slug": slug,
        "content": text_to_wordpress_html(body),
        "excerpt": summary,
        "newspilot_yoast": {
            "seo_title": seo_title,
            "meta_description": meta_description,
            "focus_keyphrase": focus_keyphrase,
        },
    }
    if tags:
        payload["tags"] = tags
    if existing_post_id is None:
        payload["status"] = "publish" if publish else "draft"
    else:
        payload["existing_post_id"] = existing_post_id
        if publish:
            payload["status"] = "publish"

    try:
        response = httpx.post(
            post_url,
            headers={"X-NewsPilot-Token": WORDPRESS_CONNECTOR_TOKEN},
            json=payload,
            timeout=30,
        )
        _raise_for_wordpress_error(response)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach the WordPress REST API: {exc}") from exc

    try:
        post = response.json()
        post_id = post["id"]
        if not isinstance(post_id, int) or post_id <= 0:
            raise ValueError("Invalid WordPress post ID.")
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("WordPress returned an invalid post response.") from exc

    return {
        "id": post_id,
        "edit_url": (
            f"{WORDPRESS_SITE_URL}/wp-admin/post.php"
            f"?post={post_id}&action=edit"
        ),
        "status": "updated" if existing_post_id is not None else "created",
        "wordpress_status": post.get("status") or ("publish" if publish else "draft"),
    }
