"""Interface locale Streamlit de SEO Ecosystem."""
from __future__ import annotations

import hmac
import os
import re
from pathlib import Path

import pandas as pd
import streamlit as st

from seotool import briefs, graph, gsc, recommendations, semantic
from seotool.cli import db_for
from seotool.crawler import crawl
from seotool.store import Store


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
IMPORT_DIR = ROOT / "imports"
DATA_DIR.mkdir(exist_ok=True)
IMPORT_DIR.mkdir(exist_ok=True)

st.set_page_config(page_title="SEO Ecosystem", page_icon="🧭", layout="wide")


def require_password() -> None:
    """Protège l'interface si APP_PASSWORD est défini dans les secrets."""
    expected = os.environ.get("APP_PASSWORD")
    try:
        expected = st.secrets.get("APP_PASSWORD", expected)
    except (FileNotFoundError, KeyError):
        pass
    if not expected:
        return  # mode local sans secret
    if st.session_state.get("authenticated"):
        return
    st.title("SEO Ecosystem")
    st.caption("Accès protégé")
    supplied = st.text_input("Mot de passe", type="password")
    if st.button("Se connecter"):
        if hmac.compare_digest(supplied, str(expected)):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Mot de passe incorrect.")
    st.stop()


require_password()


def databases() -> list[Path]:
    return sorted(DATA_DIR.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)


def scalar(store: Store, sql: str, params=()):
    return store.conn.execute(sql, params).fetchone()[0]


def safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-.") or "gsc.csv"


st.title("SEO Ecosystem")
st.caption("Crawl, GSC, priorités, maillage et briefs — en local, une base par client.")

dbs = databases()
if not dbs:
    st.warning("Aucun projet disponible. Crée un premier crawl dans l'onglet Administration.")
    selected_db = None
else:
    labels = {p.name: p for p in dbs}
    selected_name = st.sidebar.selectbox("Client / base", list(labels), index=0)
    selected_db = labels[selected_name]
    st.sidebar.caption(str(selected_db.relative_to(ROOT)))

tabs = st.tabs(["Vue d'ensemble", "Opportunités contenu", "Priorités", "Maillage",
                "Brief", "Import GSC", "Administration"])

