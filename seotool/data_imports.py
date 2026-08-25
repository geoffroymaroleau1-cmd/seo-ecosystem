"""Imports souples des exports Semrush, français ou anglais."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd


def _key(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _read(path: str | Path) -> pd.DataFrame:
    try:
        frame = pd.read_csv(path, sep=None, engine="python", encoding="utf-8-sig")
    except UnicodeDecodeError:
        frame = pd.read_csv(path, sep=None, engine="python", encoding="latin-1")
    if frame.empty:
        raise ValueError("Le fichier ne contient aucune ligne.")
    return frame


def _number(value):
    if pd.isna(value) or str(value).strip() in {"", "-"}:
        return None
    text = str(value).replace("\u00a0", "").replace(" ", "").replace("%", "")
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def _columns(frame, aliases, required):
    available = {_key(c): c for c in frame.columns}
    found = {name: next((available[a] for a in names if a in available), None)
             for name, names in aliases.items()}
    missing = [name for name in required if not found.get(name)]
    if missing:
        raise ValueError("Colonnes introuvables : " + ", ".join(missing) +
                         ". Colonnes reçues : " + ", ".join(map(str, frame.columns)))
    return found


def import_semrush_keywords(store, path, period="non précisé") -> int:
    frame = _read(path)
    aliases = {
        "keyword": {"keyword", "motcle", "motcleorganique"},
        "page": {"url", "page", "landingpage", "urlpositionnee"},
        "position": {"position", "pos"}, "volume": {"volume", "searchvolume"},
        "traffic": {"traffic", "trafic", "trafficpercent", "traficpercent"},
        "kd": {"keyworddifficulty", "kd", "kdpercent"}, "cpc": {"cpc"},
        "intent": {"intent", "intention"}, "domain": {"domain", "domaine"},
    }
    c = _columns(frame, aliases, ("keyword", "page", "position"))
    rows = []
    for _, r in frame.iterrows():
        page, keyword = str(r[c["page"]]).strip(), str(r[c["keyword"]]).strip()
        if not page or not keyword or keyword.lower() == "nan":
            continue
        domain = str(r[c["domain"]]).strip() if c["domain"] else urlparse(page).netloc
        rows.append((domain, page, keyword, _number(r[c["position"]]),
                     _number(r[c["volume"]]) if c["volume"] else None,
                     _number(r[c["traffic"]]) if c["traffic"] else None,
                     _number(r[c["kd"]]) if c["kd"] else None,
                     _number(r[c["cpc"]]) if c["cpc"] else None,
                     str(r[c["intent"]]).strip() if c["intent"] else "", period))
    store.conn.executemany("INSERT OR REPLACE INTO semrush_keywords VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    store.conn.commit()
    return len(rows)


def import_keyword_gap(store, path, period="non précisé") -> int:
    frame = _read(path)
    aliases = {
        "keyword": {"keyword", "motcle"}, "competitor": {"competitor", "concurrent", "domain"},
        "own_pos": {"yourposition", "positiondomaine", "you", "rootdomainposition"},
        "comp_pos": {"competitorposition", "positionconcurrent", "position"},
        "volume": {"volume", "searchvolume"}, "kd": {"keyworddifficulty", "kd", "kdpercent"},
        "intent": {"intent", "intention"}, "status": {"status", "type", "opportunity"},
    }
    c = _columns(frame, aliases, ("keyword",))
    rows = []
    for _, r in frame.iterrows():
        keyword = str(r[c["keyword"]]).strip()
        if not keyword or keyword.lower() == "nan":
            continue
        competitor = str(r[c["competitor"]]).strip() if c["competitor"] else "export-gap"
        rows.append((keyword, competitor,
                     _number(r[c["own_pos"]]) if c["own_pos"] else None,
                     _number(r[c["comp_pos"]]) if c["comp_pos"] else None,
                     _number(r[c["volume"]]) if c["volume"] else None,
                     _number(r[c["kd"]]) if c["kd"] else None,
                     str(r[c["intent"]]).strip() if c["intent"] else "",
                     str(r[c["status"]]).strip() if c["status"] else "", period))
    store.conn.executemany("INSERT OR REPLACE INTO keyword_gap VALUES (?,?,?,?,?,?,?,?,?)", rows)
    store.conn.commit()
    return len(rows)


def import_backlinks(store, path, period="non précisé") -> int:
    frame = _read(path)
    aliases = {
        "source": {"sourceurl", "source", "referringpageurl", "pageasource"},
        "target": {"targeturl", "target", "destinationurl", "urlcible"},
        "domain": {"sourcedomain", "referringdomain", "domain", "domainedorigine"},
        "authority": {"authorityscore", "as", "domainscore", "scoreautorite"},
        "anchor": {"anchor", "anchortext", "ancre"}, "follow": {"follow", "linktype", "type"},
        "first": {"firstseen", "firstseenat", "premierevue"},
        "last": {"lastseen", "lastseenat", "dernierevue"},
    }
    c = _columns(frame, aliases, ("source", "target"))
    rows = []
    for _, r in frame.iterrows():
        source, target = str(r[c["source"]]).strip(), str(r[c["target"]]).strip()
        if not source or not target or source.lower() == "nan":
            continue
        domain = str(r[c["domain"]]).strip() if c["domain"] else urlparse(source).netloc
        follow_raw = str(r[c["follow"]]).lower() if c["follow"] else ""
        follow = 0 if "nofollow" in follow_raw else 1
        rows.append((source, target, domain,
                     _number(r[c["authority"]]) if c["authority"] else None,
                     str(r[c["anchor"]]).strip() if c["anchor"] else "", follow,
                     str(r[c["first"]]).strip() if c["first"] else "",
                     str(r[c["last"]]).strip() if c["last"] else "", period))
    store.conn.executemany("INSERT OR REPLACE INTO backlinks VALUES (?,?,?,?,?,?,?,?,?)", rows)
    store.conn.commit()
    return len(rows)
