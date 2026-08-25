"""Stockage SQLite : une base = un site = un écosystème versionné."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;

-- 1 ligne = 1 URL crawlée
CREATE TABLE IF NOT EXISTS pages (
    url             TEXT PRIMARY KEY,
    status          INTEGER,
    redirect_to     TEXT,
    content_type    TEXT,
    depth           INTEGER,
    discovered_via  TEXT,          -- 'sitemap' | 'link' | 'seed'
    in_sitemap      INTEGER DEFAULT 0,
    title           TEXT,
    meta_desc       TEXT,
    meta_robots     TEXT,
    canonical       TEXT,
    lang            TEXT,
    h1              TEXT,
    word_count      INTEGER,
    text            TEXT,           -- contenu principal nettoyé
    html_path       TEXT,           -- chemin du HTML brut gzippé
    rendered        INTEGER DEFAULT 0,
    jsonld          TEXT,           -- liste JSON-LD sérialisée
    hreflang        TEXT,
    load_ms         INTEGER,
    crawled_at      TEXT
);

-- structure Hn ordonnée (pour analyser les plans de page)
CREATE TABLE IF NOT EXISTS headings (
    url      TEXT,
    position INTEGER,
    level    INTEGER,
    text     TEXT
);
CREATE INDEX IF NOT EXISTS idx_headings_url ON headings(url);

-- maillage interne constaté
CREATE TABLE IF NOT EXISTS links (
    source   TEXT,
    target   TEXT,
    anchor   TEXT,
    rel      TEXT,
    zone     TEXT,       -- 'nav' | 'footer' | 'content' | 'aside'
    internal INTEGER,
    PRIMARY KEY (source, target, anchor, zone)
);
CREATE INDEX IF NOT EXISTS idx_links_target ON links(target);
CREATE INDEX IF NOT EXISTS idx_links_source ON links(source);

-- métriques de graphe (recalculées à chaque run)
CREATE TABLE IF NOT EXISTS graph_metrics (
    url        TEXT PRIMARY KEY,
    pagerank   REAL,
    inlinks    INTEGER,
    outlinks   INTEGER,
    unique_inlinks INTEGER,
    depth_click INTEGER,
    is_orphan  INTEGER
);

-- Search Console : 1 ligne = url x requête x mois
CREATE TABLE IF NOT EXISTS gsc (
    page        TEXT,
    query       TEXT,
    period      TEXT,      -- 'YYYY-MM'
    clicks      INTEGER,
    impressions INTEGER,
    ctr         REAL,
    position    REAL,
    country     TEXT,
    device      TEXT,
    PRIMARY KEY (page, query, period, country, device)
);
CREATE INDEX IF NOT EXISTS idx_gsc_page ON gsc(page);
CREATE INDEX IF NOT EXISTS idx_gsc_query ON gsc(query);

-- Exports Semrush : positions organiques du domaine
CREATE TABLE IF NOT EXISTS semrush_keywords (
    domain TEXT, page TEXT, keyword TEXT, position REAL, volume REAL,
    traffic REAL, keyword_difficulty REAL, cpc REAL, intent TEXT,
    period TEXT, PRIMARY KEY (domain, page, keyword, period)
);
CREATE INDEX IF NOT EXISTS idx_semrush_keyword ON semrush_keywords(keyword);

-- Keyword Gap : opportunités observées chez les concurrents
CREATE TABLE IF NOT EXISTS keyword_gap (
    keyword TEXT, competitor TEXT, domain_position REAL,
    competitor_position REAL, volume REAL, keyword_difficulty REAL,
    intent TEXT, status TEXT, period TEXT,
    PRIMARY KEY (keyword, competitor, period)
);

-- Backlinks Semrush : données off-site importées
CREATE TABLE IF NOT EXISTS backlinks (
    source_url TEXT, target_url TEXT, source_domain TEXT, authority_score REAL,
    anchor TEXT, follow INTEGER, first_seen TEXT, last_seen TEXT, period TEXT,
    PRIMARY KEY (source_url, target_url, anchor, period)
);

-- suggestions de maillage produites par le module sémantique
CREATE TABLE IF NOT EXISTS link_suggestions (
    source      TEXT,
    target      TEXT,
    score       REAL,
    method      TEXT,       -- 'tfidf' | 'embedding'
    anchor      TEXT,       -- ancre proposée (issue de la GSC de la cible)
    evidence    TEXT,       -- phrase source contenant l'ancre
    created_at  TEXT,
    PRIMARY KEY (source, target, method)
);

-- vocabulaire / jargon du site
CREATE TABLE IF NOT EXISTS lexicon (
    term    TEXT PRIMARY KEY,
    df      INTEGER,     -- nb de pages contenant le terme
    tfidf   REAL,
    ngram   INTEGER
);

-- vecteurs (embeddings) sérialisés
CREATE TABLE IF NOT EXISTS vectors (
    url    TEXT PRIMARY KEY,
    model  TEXT,
    dim    INTEGER,
    vec    BLOB
);

-- historique des relectures agent
CREATE TABLE IF NOT EXISTS content_reviews (
    doc_id     TEXT,
    iteration  INTEGER,
    dimension  TEXT,
    score      INTEGER,
    findings   TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_reviews_doc ON content_reviews(doc_id);

-- suivi de citation dans les moteurs génératifs (GEO)
CREATE TABLE IF NOT EXISTS ai_mentions (
    prompt     TEXT,
    engine     TEXT,      -- chatgpt | perplexity | claude | google_ai
    cited_url  TEXT,
    cited_brand INTEGER,
    rank       INTEGER,
    period     TEXT,
    raw        TEXT,
    PRIMARY KEY (prompt, engine, cited_url, period)
);
"""


