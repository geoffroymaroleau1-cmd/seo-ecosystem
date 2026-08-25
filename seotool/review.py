"""Agent de relecture : audit déterministe + passes LLM spécialisées + réécriture.

Architecture volontairement en deux étages :

  1. `structural_audit()` — 100% déterministe, pas de LLM. Longueur des chunks,
     H2 interrogatifs, réponse directe en tête, densité de chiffres, sources
     citées, tableaux et FAQ. C'est ce qui donne un score reproductible
     d'un run à l'autre.

  2. Passes LLM spécialisées (E-E-A-T, GEO, rédaction), chacune avec sa
     grille, qui retournent du JSON structuré. Un éditeur applique ensuite les
     correctifs. Boucle jusqu'au seuil ou au nombre d'itérations max.

Avertissement honnête : E-E-A-T n'est pas un score que Google calcule, c'est une
grille d'évaluation humaine dont on approxime les signaux. Et le GEO est une
discipline jeune — les critères ci-dessous (réponse directe en tête, chunks
autonomes, statistiques sourçables, structure Q/R) reflètent l'état de l'art
observé, pas une mécanique documentée par les moteurs. À traiter comme des
hypothèses à mesurer, pas comme des règles.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

from .store import Store

MODEL = os.environ.get("SEOTOOL_MODEL", "claude-sonnet-4-6")


# ==========================================================================
# 1. Audit déterministe
# ==========================================================================
SOURCE_RE = re.compile(r"(https?://|\[\d+\]|selon\s+[A-ZÉÈ]|d'après\s+[A-ZÉÈ]|source\s*:)", re.I)
STAT_RE = re.compile(
    r"\b\d+(?:[.,\u202f ]\d{3})*(?:[.,]\d+)?\s?"
    r"(?:%|€|\$|k€|M€|Md€|millions?|milliards?|points?|pts?|fois|x)(?![a-zà-ÿ])", re.I)
QUESTION_H = re.compile(r"^(comment|pourquoi|qu[e'’]est|quel|quelle|quand|où|combien|qui|faut-il|est-ce)",
                        re.I)
HEDGE = re.compile(r"\b(il est important de|dans un monde où|à l'ère (du|de la)|de nos jours|"
                   r"force est de constater|il convient de noter|n'hésitez pas à)\b", re.I)


def split_sections(md: str) -> list[dict]:
    """Découpe le markdown en chunks au niveau des titres — l'unité que les
    moteurs génératifs extraient réellement."""
    out, cur = [], {"level": 0, "title": "(intro)", "body": []}
    for line in md.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            out.append(cur)
            cur = {"level": len(m.group(1)), "title": m.group(2).strip(), "body": []}
        else:
            cur["body"].append(line)
    out.append(cur)
    for s in out:
        s["text"] = "\n".join(s["body"]).strip()
        s["words"] = len(s["text"].split())
    return [s for s in out if s["text"] or s["level"]]


def structural_audit(md: str) -> dict:
    sections = split_sections(md)
    body = re.sub(r"^#{1,6}\s+.*$", "", md, flags=re.M)
    words = len(body.split())
    h2h3 = [s for s in sections if s["level"] in (2, 3)]
    autonomes = [s for s in h2h3 if 40 <= s["words"] <= 300]
    intro = next((s["text"] for s in sections if s["level"] <= 1 and s["text"]), "")
    first_para = intro.split("\n\n")[0] if intro else ""

    m = {
        "mots": words,
        "sections": len(h2h3),
        "chunks_autonomes_pct": round(100 * len(autonomes) / max(1, len(h2h3))),
        "h2_interrogatifs_pct": round(100 * sum(bool(QUESTION_H.match(s["title"])) or
                                                s["title"].endswith("?") for s in h2h3) / max(1, len(h2h3))),
        "reponse_directe_en_tete": bool(first_para) and 25 <= len(first_para.split()) <= 90,
        "mots_1er_paragraphe": len(first_para.split()),
        "statistiques": len(STAT_RE.findall(body)),
        "sources_citees": len(SOURCE_RE.findall(body)),
        "tableaux": body.count("\n|"),
        "listes": len(re.findall(r"^\s*[-*+]\s+", body, flags=re.M)),
        "faq_presente": bool(re.search(r"^#{2,3}\s*(faq|questions? fréquentes?)", md, re.I | re.M)),
        "phrases_longues_pct": _long_sentences(body),
        "formules_creuses": len(HEDGE.findall(body)),
        "densite_stats_pour_1000_mots": round(1000 * len(STAT_RE.findall(body)) / max(1, words), 1),
    }
    return m


def _long_sentences(text: str) -> int:
    ph = [p for p in re.split(r"[.!?]\s", text) if p.strip()]
    if not ph:
        return 0
    return round(100 * sum(len(p.split()) > 30 for p in ph) / len(ph))



def geo_score(m: dict) -> dict:
    """Score 0-100 pondéré. Les poids sont des hypothèses : ajuste-les quand tu
    auras mesuré ce qui corrèle avec tes citations réelles dans les LLM."""
    c = {
        "réponse directe en tête": (20, 100 if m["reponse_directe_en_tete"] else 0),
        "chunks autonomes": (20, m["chunks_autonomes_pct"]),
        "structure interrogative": (10, m["h2_interrogatifs_pct"]),
        "faits citables (stats)": (15, min(100, m["densite_stats_pour_1000_mots"] * 25)),
        "sources vérifiables": (15, min(100, m["sources_citees"] * 20)),
        "formats extractibles": (10, min(100, (m["tableaux"] > 0) * 50 + (m["listes"] > 3) * 30 +
                                         m["faq_presente"] * 20)),
        "lisibilité": (10, max(0, 100 - max(0, m["phrases_longues_pct"] - 10) * 4)),
    }
    total = sum(w * s for w, s in c.values()) / 100
    return {"score": round(total), "detail": {k: round(v[1]) for k, v in c.items()}}


# ==========================================================================
# 2. Passes LLM
# ==========================================================================
RUBRICS = {

    "eeat": """Tu évalues les signaux d'expérience, expertise, autorité et fiabilité, en gardant
