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

    bg, card = (13, 17, 23), (24, 29, 38)
    accent, gold = (88, 166, 255), (240, 185, 80)
    white, muted = (235, 240, 246), (139, 148, 158)
    lang_colors = {
        "Shell": (137, 221, 255), "Python": (63, 185, 80),
        "JavaScript": (240, 220, 90), "TypeScript": (88, 166, 255),
        "Go": (0, 173, 216), "Rust": (222, 165, 132), "C++": (243, 75, 125),
    }

    rows = repos[:7]
    W, row_h = 1080, 112
    H = 250 + len(rows) * row_h + 70
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)

    d.rectangle((0, 0, W, 10), fill=accent)  # top accent bar
    d.text((60, 52), "Today's AI Digest", font=font(60, True), fill=white)
    d.text((62, 130), f"{date_iso}   |   GitHub Trending Today", font=font(27), fill=muted)
    d.line((60, 192, W - 60, 192), fill=(46, 52, 62), width=2)

    y = 224
    for i, repo in enumerate(rows, 1):
        dot = lang_colors.get(repo["lang"], muted)
        d.rounded_rectangle((60, y, W - 60, y + 94), radius=16, fill=card)
        d.ellipse((84, y + 40, 104, y + 60), fill=dot)          # language color dot
        d.text((122, y + 14), f"#{i}", font=font(24, True), fill=accent)
        d.text((176, y + 12), repo["full"], font=font(31, True), fill=white)
        d.text((122, y + 52), repo["lang"] or "-", font=font(22), fill=dot)
        tag = f"* {repo['stars_today']} today"
        tw = d.textlength(tag, font=font(26, True))
        d.text((W - 84 - tw, y + 30), tag, font=font(26, True), fill=gold)
        y += row_h

    d.line((60, y + 6, W - 60, y + 6), fill=(46, 52, 62), width=2)
    d.text((60, y + 24), "github.com/trending   +   reddit.com/r/artificial",
           font=font(22), fill=muted)

    img.save(path)
    return path


# --------------------------------------------------------------------------- #
# Message
# --------------------------------------------------------------------------- #
def build_message(repos: list[dict], posts: list[dict], date_str: str, lang: str) -> str:
    """Compose a readable Telegram HTML digest.

    Telegram only supports a small tag set (<b>, <i>, <a>, <code>,
    <blockquote>), so we lean on medals, rule lines and spacing for structure.
    """
    def esc(s: str) -> str:
        return html.escape(s, quote=False)

    ko = lang != "en"
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣"]
    div = "━" * 16
    title = "오늘의 AI 다이제스트" if ko else "Today's AI Digest"
    sub = "아침 브리핑" if ko else "morning brief"

    lines = [f"☀️ <b>{title}</b>", f"<i>{esc(date_str)} · {sub}</i>", ""]

    lines += [div, "🔥 <b>GitHub Trending</b>", ""]
    for i, r in enumerate(repos):
        badge = medals[i] if i < len(medals) else f"{i + 1}."
        name = r["full"].split("/")[-1]  # repo name only, for readability
        lines.append(f'{badge} <a href="{esc(r["url"])}"><b>{esc(name)}</b></a>')
        meta = [f'<code>{esc(r["lang"])}</code>'] if r["lang"] else []
        meta.append(f'⭐ <b>{esc(r["stars_today"])}</b>')
        lines.append("    " + "  ·  ".join(meta))
        if r["desc"]:
            lines.append(f'    └ {esc(r["desc"])}')
        lines.append("")

    lines += [div, "💬 <b>Reddit r/artificial</b>", ""]
    if posts:
        for p in posts:
            lines.append(f'▸ <a href="{esc(p["url"])}">{esc(p["title"])}</a>')
    else:
        lines.append("수집 실패 — 소스 접근 불가" if ko else "Fetch failed — source unreachable")

    note = "오늘의 한 줄" if ko else "One-liner"
    if ko:
        summary = f"오늘 GitHub 트렌딩 {len(repos)}건, r/artificial {len(posts)}건을 추렸어요."
    else:
        summary = f"Picked {len(repos)} trending repos and {len(posts)} r/artificial posts today."
    lines += ["", div, f"📌 <b>{note}</b>", f"<blockquote>{summary}</blockquote>"]
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
