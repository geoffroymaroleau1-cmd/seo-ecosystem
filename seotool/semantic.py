"""Sémantique : jargon du site, similarité TF-IDF / embeddings, suggestions de maillage."""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from math import log1p
from urllib.parse import urlparse

import numpy as np

from .store import Store

FR_STOPWORDS = """au aux avec ce ces dans de des du elle en et eux il je la le les leur lui ma mais me
même mes moi mon ne nos notre nous on ou par pas pour qu que qui sa se ses son sur ta te tes toi ton tu
un une vos votre vous c d j l à m n s t y été étée étées étés étant suis es est sommes êtes sont serai
seras sera serons serez seront avoir ai as avons avez ont plus tout tous toute toutes cette cet aussi
comme si sans sous entre chez alors donc car dont où quel quelle quels quelles très peu bien être avoir
faire plusieurs autre autres notamment ainsi afin lors depuis vers chaque nôtre leurs vos""".split()


def _corpus(store: Store):
    rows = store.indexable_pages()
    urls = [r["url"] for r in rows]
    docs = [
        f"{r['title'] or ''}. {r['h1'] or ''}. {r['text'] or ''}"
        for r in rows
    ]
    return urls, docs


# --- jargon / vocabulaire métier -----------------------------------------
def build_lexicon(store: Store, top: int = 500, ngram=(1, 3)):
    from sklearn.feature_extraction.text import TfidfVectorizer

    urls, docs = _corpus(store)
    vec = TfidfVectorizer(
        stop_words=FR_STOPWORDS, ngram_range=ngram, min_df=3,
        max_df=0.5, sublinear_tf=True, token_pattern=r"(?u)\b[a-zà-ÿ][a-zà-ÿ\-']{2,}\b",
        lowercase=True,
    )
    X = vec.fit_transform(docs)
    terms = np.array(vec.get_feature_names_out())
    scores = np.asarray(X.mean(axis=0)).ravel()
    df = np.asarray((X > 0).sum(axis=0)).ravel()
    order = scores.argsort()[::-1][:top]
    rows = [(terms[i], int(df[i]), float(scores[i]), len(terms[i].split())) for i in order]
    with store.tx() as c:
        c.execute("DELETE FROM lexicon")
        c.executemany("INSERT INTO lexicon (term,df,tfidf,ngram) VALUES (?,?,?,?)", rows)
    return rows


# --- vecteurs -------------------------------------------------------------
def embed(store: Store, model_name: str = "intfloat/multilingual-e5-base", batch: int = 32):
    """Embeddings multilingues (CPU-friendly). Alternative : voyage-3 / OpenAI via API."""
    from sentence_transformers import SentenceTransformer

    urls, docs = _corpus(store)
    model = SentenceTransformer(model_name)
    docs = [f"passage: {d[:4000]}" for d in docs]  # e5 attend un préfixe
    V = model.encode(docs, batch_size=batch, normalize_embeddings=True, show_progress_bar=True)
    with store.tx() as c:
        c.execute("DELETE FROM vectors")
        c.executemany(
            "INSERT INTO vectors (url,model,dim,vec) VALUES (?,?,?,?)",
            [(u, model_name, V.shape[1], V[i].astype("float32").tobytes()) for i, u in enumerate(urls)],
        )
    return len(urls)


def load_vectors(store: Store):
    rows = store.conn.execute("SELECT url, dim, vec FROM vectors").fetchall()
    if not rows:
        return [], None
    urls = [r["url"] for r in rows]
    V = np.vstack([np.frombuffer(r["vec"], dtype="float32") for r in rows])
    return urls, V


def similarity_matrix(store: Store, method: str = "tfidf"):
    if method == "embedding":
        urls, V = load_vectors(store)
        if V is None:
            raise RuntimeError("Aucun vecteur : lancer `embed` d'abord.")
        return urls, V @ V.T
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    urls, docs = _corpus(store)
    X = TfidfVectorizer(stop_words=FR_STOPWORDS, ngram_range=(1, 2), min_df=2,
                        max_df=0.6, sublinear_tf=True).fit_transform(docs)
    return urls, cosine_similarity(X)