en tête que ce sont des proxys, pas un score Google. Vérifie :
- Expérience : éléments de première main (cas vécu, test, chiffres internes, terrain) ou texte
  purement compilatoire ?
- Expertise : précision terminologique, nuances qu'un praticien seul connaît, erreurs factuelles.
- Autorité : affirmations non étayées qui exigeraient une source, sources citées vérifiables et
  récentes, attribution claire.
- Fiabilité : promesses excessives, généralisations, absence de dates sur des données
  périssables, conflits d'intérêt non signalés.
Signale chaque affirmation invérifiable en la citant textuellement.""",

    "geo": """Tu évalues la citabilité par les moteurs génératifs (ChatGPT, Perplexity, AI Overviews).
Critères : réponse directe et autoportante en tête (25-90 mots), chaque section compréhensible
hors contexte, titres formulés comme des questions réelles, faits chiffrés attribués et datés,
définitions explicites des termes, formats extractibles (tableau comparatif, liste de critères,
FAQ), absence d'anaphores qui cassent l'extraction ("comme vu plus haut", "celui-ci").
Ces critères sont des hypothèses de l'état de l'art, pas des règles documentées : signale-les
comme des améliorations probables, sans les présenter comme des certitudes.""",

    "redac": """Tu évalues la qualité rédactionnelle française : formules creuses et chevilles IA
