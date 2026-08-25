"""Connecteur Search Console : requêtes x URL, stockées par mois.

Auth : soit un compte de service (recommandé en prod, à ajouter comme
utilisateur délégué dans la GSC), soit un OAuth desktop (client_secret.json).
"""
from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import date, timedelta
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
API = "https://www.googleapis.com/webmasters/v3/sites/"


def _column_key(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _number(value) -> float:
    if value is None or str(value).strip() == "":
        return 0.0
    text = str(value).strip().replace("\u00a0", "").replace(" ", "")
    return float(text.rstrip("%").replace(",", "."))


def import_csv(store, csv_path: str | Path, *, page: str | None = None,
               period: str | None = None, country: str = "all",
               device: str = "all") -> int:
    """Importe un export CSV de Search Console, en français ou en anglais."""
    import pandas as pd

    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"Export GSC introuvable : {path}")
    try:
        frame = pd.read_csv(path, sep=None, engine="python", encoding="utf-8-sig")
    except UnicodeDecodeError:
        frame = pd.read_csv(path, sep=None, engine="python", encoding="latin-1")
    if frame.empty:
        raise ValueError("L'export GSC ne contient aucune ligne.")

    aliases = {
        "query": {"query", "queries", "requete", "requetes", "topqueries"},
        "page": {"page", "pages", "url", "landingpage"},
        "clicks": {"click", "clicks", "clic", "clics", "urlclick", "urlclicks"},
        "impressions": {"impression", "impressions"},
        "ctr": {"ctr", "urlctr", "tauxdeclic", "tauxdeclics"},
        "position": {"position", "averageposition", "positionmoyenne"},
        "period": {"period", "periode", "month", "mois", "date"},
    }
    columns = {_column_key(col): col for col in frame.columns}

    def find(name: str):
        return next((columns[key] for key in aliases[name] if key in columns), None)

    selected = {name: find(name) for name in aliases}
    missing = [name for name in ("query", "clicks", "impressions", "position")
               if selected[name] is None]
    if missing:
        raise ValueError("Colonnes GSC manquantes : " + ", ".join(missing))
    if selected["page"] is None and not page:
        raise ValueError("La colonne Page est absente : ajoutez --page URL.")
    if selected["period"] is None and not period:
        raise ValueError("La date est absente : ajoutez --period YYYY-MM.")

    grouped = {}
    for _, item in frame.iterrows():
        query = str(item[selected["query"]]).strip()
        row_page = str(item[selected["page"]]).strip() if selected["page"] else page
        if selected["period"]:
            raw_period = str(item[selected["period"]]).strip()
            match = re.search(r"(\d{4})[-/]?(\d{2})", raw_period)
            row_period = f"{match.group(1)}-{match.group(2)}" if match else None
        else:
            # An explicit label may describe an aggregate export (for example
            # eight months), so preserve it instead of pretending it is one month.
            row_period = period
        if not query or query.lower() == "nan" or not row_page or not row_period:
            continue
        clicks = _number(item[selected["clicks"]])
        impressions = _number(item[selected["impressions"]])
        position = _number(item[selected["position"]])
        key = (row_page, query, row_period, country, device)
        acc = grouped.setdefault(key, [0.0, 0.0, 0.0])
        acc[0] += clicks
        acc[1] += impressions
        acc[2] += position * impressions

    rows = []
    for (row_page, query, row_period, row_country, row_device), values in grouped.items():
        clicks, impressions, weighted_position = values
        ctr = clicks / impressions if impressions else 0.0
        position = weighted_position / impressions if impressions else 0.0
        rows.append((row_page, query, row_period, round(clicks), round(impressions),
                     ctr, position, row_country, row_device))
    store.insert_gsc(rows)
    store.conn.commit()
    print(f"[gsc] importé : {len(rows)} lignes depuis {path}")
    return len(rows)


def get_service(credentials_path: str = "credentials.json", token_path: str = "token.json"):
    from google.oauth2.credentials import Credentials
    from google.oauth2.service_account import Credentials as SACredentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    import json

    cred_file = Path(credentials_path)
    info = json.loads(cred_file.read_text())

    if info.get("type") == "service_account":
        creds = SACredentials.from_service_account_file(credentials_path, scopes=SCOPES)
    else:
        tp = Path(token_path)
        creds = Credentials.from_authorized_user_file(token_path, SCOPES) if tp.exists() else None
        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
            tp.write_text(creds.to_json())
    return build("searchconsole", "v1", credentials=creds, cache_discovery=False)


def month_bounds(period: str) -> tuple[str, str]:
    y, m = map(int, period.split("-"))
    last = calendar.monthrange(y, m)[1]
    return f"{y}-{m:02d}-01", f"{y}-{m:02d}-{last:02d}"


