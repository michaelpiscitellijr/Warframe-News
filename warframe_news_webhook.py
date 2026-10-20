#!/usr/bin/env python3
"""Post new Warframe news (https://www.warframe.com/en/news) to a Discord webhook.

Setup:
    pip install requests beautifulsoup4
    export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/<id>/<token>"
    python warframe_news_webhook.py

Run it on a schedule (cron, Task Scheduler, GitHub Actions). It remembers what it
has already posted in seen_warframe.json, so each article is only posted once.
The first run only records the current articles (no posting) so you don't get
a flood of old news; use --post-existing to override that.
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

NEWS_URL = "https://www.warframe.com/en/news"
STATE_FILE = Path(__file__).with_name("seen_warframe.json")
EMBED_COLOR = 0x3B82F6
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; GameNewsWebhook/1.0)"}
DATE_RE = re.compile(r"Posted On\s+(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}:\d{2})?)")


def fetch_articles():
    """Scrape the news page. Returns a list of dicts, newest first."""
    resp = requests.get(NEWS_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    articles, seen_urls = [], set()
    # Each article has a "Posted On <date>" line; anchor on that and walk up
    # to the container that also holds the title link and blurb.
    for node in soup.find_all(string=DATE_RE):
        date = DATE_RE.search(node).group(1)
        container = node.parent
        link = None
        for _ in range(5):  # climb a few levels looking for the title link
            if container is None:
                break
            for a in container.find_all("a", href=True):
                text = a.get_text(strip=True)
                if text and text.lower() != "read more":
                    link = a
                    break
            if link:
                break
            container = container.parent
        if not link:
            continue

        url = urljoin(NEWS_URL, link["href"])
        if url in seen_urls:
            continue
        seen_urls.add(url)

        # Blurb = the container's text minus title, date line and "Read More"
        parts = [
            s.strip()
            for s in container.stripped_strings
            if s.strip() and not DATE_RE.search(s) and s.strip().lower() != "read more"
            and s.strip() != link.get_text(strip=True)
        ]
        articles.append({
            "title": link.get_text(strip=True),
            "url": url,
            "date": date,
            "summary": " ".join(parts),
            "image": listing_image(container),
        })
    return articles


def listing_image(container):
    """Look for an image in the news-list card (img tag, lazy-load attrs, or CSS background)."""
    img = container.find("img")
    if img:
        for attr in ("src", "data-src", "data-lazy-src", "data-original"):
            if img.get(attr) and not img[attr].startswith("data:"):
                return urljoin(NEWS_URL, img[attr])
    for el in container.find_all(style=True):
        m = re.search(r"url\(['\"]?([^'\")]+)", el["style"])
        if m:
            return urljoin(NEWS_URL, m.group(1))
    return None


def article_image(url):
    """Fallback: the article page's og:image (the preview image Discord/Twitter use)."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        tag = BeautifulSoup(r.text, "html.parser").find("meta", property="og:image")
        if tag and tag.get("content"):
            return urljoin(url, tag["content"])
    except requests.RequestException:
        pass
    return None


def load_seen():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return None  # None = first run


def save_seen(urls):
    STATE_FILE.write_text(json.dumps(sorted(urls), indent=2))


def post_to_discord(webhook_url, article):
    if not article["image"]:
        article["image"] = article_image(article["url"])
    embed = {
        "title": article["title"][:256],
        "url": article["url"],
        "description": article["summary"][:2000],
        "color": EMBED_COLOR,
        "footer": {"text": f"Warframe News • {article['date']}"},
    }
    if article["image"] and article["image"].startswith("http"):
        embed["image"] = {"url": article["image"]}

    while True:
        r = requests.post(webhook_url, json={"username": "Warframe News", "embeds": [embed]}, timeout=30)
        if r.status_code == 429:  # rate limited: wait and retry
            time.sleep(float(r.json().get("retry_after", 2)) + 0.5)
            continue
        r.raise_for_status()
        return


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--post-existing", action="store_true",
                        help="on first run, post current articles instead of just recording them")
    parser.add_argument("--latest", type=int, metavar="N",
                        help="post the newest N articles right now, even if already posted")
    args = parser.parse_args()

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        sys.exit("Set the DISCORD_WEBHOOK_URL environment variable.")

    articles = fetch_articles()
    if not articles:
        sys.exit("No articles found - the page layout may have changed.")

    seen = load_seen()
    first_run = seen is None
    seen = seen or set()

    if args.latest:
        # Manual mode: newest N (page lists newest first), posted oldest first
        new = list(reversed(articles[: args.latest]))
    elif first_run and not args.post_existing:
        print("First run: recording existing articles without posting.")
        new = []
    else:
        new = list(reversed([a for a in articles if a["url"] not in seen]))

    for article in new:
        post_to_discord(webhook_url, article)
        seen.add(article["url"])
        print("Posted:", article["title"])
        time.sleep(1)

    seen.update(a["url"] for a in articles)
    save_seen(seen)


if __name__ == "__main__":
    main()
