"""Graphe de maillage interne : PageRank, profondeur de clic, orphelines, arborescence."""
from __future__ import annotations

from collections import defaultdict, deque
from urllib.parse import urlparse

import networkx as nx

from .parser import normalize_url
from .store import Store


def build_graph(store: Store, ignore_zones=("nav", "footer")) -> nx.DiGraph:
    """Graphe des liens internes. On peut ignorer nav/footer pour un PageRank
    'éditorial' qui reflète les vrais choix de maillage et non le template."""
    ok = {r[0] for r in store.conn.execute("SELECT url FROM pages WHERE status=200")}
    g = nx.DiGraph()
    g.add_nodes_from(ok)
    q = "SELECT source, target, zone, rel FROM links WHERE internal=1"
    for s, t, zone, rel in store.conn.execute(q):
        if s not in ok or t not in ok or s == t:
            continue
        if zone in ignore_zones:
            continue
        if rel and "nofollow" in rel.lower():
            continue
        g.add_edge(s, t)
    return g


def compute_metrics(store: Store, root: str, ignore_zones=("nav", "footer")) -> dict:
    g_full = build_graph(store, ignore_zones=())
    g_edit = build_graph(store, ignore_zones=ignore_zones)

    root = _resolve_root(g_full, root)
    pr = nx.pagerank(g_edit, alpha=0.85) if g_edit.number_of_edges() else {}
    depth = _click_depth(g_full, root)

    rows = []
    for url in g_full.nodes:
        inl = g_full.in_degree(url)
        rows.append((
            url,
            pr.get(url, 0.0),
            inl,
            g_full.out_degree(url),
            len(set(g_full.predecessors(url))),
            depth.get(url, -1),
            int(inl == 0 and url != root),
        ))
    with store.tx() as c:
        c.execute("DELETE FROM graph_metrics")
        c.executemany(
            "INSERT INTO graph_metrics (url,pagerank,inlinks,outlinks,unique_inlinks,depth_click,is_orphan) "
            "VALUES (?,?,?,?,?,?,?)", rows,
        )
    return {
        "root": root,
        "nodes": g_full.number_of_nodes(),
        "edges": g_full.number_of_edges(),
        "edges_editorial": g_edit.number_of_edges(),
        "orphans": sum(r[6] for r in rows),
        "unreachable": g_full.number_of_nodes() - len(depth),
        "max_depth": max((d for d in depth.values()), default=0),
    }


def _resolve_root(g: nx.DiGraph, root: str) -> str:
    """Return the graph node corresponding to the requested homepage.

    URLs produced by the crawler are normalized (notably with ``/`` as the
    empty path), while a CLI argument may not be.  If an HTTP redirect changed
    the hostname or scheme, fall back to a homepage node from the same host.
    """
    normalized = normalize_url(root)
    if normalized in g:
        return normalized
    if root in g:
        return root

    parsed = urlparse(normalized or root)
    wanted_host = parsed.netloc.lower().removeprefix("www.")
    candidates = []
    for url in g:
        candidate = urlparse(url)
        host = candidate.netloc.lower().removeprefix("www.")
        if host == wanted_host and candidate.path in ("", "/"):
            candidates.append(url)
    if candidates:
        # Prefer HTTPS, then the shortest canonical-looking URL.
        return min(candidates, key=lambda u: (urlparse(u).scheme != "https", len(u)))

    raise ValueError(
        f"La page racine {root!r} est absente des pages HTTP 200 du crawl. "
        "Relancez le crawl ou vérifiez l'URL fournie."
    )


def _click_depth(g: nx.DiGraph, root: str) -> dict[str, int]:
    depth, dq = {root: 0}, deque([root])
    while dq:
        n = dq.popleft()
        for m in g.successors(n):
            if m not in depth:
                depth[m] = depth[n] + 1
                dq.append(m)
    return depth


def tree(store: Store, max_depth: int = 4) -> dict:
    """Arborescence par segments d'URL, avec volumétrie et PageRank moyen."""
    node = lambda: {"pages": 0, "pr": 0.0, "children": defaultdict(node)}
    root = node()
    q = """SELECT p.url, COALESCE(m.pagerank,0) FROM pages p
           LEFT JOIN graph_metrics m ON m.url = p.url WHERE p.status=200"""
    for url, pr in store.conn.execute(q):
        segs = [s for s in urlparse(url).path.split("/") if s][:max_depth]
        cur = root
        cur["pages"] += 1
        cur["pr"] += pr
        for s in segs:
            cur = cur["children"][s]
            cur["pages"] += 1
            cur["pr"] += pr
    return root


def print_tree(n: dict, name: str = "/", indent: int = 0, min_pages: int = 1):
    print(f"{'  ' * indent}{name}  ({n['pages']} pages, PR {n['pr']:.4f})")
    for k, child in sorted(n["children"].items(), key=lambda kv: -kv[1]["pages"]):
        if child["pages"] >= min_pages:
            print_tree(child, "/" + k, indent + 1, min_pages)


def anchor_report(store: Store, limit: int = 50):
    """Ancres internes les plus utilisées : détecte les ancres génériques."""
    return store.df(
        """SELECT anchor, COUNT(*) n, COUNT(DISTINCT target) cibles
           FROM links WHERE internal=1 AND zone='content' AND anchor<>''
           GROUP BY lower(anchor) ORDER BY n DESC LIMIT ?""", (limit,)
    )