# --- suggestions de maillage ---------------------------------------------
def suggest_links(store: Store, method: str = "tfidf", top_k: int = 5,
                  min_score: float = 0.25, respect_hierarchy: bool = True):
    """Pour chaque page, propose des liens sortants vers des pages proches
    sémantiquement mais non encore liées.

    Le score combine proximité sémantique, demande GSC de la cible et besoin
    de renforcement interne. La GSC fournit l'ancre seulement si elle est
    exploitable ; le H1/title de la cible sert sinon de repli.
    """
    urls, S = similarity_matrix(store, method)
    idx = {u: i for i, u in enumerate(urls)}
    existing = {
        (s, t) for s, t in store.conn.execute(
            "SELECT source, target FROM links WHERE internal=1 AND zone='content'")
    }
    pr = {r[0]: r[1] for r in store.conn.execute("SELECT url, pagerank FROM graph_metrics")}
    structure = {
        r["url"]: r for r in store.conn.execute(
            "SELECT url, unique_inlinks, depth_click, is_orphan FROM graph_metrics"
        )
    }
    gsc_metrics = _gsc_page_metrics(store)
    max_impressions = max((m["impressions"] for m in gsc_metrics.values()), default=0)
    max_clicks = max((m["clicks"] for m in gsc_metrics.values()), default=0)
    max_inlinks = max((r["unique_inlinks"] for r in structure.values()), default=0)
    texts = {r["url"]: (r["text"] or "").lower() for r in store.indexable_pages()}

    suggestions = []
    for i, src in enumerate(urls):
        order = np.argsort(S[i])[::-1]
        picked = 0
        for j in order:
            if picked >= top_k:
                break
            tgt = urls[j]
            if tgt == src or S[i, j] < min_score or (src, tgt) in existing:
                continue
            # on privilégie les liens depuis une page forte vers une page faible
            if respect_hierarchy and pr.get(src, 0) < pr.get(tgt, 0):
                continue
            final_score = _link_score(
                float(S[i, j]), gsc_metrics.get(tgt), structure.get(tgt),
                max_impressions=max_impressions, max_clicks=max_clicks,
                max_inlinks=max_inlinks,
            )
            anchor, evidence = best_anchor(store, tgt, texts.get(src, ""))
            suggestions.append((src, tgt, final_score, method, anchor, evidence,
                                date.today().isoformat()))
            picked += 1

    with store.tx() as c:
        c.execute("DELETE FROM link_suggestions WHERE method=?", (method,))
        c.executemany(
            "INSERT OR REPLACE INTO link_suggestions "
            "(source,target,score,method,anchor,evidence,created_at) VALUES (?,?,?,?,?,?,?)",
            suggestions,
        )
    return len(suggestions)


def _gsc_page_metrics(store: Store) -> dict[str, dict[str, float]]:
    rows = store.conn.execute(
        """SELECT page, SUM(impressions) impressions, SUM(clicks) clicks,
                  CASE WHEN SUM(impressions)>0
                       THEN SUM(position*impressions)/SUM(impressions)
                       ELSE 0 END position
           FROM gsc GROUP BY page"""
    ).fetchall()
    return {
        r["page"]: {"impressions": float(r["impressions"] or 0),
                    "clicks": float(r["clicks"] or 0),
                    "position": float(r["position"] or 0)}
        for r in rows
    }