class Store:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=60)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    @contextmanager
    def tx(self):
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # --- écritures -----------------------------------------------------
    def upsert_page(self, page: dict):
        cols = ",".join(page.keys())
        ph = ",".join("?" for _ in page)
        self.conn.execute(
            f"INSERT INTO pages ({cols}) VALUES ({ph}) "
            f"ON CONFLICT(url) DO UPDATE SET "
            + ",".join(f"{k}=excluded.{k}" for k in page if k != "url"),
            list(page.values()),
        )

    def insert_headings(self, url: str, headings: list[tuple[int, int, str]]):
        self.conn.execute("DELETE FROM headings WHERE url=?", (url,))
        self.conn.executemany(
            "INSERT INTO headings (url, position, level, text) VALUES (?,?,?,?)",
            [(url, p, lv, tx) for p, lv, tx in headings],
        )

    def insert_links(self, rows: list[tuple]):
        self.conn.executemany(
            "INSERT OR IGNORE INTO links (source,target,anchor,rel,zone,internal) "
            "VALUES (?,?,?,?,?,?)",
            rows,
        )

    def insert_gsc(self, rows: list[tuple]):
        self.conn.executemany(
            "INSERT INTO gsc (page,query,period,clicks,impressions,ctr,position,country,device) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(page,query,period,country,device) DO UPDATE SET "
            "clicks=excluded.clicks, impressions=excluded.impressions, "
            "ctr=excluded.ctr, position=excluded.position",
            rows,
        )

    # --- lectures ------------------------------------------------------
    def df(self, sql: str, params: tuple = ()):  # -> pandas.DataFrame
        import pandas as pd

        return pd.read_sql_query(sql, self.conn, params=params)

    def indexable_pages(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT url, title, h1, text, word_count FROM pages "
            "WHERE status=200 AND (meta_robots IS NULL OR meta_robots NOT LIKE '%noindex%') "
            "AND (canonical IS NULL OR canonical='' OR canonical=url) "
            "AND text IS NOT NULL AND length(text) > 200"
        ).fetchall()

    def json_get(self, url: str, field: str):
        row = self.conn.execute(f"SELECT {field} FROM pages WHERE url=?", (url,)).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def close(self):
        self.conn.commit()
        self.conn.close()
