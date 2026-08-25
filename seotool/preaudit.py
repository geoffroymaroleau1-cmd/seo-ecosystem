"""Pré-audit SEO illustré et explicable pour l'avant-vente."""
from __future__ import annotations

from datetime import date

import pandas as pd

from . import recommendations
from .store import Store


def _pct_score(ok: int, total: int) -> int:
    return round(100 * ok / total) if total else 0


def snapshot(store: Store) -> dict:
    """Calcule une photographie synthétique sans score SEO opaque."""
    total = store.conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    pages_200 = store.conn.execute("SELECT COUNT(*) FROM pages WHERE status=200").fetchone()[0]
    errors = store.conn.execute("SELECT COUNT(*) FROM pages WHERE status>=400").fetchone()[0]
    redirects = store.conn.execute(
        "SELECT COUNT(*) FROM pages WHERE redirect_to IS NOT NULL OR status BETWEEN 300 AND 399"
    ).fetchone()[0]
    noindex = store.conn.execute(
        "SELECT COUNT(*) FROM pages WHERE status=200 AND meta_robots LIKE '%noindex%'"
    ).fetchone()[0]
    missing_title = store.conn.execute(
        "SELECT COUNT(*) FROM pages WHERE status=200 AND COALESCE(title,'')=''"
    ).fetchone()[0]
    missing_meta = store.conn.execute(
        "SELECT COUNT(*) FROM pages WHERE status=200 AND COALESCE(meta_desc,'')=''"
    ).fetchone()[0]
    missing_h1 = store.conn.execute(
        "SELECT COUNT(*) FROM pages WHERE status=200 AND COALESCE(h1,'')=''"
    ).fetchone()[0]
    thin = store.conn.execute(
        "SELECT COUNT(*) FROM pages WHERE status=200 AND COALESCE(word_count,0)<300"
    ).fetchone()[0]
    orphans = store.conn.execute("SELECT COUNT(*) FROM graph_metrics WHERE is_orphan=1").fetchone()[0]
    deep = store.conn.execute("SELECT COUNT(*) FROM graph_metrics WHERE depth_click>3").fetchone()[0]

    index_score = _pct_score(max(0, pages_200 - errors - noindex), pages_200)
    metadata_checks = pages_200 * 3
    metadata_ok = max(0, metadata_checks - missing_title - missing_meta - missing_h1)
    metadata_score = _pct_score(metadata_ok, metadata_checks)
    content_score = _pct_score(max(0, pages_200 - thin), pages_200)
    architecture_score = _pct_score(max(0, pages_200 - orphans - deep), pages_200)
    scores = {
        "Exploration & indexation": index_score,
        "Métadonnées": metadata_score,
        "Contenu": content_score,
        "Architecture": architecture_score,
    }
    global_score = round(sum(scores.values()) / len(scores)) if pages_200 else 0

    status = store.df(
        """SELECT CASE
                    WHEN status BETWEEN 200 AND 299 THEN '2xx'
                    WHEN status BETWEEN 300 AND 399 OR redirect_to IS NOT NULL THEN '3xx'
                    WHEN status BETWEEN 400 AND 499 THEN '4xx'
                    WHEN status>=500 THEN '5xx'
                    ELSE 'Autres' END catégorie,
                  COUNT(*) pages
           FROM pages GROUP BY catégorie ORDER BY catégorie"""
    )
    depths = store.df(
        """SELECT CASE WHEN depth_click<0 THEN 'Inaccessible'
                        ELSE CAST(depth_click AS TEXT) END profondeur,
                  COUNT(*) pages
           FROM graph_metrics GROUP BY profondeur ORDER BY depth_click"""
    )
    issues = pd.DataFrame([
        ("Meta descriptions manquantes", missing_meta, "Moyenne"),
        ("Pages orphelines", orphans, "Élevée"),
        ("Contenus de moins de 300 mots", thin, "Moyenne"),
        ("H1 manquants", missing_h1, "Élevée"),
        ("Titles manquants", missing_title, "Critique"),
        ("Pages HTTP en erreur", errors, "Critique"),
        ("Pages profondes (> 3 clics)", deep, "Moyenne"),
        ("Pages noindex", noindex, "À vérifier"),
        ("Redirections", redirects, "À vérifier"),
    ], columns=["contrôle", "pages", "niveau"])
    issues = issues[issues["pages"] > 0].sort_values("pages", ascending=False).reset_index(drop=True)
    priorities = recommendations.build(store, min_impressions=100).head(12)
    return {
        "score": global_score, "scores": scores, "total": total, "pages_200": pages_200,
        "errors": errors, "orphans": orphans, "status": status, "depths": depths,
        "issues": issues, "priorities": priorities,
    }


def report_markdown(data: dict, project_name: str) -> str:
    score_lines = "\n".join(f"- {name} : **{score}/100**" for name, score in data["scores"].items())
    issue_lines = "\n".join(
        f"- **{r['contrôle']}** : {int(r['pages'])} page(s) — {r['niveau']}"
        for _, r in data["issues"].iterrows()
    ) or "- Aucun problème détecté dans les contrôles disponibles."
    priority_lines = "\n".join(
        f"- **{r['action']}** — {r['sujet'] or r['url']} ({r['priorité']}, confiance {r['confiance']} %)"
        for _, r in data["priorities"].iterrows()
    ) or "- Données insuffisantes pour établir des priorités."
    return f"""# Pré-audit SEO — {project_name}

_Généré le {date.today().isoformat()} à partir des données disponibles dans l'outil._

## Synthèse

- Score de contrôle interne : **{data['score']}/100**
- URL observées : **{data['total']}**
- Pages HTTP 200 : **{data['pages_200']}**
- Erreurs HTTP : **{data['errors']}**
- Pages orphelines : **{data['orphans']}**

Ce score est une synthèse interne des contrôles disponibles, pas une note Google.

## Détail des contrôles

{score_lines}

## Erreurs et points de vigilance

{issue_lines}

## Actions prioritaires

{priority_lines}

## Limites

Ce pré-audit sert à identifier rapidement des signaux et opportunités. Il ne remplace pas un audit
exhaustif, une validation humaine, une analyse de logs ou une étude complète de la concurrence.
"""

