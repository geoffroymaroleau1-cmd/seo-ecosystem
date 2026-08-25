"""CLI : python -m seotool <commande> --help"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import briefs, graph, gsc, semantic
from .crawler import crawl
from .store import Store


def db_for(site: str) -> str:
    slug = site.replace("https://", "").replace("http://", "").strip("/").replace("/", "_")
    return f"data/{slug}.db"


def main(argv=None):
    p = argparse.ArgumentParser("seotool", description="Écosystème SEO d'un site")
    p.add_argument("--db", help="chemin de la base (défaut: data/<domaine>.db)")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("crawl", help="crawler un site")
    c.add_argument("url")
    c.add_argument("--max-pages", type=int, default=5000)
    c.add_argument("--concurrency", type=int, default=10)
    c.add_argument("--delay", type=float, default=0.2)
    c.add_argument("--render-js", action="store_true")
    c.add_argument("--subdomains", action="store_true")
    c.add_argument("--ignore-robots", action="store_true")

    g = sub.add_parser("graph", help="PageRank interne, profondeur, orphelines")
    g.add_argument("url")
    g.add_argument("--include-template", action="store_true",
                   help="inclure nav/footer dans le PageRank")
    g.add_argument("--tree", action="store_true")

    s = sub.add_parser("gsc", help="synchroniser la Search Console")
    s.add_argument("site", nargs="?", help="propriété GSC pour la synchronisation API")
    s.add_argument("--months", type=int, default=16)
    s.add_argument("--credentials", default="credentials.json")
    s.add_argument("--country")
    s.add_argument("--import-csv", metavar="FICHIER", help="importer un export CSV GSC")
    s.add_argument("--page", help="URL concernée si elle n'est pas présente dans le CSV")
    s.add_argument("--period", help="mois YYYY-MM si absent du CSV")
    s.add_argument("--device", default="all")

    e = sub.add_parser("semantic", help="lexique, embeddings, suggestions de maillage")
    e.add_argument("--method", choices=["tfidf", "embedding"], default="tfidf")
    e.add_argument("--embed", action="store_true", help="calculer les vecteurs")
    e.add_argument("--top-k", type=int, default=5)
    e.add_argument("--min-score", type=float, default=0.25)

    b = sub.add_parser("brief", help="générer un brief")
    b.add_argument("keyword")
    b.add_argument("--kind", choices=["article", "lp", "sponso"], default="article")
    b.add_argument("--llm", action="store_true", help="rédiger via l'API Anthropic")
    b.add_argument("--out", default=None)


    rv = sub.add_parser("review", help="relecture agent d'un texte")
    rv.add_argument("file", help="fichier markdown à relire")
    rv.add_argument("--query", help="requête cible (contexte éditorial)")
    rv.add_argument("--max-iter", type=int, default=3)
    rv.add_argument("--target", type=int, default=85)
    rv.add_argument("--dimensions", default="eeat,geo,redac")
    rv.add_argument("--dry", action="store_true", help="audit déterministe seul, sans LLM")
    rv.add_argument("--out", default=None)

    r = sub.add_parser("report", help="point mensuel")
    r.add_argument("--period", help="YYYY-MM (défaut: dernier mois complet)")
    r.add_argument("--out", default=None)

    x = sub.add_parser("export", help="exporter les tables en CSV/XLSX")
    x.add_argument("--format", choices=["csv", "xlsx"], default="xlsx")
    x.add_argument("--out", default="export")

    a = sub.add_parser("audit", help="synthèse technique rapide")

    args = p.parse_args(argv)
    site = getattr(args, "url", None) or getattr(args, "site", "")
    db = args.db or db_for(site) if site else (args.db or "data/site.db")

    if args.cmd == "crawl":
        crawl(args.url, db, max_pages=args.max_pages, concurrency=args.concurrency,
              delay=args.delay, render_js=args.render_js,
              include_subdomains=args.subdomains, respect_robots=not args.ignore_robots)
        return

    store = Store(db)

    if args.cmd == "graph":
        zones = () if args.include_template else ("nav", "footer")
        stats = graph.compute_metrics(store, args.url, ignore_zones=zones)
        print(json.dumps(stats, indent=2))
        if args.tree:
            graph.print_tree(graph.tree(store))
        print("\nTop PageRank :")
        print(store.df("""SELECT p.url, m.pagerank, m.unique_inlinks, m.depth_click
                          FROM graph_metrics m JOIN pages p ON p.url=m.url
                          ORDER BY m.pagerank DESC LIMIT 20""").to_string(index=False))

    elif args.cmd == "gsc":
        if args.import_csv:
            gsc.import_csv(store, args.import_csv, page=args.page, period=args.period,
                           country=args.country or "all", device=args.device)
        elif args.site:
            gsc.sync(store, args.site, months=args.months,
                     credentials_path=args.credentials, country=args.country)
        else:
            p.error("gsc demande soit une propriété SITE, soit --import-csv FICHIER")

    elif args.cmd == "semantic":
        print(f"[lexique] {len(semantic.build_lexicon(store))} termes")
        if args.embed or args.method == "embedding":
            print(f"[embeddings] {semantic.embed(store)} pages vectorisées")
        n = semantic.suggest_links(store, method=args.method,
                                   top_k=args.top_k, min_score=args.min_score)
        print(f"[maillage] {n} suggestions")
        print(store.df("""SELECT source, target, anchor, round(score,3) score
                          FROM link_suggestions ORDER BY score DESC LIMIT 25""").to_string(index=False))

    elif args.cmd == "brief":
        d = briefs.dossier(store, args.keyword)
        md = briefs.generate(d, args.kind) if args.llm else briefs.brief_markdown(d, args.kind)
        out = Path(args.out or f"out/brief-{args.kind}-{args.keyword.replace(' ', '-')}.md")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        print(f"écrit -> {out}")


    elif args.cmd == "review":
        from . import review as review_mod

        text = Path(args.file).read_text(encoding="utf-8")
        if args.dry:
            m = review_mod.structural_audit(text)
            g = review_mod.geo_score(m)
            print(json.dumps({"metriques": m, "geo": g}, ensure_ascii=False,
                             indent=2, default=str))
        else:
            res = review_mod.review(
                text, store=store, query=args.query, max_iter=args.max_iter,
                target=args.target, dimensions=tuple(args.dimensions.split(",")),
                doc_id=Path(args.file).stem)
            out = Path(args.out or f"out/{Path(args.file).stem}")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.with_suffix(".revu.md").write_text(res["texte"], encoding="utf-8")
            out.with_suffix(".rapport.md").write_text(
                review_mod.report_markdown(res), encoding="utf-8")
            print(f"score final {res['score_final']}/100 -> "
                  f"{out.with_suffix('.revu.md')} + {out.with_suffix('.rapport.md')}")

    elif args.cmd == "report":
        md = briefs.monthly_report(store, args.period)
        out = Path(args.out or f"out/report-{args.period or 'last'}.md")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        print(f"écrit -> {out}")

    elif args.cmd == "export":
        tables = ["pages", "links", "headings", "graph_metrics", "gsc",
                  "link_suggestions", "lexicon"]
        Path(args.out).mkdir(parents=True, exist_ok=True)
        if args.format == "xlsx":
            import pandas as pd

            with pd.ExcelWriter(f"{args.out}/ecosysteme.xlsx") as w:
                for t in tables:
                    store.df(f"SELECT * FROM {t} LIMIT 500000").to_excel(w, sheet_name=t[:31], index=False)
        else:
            for t in tables:
                store.df(f"SELECT * FROM {t}").to_csv(f"{args.out}/{t}.csv", index=False)
        print(f"exporté -> {args.out}")

    elif args.cmd == "audit":
        checks = {
            "pages 200": "SELECT COUNT(*) FROM pages WHERE status=200",
            "erreurs 4xx": "SELECT COUNT(*) FROM pages WHERE status BETWEEN 400 AND 499",
            "erreurs 5xx": "SELECT COUNT(*) FROM pages WHERE status>=500",
            "redirections": "SELECT COUNT(*) FROM pages WHERE redirect_to IS NOT NULL",
            "noindex": "SELECT COUNT(*) FROM pages WHERE meta_robots LIKE '%noindex%'",
            "sans title": "SELECT COUNT(*) FROM pages WHERE status=200 AND (title IS NULL OR title='')",
            "sans meta desc": "SELECT COUNT(*) FROM pages WHERE status=200 AND (meta_desc IS NULL OR meta_desc='')",
            "titles dupliqués": "SELECT COUNT(*) FROM (SELECT title FROM pages WHERE status=200 AND title<>'' GROUP BY title HAVING COUNT(*)>1)",
            "sans H1": "SELECT COUNT(*) FROM pages WHERE status=200 AND h1 IS NULL",
            "contenu < 300 mots": "SELECT COUNT(*) FROM pages WHERE status=200 AND word_count < 300",
            "orphelines": "SELECT COUNT(*) FROM graph_metrics WHERE is_orphan=1",
            "profondeur > 4": "SELECT COUNT(*) FROM graph_metrics WHERE depth_click > 4",
            "hors sitemap": "SELECT COUNT(*) FROM pages WHERE status=200 AND in_sitemap=0",
        }
        for label, q in checks.items():
            try:
                print(f"{label:.<28} {store.conn.execute(q).fetchone()[0]}")
            except Exception as e:
                print(f"{label:.<28} n/a ({e})")

    store.close()


if __name__ == "__main__":
    main()
