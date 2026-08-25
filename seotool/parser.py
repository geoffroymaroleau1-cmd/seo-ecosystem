"""Extraction structurée d'une page HTML (selectolax, fallback BeautifulSoup)."""
from __future__ import annotations

import json
import re
from urllib.parse import urldefrag, urljoin, urlparse

try:
    from selectolax.parser import HTMLParser

    _ENGINE = "selectolax"
except ImportError:  # pragma: no cover
    from bs4 import BeautifulSoup

    _ENGINE = "bs4"

BOILERPLATE = ("script", "style", "noscript", "svg", "template", "iframe")
ZONE_TAGS = {"nav": "nav", "header": "nav", "footer": "footer", "aside": "aside"}
TRACKING_PARAMS = re.compile(r"^(utm_|gclid|fbclid|mc_cid|mc_eid|_ga|msclkid)", re.I)


def normalize_url(url: str, base: str | None = None, strip_params: bool = True) -> str | None:
    """Canonicalise une URL : absolue, sans fragment, sans paramètres de tracking."""
    if not url:
        return None
    url = url.strip()
    if url.startswith(("mailto:", "tel:", "javascript:", "#", "data:")):
        return None
    if base:
        url = urljoin(base, url)
    url, _ = urldefrag(url)
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return None
    if strip_params and p.query:
        keep = [kv for kv in p.query.split("&") if kv and not TRACKING_PARAMS.match(kv.split("=")[0])]
        p = p._replace(query="&".join(sorted(keep)))
    netloc = p.netloc.lower().replace(":80", "").replace(":443", "")
    path = re.sub(r"/{2,}", "/", p.path) or "/"
    return p._replace(netloc=netloc, path=path).geturl()


def same_site(url: str, root: str, include_subdomains: bool = True) -> bool:
    a, b = urlparse(url).netloc.lower(), urlparse(root).netloc.lower()
    a, b = a.removeprefix("www."), b.removeprefix("www.")
    return a == b or (include_subdomains and a.endswith("." + b))


def parse(html: str, url: str, root: str) -> dict:
    if _ENGINE == "selectolax":
        return _parse_selectolax(html, url, root)
    return _parse_bs4(html, url, root)


# --------------------------------------------------------------------------
def _parse_selectolax(html: str, url: str, root: str) -> dict:
    tree = HTMLParser(html)
    head = tree.head

    def meta(name=None, prop=None):
        sel = f'meta[name="{name}"]' if name else f'meta[property="{prop}"]'
        node = tree.css_first(sel)
        return node.attributes.get("content", "").strip() if node else None

    title = tree.css_first("title")
    canonical = tree.css_first('link[rel="canonical"]')
    html_tag = tree.css_first("html")

    hreflang = [
        {"lang": n.attributes.get("hreflang"), "href": n.attributes.get("href")}
        for n in tree.css('link[rel="alternate"][hreflang]')
    ]

    jsonld = []
    for n in tree.css('script[type="application/ld+json"]'):
        try:
            jsonld.append(json.loads(n.text()))
        except Exception:
            pass

    # zones : on tague les liens selon leur ancêtre structurel
    links, headings = [], []
    for node in tree.css("a[href]"):
        href = normalize_url(node.attributes.get("href"), base=url)
        if not href:
            continue
        anchor = " ".join(node.text().split())[:200]
        rel = node.attributes.get("rel", "") or ""
        links.append((url, href, anchor, rel, _zone(node), int(same_site(href, root))))

    for i, node in enumerate(tree.css("h1,h2,h3,h4,h5,h6")):
        txt = " ".join(node.text().split())
        if txt:
            headings.append((i, int(node.tag[1]), txt[:300]))

    # texte principal : on retire boilerplate + nav/footer/aside
    for tag in BOILERPLATE + ("nav", "footer", "aside", "form"):
        for n in tree.css(tag):
            n.decompose()
    main = tree.css_first("main") or tree.css_first("article") or tree.body
    text = " ".join((main.text() if main else "").split())

    h1s = [h[2] for h in headings if h[1] == 1]
    return {
        "title": title.text().strip() if title else None,
        "meta_desc": meta(name="description"),
        "meta_robots": meta(name="robots"),
        "canonical": normalize_url(canonical.attributes.get("href"), base=url) if canonical else None,
        "lang": html_tag.attributes.get("lang") if html_tag else None,
        "h1": h1s[0] if h1s else None,
        "h1_count": len(h1s),
        "word_count": len(text.split()),
        "text": text[:200_000],
        "jsonld": json.dumps(jsonld, ensure_ascii=False) if jsonld else None,
        "hreflang": json.dumps(hreflang, ensure_ascii=False) if hreflang else None,
        "headings": headings,
        "links": links,
    }


def _zone(node) -> str:
    cur, hops = node.parent, 0
    while cur is not None and hops < 25:
        if cur.tag in ZONE_TAGS:
            return ZONE_TAGS[cur.tag]
        cls = (cur.attributes.get("class") or "").lower()
        if any(k in cls for k in ("nav", "menu", "breadcrumb")):
            return "nav"
        if "footer" in cls:
            return "footer"
        cur, hops = cur.parent, hops + 1
    return "content"


def _parse_bs4(html: str, url: str, root: str) -> dict:  # pragma: no cover
    soup = BeautifulSoup(html, "lxml")
    for t in soup(list(BOILERPLATE)):
        t.decompose()
    links = []
    for a in soup.select("a[href]"):
        href = normalize_url(a.get("href"), base=url)
        if href:
            links.append(
                (url, href, " ".join(a.get_text().split())[:200],
                 " ".join(a.get("rel") or []), "content", int(same_site(href, root)))
            )
    headings = [
        (i, int(h.name[1]), " ".join(h.get_text().split())[:300])
        for i, h in enumerate(soup.select("h1,h2,h3,h4,h5,h6"))
    ]
    text = " ".join(soup.get_text(" ").split())
    md = soup.find("meta", attrs={"name": "description"})
    rb = soup.find("meta", attrs={"name": "robots"})
    can = soup.find("link", attrs={"rel": "canonical"})
    h1s = [h[2] for h in headings if h[1] == 1]
    return {
        "title": soup.title.get_text().strip() if soup.title else None,
        "meta_desc": md.get("content") if md else None,
        "meta_robots": rb.get("content") if rb else None,
        "canonical": normalize_url(can.get("href"), base=url) if can else None,
        "lang": (soup.html.get("lang") if soup.html else None),
        "h1": h1s[0] if h1s else None,
        "h1_count": len(h1s),
        "word_count": len(text.split()),
        "text": text[:200_000],
        "jsonld": None,
        "hreflang": None,
        "headings": headings,
        "links": links,
    }
