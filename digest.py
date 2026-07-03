#!/usr/bin/env python3
"""
AI News Digest Bot
==================
Fetches the day's most interesting AI/dev news from GitHub Trending and
Reddit r/artificial, renders a summary card image, and delivers a clean
digest to your Telegram — every morning.

Usage:
    export BOT_TOKEN="123456:ABC..."   # from @BotFather
    export CHAT_ID="7518530902"        # your Telegram chat id
    python digest.py

Config via environment variables (see .env.example):
    BOT_TOKEN   (required)  Telegram bot token
    CHAT_ID     (required)  Target chat id
    LANG        (optional)  "ko" (default) or "en" for the digest body
"""
from __future__ import annotations

import html
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")
TIMEOUT = 20


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #
def fetch_github_trending(limit: int = 7) -> list[dict]:
    """Scrape https://github.com/trending (daily) for the top repositories."""
    url = "https://github.com/trending?since=daily"
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    html_text = r.text

    repos: list[dict] = []
    # Each repo row is an <article class="Box-row"> block.
    for block in re.split(r'<article class="Box-row">', html_text)[1:]:
        m = re.search(r'href="/([^"/]+)/([^"]+)"', block)
        if not m:
            continue
        owner, name = m.group(1), m.group(2).split('"')[0]
        full = f"{owner}/{name}"

        desc_m = re.search(r'<p class="col-9[^"]*">\s*(.*?)\s*</p>', block, re.S)
        desc = re.sub(r"<[^>]+>", "", desc_m.group(1)).strip() if desc_m else ""
        desc = html.unescape(re.sub(r"\s+", " ", desc))

        lang_m = re.search(r'itemprop="programmingLanguage">([^<]+)<', block)
        lang = lang_m.group(1).strip() if lang_m else ""

        star_m = re.search(r'([\d,]+)\s*stars today', block)
        stars_today = star_m.group(1) if star_m else "?"

        repos.append({
            "full": full,
            "url": f"https://github.com/{full}",
            "desc": desc,
            "lang": lang,
            "stars_today": stars_today,
        })
        if len(repos) >= limit:
            break
    return repos


def fetch_reddit(sub: str = "artificial", limit: int = 6) -> list[dict]:
    """Fetch a subreddit's hot posts via the Atom RSS feed.

    Reddit's JSON API is often blocked for non-browser clients, but the
    public ``.rss`` feed is reliably reachable. RSS does not expose upvote
    counts, so we only report titles + links (never fabricate numbers).
    """
    url = f"https://www.reddit.com/r/{sub}/.rss?limit=25"
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()

    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(r.content)
    posts: list[dict] = []
    for entry in root.findall("a:entry", ns):
        title_el = entry.find("a:title", ns)
        link_el = entry.find("a:link", ns)
        if title_el is None or link_el is None:
            continue
        posts.append({
            "title": (title_el.text or "").strip(),
            "url": link_el.get("href", ""),
        })
        if len(posts) >= limit:
            break
    return posts