if selected_db:
    store = Store(selected_db)

    with tabs[0]:
        pages = scalar(store, "SELECT COUNT(*) FROM pages WHERE status=200")
        gsc_rows = scalar(store, "SELECT COUNT(*) FROM gsc")
        suggestions = scalar(store, "SELECT COUNT(*) FROM link_suggestions")
        orphans = scalar(store, "SELECT COUNT(*) FROM graph_metrics WHERE is_orphan=1")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Pages HTTP 200", pages)
        c2.metric("Relations GSC", gsc_rows)
        c3.metric("Suggestions de liens", suggestions)
        c4.metric("Pages orphelines", orphans)

        st.subheader("Santé technique")
        health = pd.DataFrame([
            ("Sans title", scalar(store, "SELECT COUNT(*) FROM pages WHERE status=200 AND COALESCE(title,'')=''")),
            ("Sans meta description", scalar(store, "SELECT COUNT(*) FROM pages WHERE status=200 AND COALESCE(meta_desc,'')=''")),
            ("Sans H1", scalar(store, "SELECT COUNT(*) FROM pages WHERE status=200 AND COALESCE(h1,'')=''")),
            ("Moins de 300 mots", scalar(store, "SELECT COUNT(*) FROM pages WHERE status=200 AND word_count<300")),
            ("Noindex", scalar(store, "SELECT COUNT(*) FROM pages WHERE meta_robots LIKE '%noindex%'")),
        ], columns=["contrôle", "pages"])
        st.bar_chart(health.set_index("contrôle"))

        top = store.df(
            """SELECT page url, SUM(impressions) impressions, SUM(clicks) clics,
                      ROUND(SUM(clicks)*100.0/NULLIF(SUM(impressions),0),2) ctr_pct,
                      ROUND(SUM(position*impressions)/NULLIF(SUM(impressions),0),1) position
               FROM gsc GROUP BY page ORDER BY impressions DESC LIMIT 20"""
        )
        st.subheader("Pages les plus visibles dans GSC")
        st.dataframe(top, width="stretch", hide_index=True)

    with tabs[1]:
        st.subheader("Opportunités de création et d'optimisation")
        st.caption("Vue éditoriale uniquement : les erreurs techniques restent dans Priorités.")
        content_min_impr = st.number_input(
            "Impressions minimales pour les opportunités", min_value=0, value=100,
            step=50, key="content_min_impr",
        )
        content_recs = recommendations.build(store, int(content_min_impr))
        creation = content_recs[content_recs["action"].str.contains("création", case=False, na=False)]
        optimization = content_recs[
            (content_recs["catégorie"] == "GSC") |
            (content_recs["action"].str.contains("enrichir", case=False, na=False))
        ]
        cannibalization = content_recs[content_recs["catégorie"] == "Cannibalisation"]

        c1, c2, c3 = st.columns(3)
        c1.metric("Créations à étudier", len(creation))
        c2.metric("Pages à optimiser", len(optimization))
        c3.metric("Conflits de ciblage", len(cannibalization))

        opportunity_view = st.radio(
            "Afficher", ["Créations", "Optimisations", "Cannibalisations"],
            horizontal=True,
        )
        selected_opportunities = {
            "Créations": creation,
            "Optimisations": optimization,
            "Cannibalisations": cannibalization,
        }[opportunity_view]
        st.dataframe(
            selected_opportunities, width="stretch", hide_index=True,
            column_config={"url": st.column_config.LinkColumn("URL actuelle")},
        )
        st.download_button(
            "Télécharger cette vue (CSV)",
            selected_opportunities.to_csv(index=False).encode("utf-8-sig"),
            f"opportunites-{opportunity_view.lower()}.csv", "text/csv",
        )
        st.info(
            "Une création est une hypothèse lorsque la page actuelle couvre mal la requête. "
            "Vérifie toujours l'intention et le risque de cannibalisation avant publication."
        )

    with tabs[2]:
        st.subheader("File d'actions priorisée")
        min_impr = st.number_input("Impressions minimales", min_value=0, value=100, step=50)
        recs = recommendations.build(store, int(min_impr))
        categories = sorted(recs["catégorie"].unique()) if len(recs) else []
        chosen = st.multiselect("Catégories", categories, default=categories)
        shown = recs[recs["catégorie"].isin(chosen)] if chosen else recs.iloc[0:0]
        st.dataframe(shown, width="stretch", hide_index=True,
                     column_config={"url": st.column_config.LinkColumn("URL")})
        st.download_button("Télécharger les priorités (CSV)", shown.to_csv(index=False).encode("utf-8-sig"),
                           "priorites-seo.csv", "text/csv")
        st.caption("Les intentions sont des inférences avec un niveau de confiance, pas des certitudes SERP.")

    with tabs[3]:
        st.subheader("Suggestions de maillage")
        links = store.df(
            """SELECT source, target, anchor, ROUND(score,3) score, evidence contexte
               FROM link_suggestions ORDER BY score DESC"""
        )
        only_context = st.checkbox("Afficher uniquement les ancres trouvées dans le texte source")
        if only_context:
            links = links[links["contexte"].notna()]
        st.dataframe(links, width="stretch", hide_index=True,
                     column_config={"source": st.column_config.LinkColumn("Source"),
                                    "target": st.column_config.LinkColumn("Cible")})
        st.download_button("Télécharger le maillage (CSV)", links.to_csv(index=False).encode("utf-8-sig"),
                           "suggestions-maillage.csv", "text/csv")
        if st.button("Recalculer TF-IDF + GSC + structure"):
            with st.spinner("Calcul du lexique et des suggestions…"):
                semantic.build_lexicon(store)
                count = semantic.suggest_links(store)
            st.success(f"{count} suggestions recalculées.")
            st.rerun()

    with tabs[4]:
        st.subheader("Brief à la demande")
        keyword = st.text_input("Mot-clé ou sujet", placeholder="ex. assurance hospitalisation")
        kind = st.selectbox("Format demandé", ["Automatique", "Article", "Landing page", "Article sponsorisé"])
        if keyword:
            intent = recommendations.detect_intent(keyword)
            c1, c2, c3 = st.columns(3)
            c1.metric("Intention probable", intent["intent"])
            c2.metric("Confiance", f"{intent['confidence']} %")
            c3.metric("Format suggéré", intent["content_type"])
        if st.button("Générer le brief déterministe", disabled=not bool(keyword)):
            dossier = briefs.dossier(store, keyword)
            mapped_kind = {"Landing page": "lp", "Article sponsorisé": "sponso"}.get(kind, "article")
            md = briefs.brief_markdown(dossier, mapped_kind)
            st.session_state["brief_md"] = md
        if st.session_state.get("brief_md"):
            st.markdown(st.session_state["brief_md"])
            d1, d2 = st.columns(2)
            d1.download_button("Télécharger en Markdown", st.session_state["brief_md"].encode("utf-8"),
                               "brief.md", "text/markdown")
            try:
                docx_data = briefs.markdown_to_docx(
                    st.session_state["brief_md"], title=f"Brief SEO — {keyword or 'contenu'}"
                )
            except ImportError:
                d2.warning("Installe python-docx pour activer Word.")
            else:
                d2.download_button(
                    "Télécharger en Word (.docx)", docx_data, "brief.docx",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )

    with tabs[5]:
        st.subheader("Importer un export URL × Query")
        st.caption(
            "Cet espace sert à alimenter ou actualiser le cerveau GSC du client. "
            "Les analyses produites à partir de ces données apparaissent dans Vue d'ensemble, "
            "Opportunités contenu, Priorités, Maillage et Brief."
        )
        uploaded = st.file_uploader("CSV GSC", type=["csv"])
        period = st.text_input("Période", placeholder="2026-07 ou 8m-ending-2026-08")
        if uploaded and st.button("Importer dans ce client"):
            target = IMPORT_DIR / safe_name(uploaded.name)
            target.write_bytes(uploaded.getvalue())
            try:
                count = gsc.import_csv(store, target, period=period or None)
            except Exception as exc:
                st.error(str(exc))
            else:
                st.success(f"{count} relations importées.")
                st.rerun()
        periods = store.df(
            """SELECT period période, COUNT(*) lignes, SUM(impressions) impressions,
                      SUM(clicks) clics FROM gsc GROUP BY period ORDER BY period DESC"""
        )
        st.subheader("Périodes disponibles")
        st.dataframe(periods, width="stretch", hide_index=True)

    store.close()

else:
    for tab in tabs[:6]:
        with tab:
            st.info("Crée d'abord un projet dans Administration.")

with tabs[6]:
    st.subheader("Créer ou actualiser un projet")
    site_url = st.text_input("URL du site", placeholder="https://exemple.fr")
    max_pages = st.number_input("Maximum d'URL", min_value=10, max_value=10000, value=300, step=50)
    if st.button("Lancer le crawl", disabled=not bool(site_url)):
        relative_db = db_for(site_url)
        db_path = ROOT / relative_db
        with st.spinner("Crawl en cours — garde cette page ouverte…"):
            try:
                crawl(site_url, str(db_path), max_pages=int(max_pages))
                project = Store(db_path)
                graph.compute_metrics(project, site_url)
                project.close()
            except Exception as exc:
                st.exception(exc)
            else:
                st.success(f"Projet créé : {relative_db}")
                st.rerun()

st.sidebar.markdown("---")
st.sidebar.caption("Les données restent sur cet ordinateur.")
