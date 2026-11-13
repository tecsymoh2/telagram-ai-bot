import html
import ipaddress
import re
import socket
import urllib.parse

import httpx


class FetchError(Exception):
    pass


def _check(url):
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise FetchError("Please send a valid http(s) link.")
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except OSError:
        raise FetchError("I couldn't resolve that address.")
    for i in infos:
        try:
            ip = ipaddress.ip_address(i[4][0].split("%")[0])
        except ValueError:
            raise FetchError("That address isn't allowed.")
        if not ip.is_global:
            raise FetchError("That address isn't allowed.")


def fetch_text(url, limit=12000):
    cur = url
    with httpx.Client(timeout=15, follow_redirects=False,
                      headers={"User-Agent": "Mozilla/5.0 (compatible; TelegramAIBot)"}) as cl:
        for _ in range(4):
            _check(cur)
            r = cl.get(cur)
            if r.is_redirect and r.headers.get("location"):
                cur = urllib.parse.urljoin(cur, r.headers["location"])
                continue
            break
        else:
            raise FetchError("Too many redirects.")
    if r.status_code >= 400:
        raise FetchError(f"The site returned error {r.status_code}.")
    ctype = r.headers.get("content-type", "")
    if not any(t in ctype for t in ("html", "text", "json")):
        raise FetchError("That link isn't a web page I can read.")
    body = r.text[:1_500_000]
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", body)
    title = html.unescape(m.group(1)).strip() if m else ""
    body = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", body)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    body = html.unescape(re.sub(r"\s+", " ", body)).strip()
    if not body:
        raise FetchError("I couldn't find readable text on that page.")
    return (f"Title: {title}\n\n" if title else "") + body[:limit]