# --------------------------------------------------------------------------- #
# Card image (ASCII only — portable across fonts)
# --------------------------------------------------------------------------- #
def build_card(repos: list[dict], date_iso: str, path: str = "/tmp/digest_card.png") -> str | None:
    """Render a dark summary card. Text is ASCII-only so it renders on any
    system font (Korean/emoji glyphs are intentionally avoided — they show
    as tofu boxes without the right font)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None

    def font(size: int, bold: bool = False):
        candidates = [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold
            else "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
        for c in candidates:
            if os.path.exists(c):
                return ImageFont.truetype(c, size)
        return ImageFont.load_default()

    bg, card = (13, 17, 23), (22, 27, 34)
    accent, green = (88, 166, 255), (63, 185, 80)
    white, muted, gold = (230, 237, 243), (139, 148, 158), (210, 180, 90)

    rows = repos[:7]
    W = 1080
    H = 300 + len(rows) * 108
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)

    d.text((60, 58), "Today's AI Digest", font=font(58, True), fill=white)
    d.text((62, 132), f"{date_iso}  -  GitHub Trending Today", font=font(28), fill=muted)
    d.line((60, 190, W - 60, 190), fill=(48, 54, 61), width=2)
    d.text((60, 214), "GitHub Trending", font=font(34, True), fill=accent)

    y = 272
    for repo in rows:
        d.rounded_rectangle((60, y, W - 60, y + 94), radius=14, fill=card)
        d.text((84, y + 16), repo["full"], font=font(30, True), fill=white)
        d.text((84, y + 56), repo["lang"] or "-", font=font(23), fill=green)
        tag = f"* {repo['stars_today']} today"
        tw = d.textlength(tag, font=font(25))
        d.text((W - 84 - tw, y + 52), tag, font=font(25), fill=gold)
        y += 108

    img.save(path)
    return path


# --------------------------------------------------------------------------- #
# Message
# --------------------------------------------------------------------------- #
def build_message(repos: list[dict], posts: list[dict], date_str: str, lang: str) -> str:
    def esc(s: str) -> str:
        return html.escape(s, quote=False)

    if lang == "en":
        h_gh, h_rd, h_note = "GitHub Trending", "Reddit r/artificial", "One-liner"
    else:
        h_gh, h_rd, h_note = "GitHub Trending", "Reddit r/artificial", "오늘의 한 줄"

    lines = [f"<b>☀️ Today's AI Digest — {esc(date_str)}</b>", ""]

    lines.append(f"<b>🔥 {h_gh}</b>")
    for i, r in enumerate(repos, 1):
        meta = " · ".join(x for x in [r["lang"], f"★{r['stars_today']}"] if x)
        lines.append(f'{i}. <a href="{esc(r["url"])}">{esc(r["full"])}</a> · {meta}')
        if r["desc"]:
            lines.append(esc(r["desc"]))
    lines.append("")

    lines.append(f"<b>💬 {h_rd}</b>")
    if posts:
        for p in posts:
            lines.append(f'• <a href="{esc(p["url"])}">{esc(p["title"])}</a>')
    else:
        lines.append("수집 실패 — 소스 접근 불가" if lang != "en" else "Fetch failed — source unreachable")
    lines.append("")

    lines.append(f"<b>📌 {h_note}</b>")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Telegram delivery
# --------------------------------------------------------------------------- #
def send_photo(token: str, chat_id: str, path: str, caption: str) -> bool:
    with open(path, "rb") as fh:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={"chat_id": chat_id, "caption": caption},
            files={"photo": fh}, timeout=TIMEOUT,
        )
    return r.ok and r.json().get("ok", False)


def send_message(token: str, chat_id: str, text: str) -> bool:
    payload = {
        "chat_id": chat_id, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": "true",
    }
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      data=payload, timeout=TIMEOUT)
    if r.ok and r.json().get("ok"):
        return True
    # Retry as plain text if HTML parsing failed.
    payload.pop("parse_mode")
    payload["text"] = re.sub(r"<[^>]+>", "", text)
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      data=payload, timeout=TIMEOUT)
    return r.ok and r.json().get("ok", False)


# --------------------------------------------------------------------------- #
def main() -> int:
    token = os.environ.get("BOT_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    lang = os.environ.get("LANG_DIGEST", "ko")
    if not token or not chat_id:
        print("ERROR: set BOT_TOKEN and CHAT_ID env vars.", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    date_iso = now.strftime("%Y-%m-%d")
    date_str = now.strftime("%Y-%m-%d")

    try:
        repos = fetch_github_trending()
    except Exception as e:  # noqa: BLE001
        print(f"github trending failed: {e}", file=sys.stderr)
        repos = []

    try:
        posts = fetch_reddit()
    except Exception as e:  # noqa: BLE001
        print(f"reddit failed: {e}", file=sys.stderr)
        posts = []

    if not repos and not posts:
        print("no sources available; aborting", file=sys.stderr)
        return 2

    card = build_card(repos, date_iso) if repos else None
    if card:
        send_photo(token, chat_id, card, f"☀️ Today's AI Digest — {date_str}")

    ok = send_message(token, chat_id, build_message(repos, posts, date_str, lang))
    print(f"done: {len(repos)} repos, {len(posts)} reddit posts, sent={ok}")
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