("dans un monde où", "il est important de", "n'hésitez pas à"), phrases de plus de 30 mots,
répétitions, transitions artificielles, ton incohérent avec le reste du site, jargon non défini
au premier emploi, promesses marketing non tenues par le corps du texte.""",
}

SYSTEM = """Tu es relecteur SEO senior en agence B2B française. Tu produis des critiques
actionnables, jamais de compliments génériques. Tu cites toujours l'extrait fautif.
Tu réponds EXCLUSIVEMENT en JSON valide, sans balises markdown, au format :
{"score": 0-100, "verdict": "une phrase", "findings": [
  {"gravite": "bloquant|majeur|mineur", "extrait": "citation exacte ou null",
   "probleme": "...", "correction": "reformulation concrète proposée"}]}"""


def llm_pass(text: str, dimension: str, context: dict, model: str = MODEL) -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    ctx = json.dumps(context, ensure_ascii=False, default=str)[:60_000]
    msg = client.messages.create(
        model=model, max_tokens=4000, system=SYSTEM,
        messages=[{"role": "user", "content":
                   f"{RUBRICS[dimension]}\n\nCONTEXTE (requête cible, GSC, site) :\n{ctx}\n\n"
                   f"TEXTE À RELIRE :\n{text}"}],
    )
    raw = "".join(b.text for b in msg.content if b.type == "text")
    return _json(raw, dimension)


def _json(raw: str, dimension: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    try:
        d = json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", cleaned, re.S)
        d = json.loads(m.group(0)) if m else {"score": 0, "verdict": "parsing échoué",
                                              "findings": [], "raw": raw[:2000]}
    d["dimension"] = dimension
    return d


def editor(text: str, findings: list[dict], context: dict, model: str = MODEL) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model=model, max_tokens=8000,
        system=("Tu es rédacteur SEO senior. Tu appliques les corrections demandées au texte "
                "en préservant sa voix et sa structure. Tu ne rallonges pas artificiellement. "
                "Si une correction exige une information factuelle que tu n'as pas, tu insères "
                "[[À VÉRIFIER : ...]] plutôt que d'inventer un chiffre ou une source. "
                "Tu renvoies UNIQUEMENT le texte markdown corrigé, sans commentaire."),
        messages=[{"role": "user", "content":
                   f"CONTEXTE :\n{json.dumps(context, ensure_ascii=False, default=str)[:40_000]}\n\n"
                   f"CORRECTIONS À APPLIQUER :\n{json.dumps(findings, ensure_ascii=False, indent=1)}\n\n"
                   f"TEXTE :\n{text}"}],
    )
    return "".join(b.text for b in msg.content if b.type == "text").strip()


# ==========================================================================
# 3. Boucle de relecture
# ==========================================================================
def review(text: str, *, store: Store | None = None, query: str | None = None,
           dimensions=("eeat", "geo", "redac"), max_iter: int = 3,
           target: int = 85, doc_id: str | None = None, model: str = MODEL) -> dict:
    """Relit, corrige, re-relit jusqu'au seuil. Retourne le texte final + l'historique."""
    context = {"requete_cible": query}
    history, current = [], text
    doc_id = doc_id or (query or "doc")

    for it in range(1, max_iter + 1):
        metrics = structural_audit(current)
        geo = geo_score(metrics)
        passes = [llm_pass(current, d, context, model) for d in dimensions]
        scores = {p["dimension"]: p.get("score", 0) for p in passes}
        scores["geo_structurel"] = geo["score"]
        moyenne = round(sum(scores.values()) / len(scores))

        history.append({"iteration": it, "scores": scores, "moyenne": moyenne,
                        "metriques": metrics, "geo_detail": geo["detail"],
                        "findings": [f for p in passes for f in p.get("findings", [])]})
        if store is not None:
            _persist(store, doc_id, it, scores, history[-1]["findings"])

        blocking = [f for p in passes for f in p.get("findings", [])
                    if f.get("gravite") in ("bloquant", "majeur")]
        print(f"[review] itération {it} — moyenne {moyenne}/100 "
              f"({', '.join(f'{k}:{v}' for k, v in scores.items())}) — "
              f"{len(blocking)} correctifs prioritaires")

        if moyenne >= target or not blocking or it == max_iter:
            break
        current = editor(current, blocking, context, model)

    return {"texte": current, "historique": history,
            "score_final": history[-1]["moyenne"], "iterations": len(history)}


def _persist(store: Store, doc_id: str, it: int, scores: dict, findings: list):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with store.tx() as c:
        c.executemany(
            "INSERT INTO content_reviews (doc_id,iteration,dimension,score,findings,created_at) "
            "VALUES (?,?,?,?,?,?)",
            [(doc_id, it, dim, sc,
              json.dumps([f for f in findings], ensure_ascii=False), now)
             for dim, sc in scores.items()])


def report_markdown(res: dict) -> str:
    """Rapport de relecture livrable au client ou au rédacteur."""
    last = res["historique"][-1]
    m, g = last["metriques"], last["geo_detail"]
    lignes = "\n".join(f"| {k} | {v} |" for k, v in m.items()
                       if not isinstance(v, list))
    geo = "\n".join(f"| {k} | {v}/100 |" for k, v in g.items())
    findings = "\n".join(
        f"- **{f.get('gravite', '?')}** — {f.get('probleme', '')}\n"
        f"  > {f.get('extrait') or '(global)'}\n"
        f"  → {f.get('correction', '')}"
        for f in last["findings"][:40]) or "_Aucun point bloquant._"
    return f"""# Rapport de relecture — score {res['score_final']}/100 ({res['iterations']} itération(s))

## Scores par dimension
{chr(10).join(f'- **{k}** : {v}/100' for k, v in last['scores'].items())}

## Métriques structurelles
| Métrique | Valeur |
|---|---|
{lignes}

## Détail du score GEO
| Critère | Score |
|---|---|
{geo}

## Points à corriger
{findings}
"""
