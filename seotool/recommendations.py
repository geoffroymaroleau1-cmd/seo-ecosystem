"""Recommandations SEO déterministes, traçables et multi-client."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from urllib.parse import urlparse

import pandas as pd

from .store import Store


INTENT_RULES = {
    "transactionnelle": ("devis", "acheter", "achat", "prix", "tarif", "souscrire", "commande"),
    "commerciale": ("meilleur", "comparatif", "comparaison", "avis", "choisir", "alternative"),
    "locale": ("près de moi", "proche", "paris", "lyon", "marseille", "bordeaux", "lille"),
    "informationnelle": ("comment", "pourquoi", "quand", "quoi", "quel", "quelle", "guide", "définition"),
}


def _plain(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def detect_intent(query: str) -> dict:
    """Infère une intention et expose sa confiance au lieu de la présenter comme certaine."""
    text = _plain(query)
    scores = Counter()
    evidence = []
    for intent, markers in INTENT_RULES.items():
        for marker in markers:
            if _plain(marker) in text:
                scores[intent] += 1
                evidence.append(marker)
    if not scores:
        intent, confidence = "indéterminée", 45
    else:
        intent, count = scores.most_common(1)[0]
        confidence = min(90, 58 + count * 12)
    content_type = {
        "transactionnelle": "landing page",
        "commerciale": "comparatif ou guide de choix",
        "locale": "page locale",
        "informationnelle": "article ou guide",
        "indéterminée": "à valider",
    }[intent]
    return {"intent": intent, "confidence": confidence,
            "content_type": content_type, "signals": evidence[:5]}


def _priority(score: float) -> str:
    if score >= 85:
        return "Critique"
    if score >= 70:
        return "Élevée"
    if score >= 45:
        return "Moyenne"
    return "Faible"


def _dedication(query: str, title: str, h1: str) -> float:
    stop = {"de", "des", "du", "la", "le", "les", "un", "une", "et", "a", "pour", "en"}
    q = {w for w in _plain(query).split() if len(w) > 2 and w not in stop}
    page = set(_plain(f"{title or ''} {h1 or ''}").split())
    return len(q & page) / len(q) if q else 0.0


def _brand_like(query: str, page: str) -> bool:
    """Détecte génériquement une requête de marque proche du domaine."""
    host = urlparse(page or "").netloc.lower().removeprefix("www.").split(".")[0]
    q = _plain(query).replace(" ", "")
    domain = _plain(host).replace(" ", "")
    if not q or not domain:
        return False
    return domain in q or q in domain or SequenceMatcher(None, q, domain).ratio() >= 0.78


def build(store: Store, min_impressions: int = 100) -> pd.DataFrame:
    """Produit une file d'actions priorisées à partir du crawl, du graphe et de GSC."""
    out = []

    def add(category, action, score, subject, url, evidence, confidence=90):
        out.append({
            "priorité": _priority(score), "score": round(score, 1),
            "catégorie": category, "action": action, "sujet": subject or "",
            "url": url or "", "justification": evidence,
            "confiance": int(confidence),
        })

    pages = store.conn.execute(
        """SELECT p.*, COALESCE(m.unique_inlinks,0) unique_inlinks,
                  COALESCE(m.depth_click,-1) depth_click, COALESCE(m.is_orphan,0) is_orphan
           FROM pages p LEFT JOIN graph_metrics m ON m.url=p.url WHERE p.status=200"""
    ).fetchall()
    for p in pages:
        if not p["title"]:
            add("Technique", "Ajouter un title", 92, "Title manquant", p["url"],
                "Page HTTP 200 sans balise title.", 99)
        if not p["h1"]:
            add("Contenu", "Ajouter un H1", 80, "H1 manquant", p["url"],
                "Page HTTP 200 sans H1 détecté.", 98)
        if not p["meta_desc"]:
            add("Technique", "Rédiger la meta description", 48, "Meta description manquante",
                p["url"], "Page HTTP 200 sans meta description.", 98)
        if p["word_count"] is not None and p["word_count"] < 300:
            add("Contenu", "Évaluer puis enrichir", 55, "Contenu court", p["url"],
                f"{p['word_count']} mots détectés ; vérifier l'intention avant d'allonger.", 80)
        if p["is_orphan"]:
            add("Maillage", "Créer des liens entrants", 78, "Page orpheline", p["url"],
                "Aucun lien interne entrant détecté dans le graphe.", 95)
        elif p["depth_click"] > 3:
            add("Maillage", "Réduire la profondeur", 60, "Page profonde", p["url"],
                f"Profondeur de clic : {p['depth_click']}.", 95)

    gsc_rows = store.conn.execute(
        """SELECT g.query, g.page, SUM(g.impressions) impressions, SUM(g.clicks) clicks,
                  CASE WHEN SUM(g.impressions)>0
                       THEN SUM(g.position*g.impressions)/SUM(g.impressions) ELSE 0 END position,
                  p.title, p.h1
           FROM gsc g LEFT JOIN pages p ON p.url=g.page
           GROUP BY g.query, g.page HAVING impressions >= ?""", (min_impressions,)
    ).fetchall()
    for r in gsc_rows:
        if _brand_like(r["query"], r["page"]):
            continue
        impressions, clicks, position = r["impressions"], r["clicks"], r["position"]
        ctr = clicks / impressions if impressions else 0
        intent = detect_intent(r["query"])
        if 4 <= position <= 20:
            score = min(94, 55 + min(25, impressions / 250) + (10 if ctr < 0.03 else 3))
            add("GSC", "Optimiser la page existante", score, r["query"], r["page"],
                f"{impressions} impressions, {clicks} clics, position {position:.1f}, CTR {ctr:.1%}.",
                92)
        dedication = _dedication(r["query"], r["title"] or "", r["h1"] or "")
        if position > 15 and dedication < 0.34:
            score = min(90, 50 + min(30, impressions / 200))
            add("Contenu", f"Étudier la création d'une {intent['content_type']}", score,
                r["query"], r["page"],
                f"Page actuelle peu dédiée (couverture {dedication:.0%}), {impressions} impressions, "
                f"position {position:.1f}. Intention probable : {intent['intent']}.",
                intent["confidence"])

    cannibal = store.conn.execute(
        """SELECT query, COUNT(DISTINCT page) n_pages, SUM(impressions) impressions,
                  GROUP_CONCAT(DISTINCT page) urls
           FROM gsc GROUP BY query HAVING n_pages>1 AND impressions>=?""", (min_impressions,)
    ).fetchall()
    for r in cannibal:
        first_url = (r["urls"] or "").split(",", 1)[0]
        if _brand_like(r["query"], first_url):
            continue
        score = min(88, 55 + r["n_pages"] * 5 + min(15, r["impressions"] / 500))
        add("Cannibalisation", "Vérifier puis consolider le ciblage", score, r["query"], "",
            f"{r['n_pages']} URL différentes et {r['impressions']} impressions. URLs : {r['urls']}", 75)

    if not out:
        return pd.DataFrame(columns=["priorité", "score", "catégorie", "action", "sujet",
                                     "url", "justification", "confiance"])
    return pd.DataFrame(out).sort_values(["score", "confiance"], ascending=False).reset_index(drop=True)
