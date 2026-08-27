"""
Scrape preview publik Telegram (t.me/s/<username>) — gak butuh bot admin atau
API key, kerja buat channel PUBLIC apapun walau kita cuma subscriber biasa.
Dipake scheduler.py buat channel yang gak bisa dijadiin admin bot (kayak
channel sekuritas orang lain, bukan channel milik sendiri).
"""
import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def fetch_channel_posts(username: str) -> list[dict]:
    """[{post_id: int, text: str}, ...] — post terbaru yang tampil di halaman
    preview (biasanya ~20 post terakhir)."""
    res = requests.get(f"https://t.me/s/{username}", headers=HEADERS, timeout=15)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")

    posts = []
    for wrap in soup.select(".tgme_widget_message_wrap"):
        post_el = wrap.select_one("[data-post]")
        if not post_el:
            continue
        post_id = int(post_el["data-post"].split("/")[-1])
        text_el = wrap.select_one(".tgme_widget_message_text")
        text = text_el.get_text("\n").strip() if text_el else ""
        if text:
            posts.append({"post_id": post_id, "text": text})
    return posts