def _link_score(similarity: float, gsc: dict | None, structure,
                *, max_impressions: float, max_clicks: float,
                max_inlinks: int) -> float:
    """Score 0..1 : 65 % sémantique, 25 % GSC, 10 % structure."""
    gsc = gsc or {}
    impressions = float(gsc.get("impressions", 0))
    clicks = float(gsc.get("clicks", 0))
    position = float(gsc.get("position", 0))
    demand = log1p(impressions) / log1p(max_impressions) if max_impressions else 0.0
    traffic = log1p(clicks) / log1p(max_clicks) if max_clicks else 0.0
    opportunity = 1.0 if 4 <= position <= 20 else (0.35 if position > 20 else 0.15)
    gsc_score = 0.5 * demand + 0.3 * traffic + 0.2 * opportunity if gsc else 0.0

    structural_need = 0.0
    if structure is not None:
        inlinks = int(structure["unique_inlinks"] or 0)
        weak_inlinks = 1 - (inlinks / max_inlinks) if max_inlinks else 1.0
        depth = int(structure["depth_click"] or 0)
        deep = min(max(depth, 0) / 4, 1.0)
        structural_need = max(weak_inlinks, deep, float(structure["is_orphan"] or 0))
    return round(min(1.0, 0.65 * similarity + 0.25 * gsc_score + 0.10 * structural_need), 6)


def best_anchor(store: Store, target: str, source_text: str) -> tuple[str | None, str | None]:
    """Choisit une requête GSC non générique déjà présente dans la source."""
    rows = store.conn.execute(
        """SELECT query, SUM(impressions) impressions, SUM(clicks) clicks,
                  AVG(position) position
           FROM gsc WHERE page = ? GROUP BY query
           ORDER BY impressions DESC LIMIT 100""", (target,)
    ).fetchall()
    candidates = [r for r in rows if _usable_anchor(r["query"], target)]
    candidates.sort(key=_anchor_priority, reverse=True)
    for r in candidates:
        q = r["query"].lower().strip()
        if q in source_text:
            m = re.search(r"[^.]{0,120}" + re.escape(q) + r"[^.]{0,120}", source_text)
            return r["query"], (m.group(0).strip() if m else None)
    row = store.conn.execute("SELECT h1, title FROM pages WHERE url=?", (target,)).fetchone()
    return (row["h1"] or row["title"]) if row else None, None


def _usable_anchor(query: str, target: str) -> bool:
    q = " ".join((query or "").lower().split())
    if len(q) < 7 or len(q) > 80 or len(q.split()) < 2:
        return False
    generic = {"accueil", "contact", "cliquez ici", "en savoir plus", "site officiel"}
    if q in generic:
        return False
    domain_label = urlparse(target).netloc.lower().removeprefix("www.").split(".")[0]
    compact_query = _compact_text(q)
    compact_domain = _compact_text(domain_label)
    if compact_domain and compact_domain in compact_query:
        return False

    # Pour une marque incluse dans un domaine composé (ex. marque-conseil.fr),
    # filtre les segments distinctifs mais conserve les mots métier génériques.
    generic_domain_words = {
        "agence", "assurance", "assurances", "cabinet", "conseil", "conseils",
        "expert", "france", "groupe", "group", "service", "services", "solutions",
    }
    brand_parts = {
        _compact_text(part) for part in re.split(r"[-_]", domain_label)
        if len(_compact_text(part)) >= 4 and _compact_text(part) not in generic_domain_words
    }
    return not any(part in compact_query for part in brand_parts)


def _compact_text(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", ascii_value.lower())


def _anchor_priority(row) -> float:
    impressions = float(row["impressions"] or 0)
    clicks = float(row["clicks"] or 0)
    position = float(row["position"] or 0)
    opportunity = 1.5 if 4 <= position <= 20 else 1.0
    return (log1p(impressions) + 0.5 * log1p(clicks)) * opportunity


def cluster_pages(store: Store, n_clusters: int | None = None, method: str = "embedding"):
    """Regroupe les pages en clusters thématiques (utile pour cocons / silos)."""
    from sklearn.cluster import AgglomerativeClustering

    urls, S = similarity_matrix(store, method)
    D = np.clip(1 - S, 0, None)
    np.fill_diagonal(D, 0)
    kw = ({"n_clusters": n_clusters} if n_clusters
          else {"n_clusters": None, "distance_threshold": 0.55})
    labels = AgglomerativeClustering(metric="precomputed", linkage="average", **kw).fit_predict(D)
    return dict(zip(urls, labels.tolist()))
