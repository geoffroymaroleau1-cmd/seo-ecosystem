"""Crawler asynchrone : robots.txt -> sitemaps -> BFS sur les liens internes."""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import time
import urllib.robotparser as robotparser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import httpx

from .parser import normalize_url, parse, same_site
from .store import Store

UA = "Mozilla/5.0 (compatible; SeoEcosystemBot/0.1; +https://example.com/bot)"
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


class Crawler:
    def __init__(
        self,
        root: str,
        store: Store,
        *,
        max_pages: int = 5000,
        concurrency: int = 10,
        delay: float = 0.2,
        render_js: bool = False,
        include_subdomains: bool = False,
        respect_robots: bool = True,
        html_dir: str | Path = "data/html",
        timeout: float = 20.0,
    ):
        self.root = normalize_url(root)
        self.store = store
        self.max_pages = max_pages
        self.delay = delay
        self.render_js = render_js
        self.include_subdomains = include_subdomains
        self.respect_robots = respect_robots
        self.html_dir = Path(html_dir)
        self.html_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.sem = asyncio.Semaphore(concurrency)
        self.seen: set[str] = set()
        self.queue: asyncio.Queue = asyncio.Queue()
        self.rp: robotparser.RobotFileParser | None = None
        self.sitemap_urls: set[str] = set()
        self._browser = None

    # -- robots & sitemaps ------------------------------------------------
    async def load_robots(self, client: httpx.AsyncClient):
        url = f"{urlparse(self.root).scheme}://{urlparse(self.root).netloc}/robots.txt"
        self.rp = robotparser.RobotFileParser()
        try:
            r = await client.get(url)
            self.rp.parse(r.text.splitlines())
            for line in r.text.splitlines():
                if line.lower().startswith("sitemap:"):
                    sm = line.split(":", 1)[1].strip()
                    await self.load_sitemap(client, sm)
        except Exception as e:
            print(f"[robots] ignoré ({e})")
        if not self.sitemap_urls:
            await self.load_sitemap(client, f"{self.root.rstrip('/')}/sitemap.xml")

    async def load_sitemap(self, client: httpx.AsyncClient, url: str, depth: int = 0):
        if depth > 4:
            return
        try:
            r = await client.get(url)
            if r.status_code != 200:
                return
            content = r.content
            if url.endswith(".gz"):
                content = gzip.decompress(content)
            root = ET.fromstring(content)
        except Exception:
            return
        tag = root.tag.split("}")[-1]
        if tag == "sitemapindex":
            for loc in root.findall(".//sm:sitemap/sm:loc", SITEMAP_NS):
                await self.load_sitemap(client, loc.text.strip(), depth + 1)
        else:
            for loc in root.findall(".//sm:url/sm:loc", SITEMAP_NS):
                u = normalize_url(loc.text.strip())
                if u and same_site(u, self.root, self.include_subdomains):
                    self.sitemap_urls.add(u)
        print(f"[sitemap] {url} -> {len(self.sitemap_urls)} URLs cumulées")

    # -- crawl ------------------------------------------------------------
    def allowed(self, url: str) -> bool:
        if not self.respect_robots or self.rp is None:
            return True
        try:
            return self.rp.can_fetch(UA, url)
        except Exception:
            return True

    async def run(self):
        limits = httpx.Limits(max_connections=50, max_keepalive_connections=20)
        async with httpx.AsyncClient(
            headers={"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9"},
            follow_redirects=True, timeout=self.timeout, limits=limits, http2=True,
        ) as client:
            await self.load_robots(client)

            seeds = [(self.root, 0, "seed")] + [(u, 0, "sitemap") for u in sorted(self.sitemap_urls)]
            for u, d, src in seeds:
                if u not in self.seen:
                    self.seen.add(u)
                    self.queue.put_nowait((u, d, src))

            if self.render_js:
                await self._start_browser()

            workers = [asyncio.create_task(self._worker(client)) for _ in range(self.sem._value)]
            await self.queue.join()
            for w in workers:
                w.cancel()
            if self._browser:
                await self._browser.close()
                await self._pw.stop()
        self.store.conn.commit()
        print(f"[crawl] terminé : {len(self.seen)} URLs vues")

    async def _worker(self, client: httpx.AsyncClient):
        while True:
            url, depth, src = await self.queue.get()
            try:
                if len(self.seen) <= self.max_pages:
                    await self._fetch(client, url, depth, src)
            except Exception as e:
                print(f"[err] {url} : {type(e).__name__} {e}")
            finally:
                self.queue.task_done()

    async def _fetch(self, client: httpx.AsyncClient, url: str, depth: int, src: str):
        if not self.allowed(url):
            return
        async with self.sem:
            t0 = time.perf_counter()
            r = await client.get(url)
            await asyncio.sleep(self.delay)
        ctype = r.headers.get("content-type", "")
        final = normalize_url(str(r.url)) or url
        base = {
            "url": url,
            "status": r.status_code,
            "redirect_to": final if final != url else None,
            "content_type": ctype.split(";")[0],
            "depth": depth,
            "discovered_via": src,
            "in_sitemap": int(url in self.sitemap_urls),
            "load_ms": int((time.perf_counter() - t0) * 1000),
            "crawled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if r.status_code != 200 or "html" not in ctype:
            self.store.upsert_page(base)
            return

        html = r.text
        rendered = 0
        if self.render_js:
            html = await self._render(url) or html
            rendered = 1

        data = parse(html, final, self.root)
        path = self._save_html(final, html)
        base.update(
            {k: v for k, v in data.items() if k not in ("headings", "links", "h1_count")},
            html_path=path, rendered=rendered,
        )
        self.store.upsert_page(base)
        self.store.insert_headings(url, data["headings"])
        self.store.insert_links(data["links"])

        for _, target, *_ in data["links"]:
            if (
                same_site(target, self.root, self.include_subdomains)
                and target not in self.seen
                and len(self.seen) < self.max_pages
            ):
                self.seen.add(target)
                self.queue.put_nowait((target, depth + 1, "link"))

        if len(self.seen) % 100 == 0:
            self.store.conn.commit()
            print(f"[crawl] {len(self.seen)} URLs / file={self.queue.qsize()}")

    def _save_html(self, url: str, html: str) -> str:
        h = hashlib.sha1(url.encode()).hexdigest()[:16]
        p = self.html_dir / f"{h}.html.gz"
        p.write_bytes(gzip.compress(html.encode("utf-8", "ignore")))
        return str(p)

    # -- rendu JS (Playwright) --------------------------------------------
    async def _start_browser(self):
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)

    async def _render(self, url: str) -> str | None:
        try:
            page = await self._browser.new_page(user_agent=UA)
            await page.goto(url, wait_until="networkidle", timeout=30000)
            html = await page.content()
            await page.close()
            return html
        except Exception as e:
            print(f"[render] {url} : {e}")
            return None


def crawl(root: str, db: str, **kw):
    store = Store(db)
    asyncio.run(Crawler(root, store, **kw).run())
    store.close()