def last_n_months(n: int) -> list[str]:
    """Les n derniers mois complets (la GSC a ~2-3 jours de latence)."""
    out, d = [], date.today().replace(day=1)
    for _ in range(n):
        d = (d - timedelta(days=1)).replace(day=1)
        out.append(f"{d.year}-{d.month:02d}")
    return list(reversed(out))


def fetch_month(service, site_url: str, period: str, *, country: str | None = None,
                device: str | None = None, row_limit: int = 25000) -> list[tuple]:
    start, end = month_bounds(period)
    filters = []
    if country:
        filters.append({"dimension": "country", "operator": "equals", "expression": country})
    if device:
        filters.append({"dimension": "device", "operator": "equals", "expression": device})

    rows, offset = [], 0
    while True:
        body = {
            "startDate": start, "endDate": end,
            "dimensions": ["page", "query"],
            "rowLimit": row_limit, "startRow": offset,
            "dataState": "final",
            "type": "web",
        }
        if filters:
            body["dimensionFilterGroups"] = [{"filters": filters}]
        resp = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
        batch = resp.get("rows", [])
        for r in batch:
            page, query = r["keys"]
            rows.append((page, query, period, r["clicks"], r["impressions"],
                         r["ctr"], r["position"], country or "all", device or "all"))
        if len(batch) < row_limit:
            break
        offset += row_limit
        print(f"  [gsc] {period} : {len(rows)} lignes...")
    return rows


def sync(store, site_url: str, months: int = 16, credentials_path="credentials.json",
         country: str | None = None):
    service = get_service(credentials_path)
    done = {r[0] for r in store.conn.execute("SELECT DISTINCT period FROM gsc")}
    for period in last_n_months(months):
        if period in done and period != last_n_months(1)[0]:
            continue
        rows = fetch_month(service, site_url, period, country=country)
        store.insert_gsc(rows)
        store.conn.commit()
        print(f"[gsc] {period} : {len(rows)} lignes enregistrées")


# --- analyses dérivées ----------------------------------------------------
def queries_for(store, url: str, period: str | None = None, limit: int = 100):
    sql = """SELECT query, SUM(clicks) clics, SUM(impressions) impr,
                    AVG(position) pos_moy
             FROM gsc WHERE page = ? {p}
             GROUP BY query ORDER BY impr DESC LIMIT ?"""
    p, params = ("", (url, limit)) if not period else ("AND period = ?", (url, period, limit))
    return store.df(sql.format(p=p), params)


def opportunities(store, period: str, min_impr: int = 100, pos_range=(4, 20)):
    """Requêtes à fort volume positionnées en page 1 basse / page 2 : le gisement
    d'optimisation le plus rentable pour un point mensuel."""
    return store.df(
        """SELECT page, query, SUM(impressions) impr, SUM(clicks) clics,
                  AVG(position) pos
           FROM gsc WHERE period = ?
           GROUP BY page, query
           HAVING impr >= ? AND pos BETWEEN ? AND ?
           ORDER BY impr DESC""",
        (period, min_impr, *pos_range),
    )


def cannibalisation(store, period: str, min_impr: int = 50):
    """Requêtes servies par plusieurs URLs : conflits de ciblage."""
    return store.df(
        """SELECT query, COUNT(DISTINCT page) n_pages,
                  SUM(impressions) impr, GROUP_CONCAT(DISTINCT page) pages
           FROM gsc WHERE period = ?
           GROUP BY query HAVING n_pages > 1 AND impr >= ?
           ORDER BY impr DESC""",
        (period, min_impr),
    )


def trend(store, months: int = 6):
    """Évolution clics/impressions par page sur N mois : détecte les déclins."""
    periods = last_n_months(months)
    ph = ",".join("?" for _ in periods)
    return store.df(
        f"""SELECT page, period, SUM(clicks) clics, SUM(impressions) impr,
                   AVG(position) pos
            FROM gsc WHERE period IN ({ph})
            GROUP BY page, period ORDER BY page, period""",
        tuple(periods),
    )


def gap_pages(store, period: str, min_impr: int = 300):
    """Requêtes fortes dont aucune page n'est vraiment dédiée (pos > 15 partout)
    => candidates à la création de page / brief d'article."""
    return store.df(
        """SELECT query, SUM(impressions) impr, MIN(position) meilleure_pos,
                  COUNT(DISTINCT page) n_pages
           FROM gsc WHERE period = ?
           GROUP BY query HAVING impr >= ? AND meilleure_pos > 15
           ORDER BY impr DESC""",
        (period, min_impr),
    )
