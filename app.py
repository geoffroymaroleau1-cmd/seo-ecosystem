"""Interface locale Streamlit de SEO Ecosystem."""
from __future__ import annotations

import hmac
import os
import re
from pathlib import Path

import pandas as pd
import streamlit as st

from seotool import briefs, data_imports, graph, gsc, preaudit, recommendations, semantic
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

st.markdown("""
<style>
    :root {--ink:#172033; --muted:#667085; --line:#e8eaf0; --brand:#6558e8; --soft:#f4f2ff;}
    .stApp {background:linear-gradient(180deg,#f7f8fc 0,#fff 420px); color:var(--ink);}
    [data-testid="stHeader"] {background:rgba(247,248,252,.78); backdrop-filter:blur(14px);}
    [data-testid="stSidebar"] {background:#15182b; border-right:0;}
    [data-testid="stSidebar"] * {color:#eef0ff;}
    [data-testid="stSidebar"] [data-baseweb="select"] > div {background:#232740; border-color:#353a59;}
    .block-container {padding-top:1.35rem; padding-bottom:4rem; max-width:1480px;}
    .hero {position:relative; overflow:hidden; padding:2.2rem 2.4rem; border-radius:28px; color:white;
           margin-bottom:1.25rem; background:linear-gradient(125deg,#15182b 0%,#4038a8 58%,#7869ef 100%);
           box-shadow:0 22px 60px rgba(48,43,130,.23);}
    .hero:after {content:""; position:absolute; width:360px; height:360px; right:-90px; top:-190px;
                 border:1px solid rgba(255,255,255,.22); border-radius:50%; box-shadow:0 0 0 55px rgba(255,255,255,.04),0 0 0 110px rgba(255,255,255,.035);}
    .hero-kicker {font-size:.72rem; font-weight:800; letter-spacing:.14em; text-transform:uppercase;
                  color:#c9c5ff; margin-bottom:.65rem;}
    .hero h1 {font-size:clamp(2rem,4vw,3.25rem); line-height:1.03; letter-spacing:-.045em; margin:0 0 .65rem; color:white;}
    .hero p {font-size:1.02rem; line-height:1.65; opacity:.86; margin:0; max-width:760px;}
    .hero-badge {display:inline-flex; margin-top:1.25rem; padding:.45rem .75rem; border-radius:99px;
                 background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.18); font-size:.78rem;}
    .journey {display:flex; gap:.5rem; align-items:center; margin:.25rem 0 1.15rem; color:var(--muted); font-size:.78rem; font-weight:700;}
    .journey span {padding:.42rem .7rem; border-radius:99px; background:white; border:1px solid var(--line);}
    .journey b {color:#b1b5c2;}
    div[data-baseweb="tab-list"] {gap:.4rem; background:white; padding:.5rem; border:1px solid var(--line);
                                  border-radius:16px; box-shadow:0 8px 26px rgba(24,32,51,.06); overflow-x:auto;}
    button[data-baseweb="tab"] {height:42px; border-radius:11px; padding:0 .85rem; color:#667085; white-space:nowrap;}
    button[data-baseweb="tab"][aria-selected="true"] {background:var(--soft); color:#4b40c8; font-weight:750;}
    div[data-baseweb="tab-highlight"], div[data-baseweb="tab-border"] {display:none;}
    .section-hero {display:flex; gap:1rem; align-items:flex-start; padding:1.35rem 1.5rem; margin:1rem 0 1.15rem;
                   background:white; border:1px solid var(--line); border-radius:20px; box-shadow:0 8px 24px rgba(24,32,51,.045);}
    .section-icon {display:flex; flex:0 0 46px; height:46px; align-items:center; justify-content:center; border-radius:14px;
                   background:var(--soft); font-size:1.35rem;}
    .section-hero h2 {font-size:1.32rem; letter-spacing:-.025em; margin:0 0 .25rem; color:var(--ink);}
    .section-hero p {margin:0; color:var(--muted); line-height:1.5;}
    .section-tip {margin-top:.55rem; color:#4b40c8; font-size:.82rem; font-weight:650;}
    .process-card {border:1px solid var(--line); border-radius:18px; padding:1.2rem; min-height:160px;
                   background:white; box-shadow:0 7px 22px rgba(24,32,51,.04); transition:.2s ease;}
    .process-card:hover {transform:translateY(-3px); box-shadow:0 13px 30px rgba(48,43,130,.10); border-color:#d8d3ff;}
    .process-card .number {display:inline-grid; place-items:center; color:#4b40c8; background:var(--soft);
                          border-radius:10px; width:34px; height:34px; font-weight:800;}
    .process-card h3 {font-size:1.02rem; margin:.8rem 0 .35rem;}
    .process-card p {color:var(--muted); font-size:.89rem; line-height:1.5; margin:0;}
    [data-testid="stMetric"] {background:white; border:1px solid var(--line); padding:1rem 1.1rem;
                             border-radius:16px; box-shadow:0 7px 20px rgba(15,23,42,.045);}
    [data-testid="stMetricLabel"] {color:var(--muted);}
    .need-box {padding:1rem 1.1rem; border:1px solid var(--line); background:white; border-radius:16px;
               margin:.35rem 0; min-height:92px; box-shadow:0 6px 18px rgba(24,32,51,.035);}
    .need-box b {color:#4b40c8; display:inline-block; margin-bottom:.25rem;}
    .project-card {padding:.9rem; margin:.5rem 0 1.25rem; background:#232740; border:1px solid #353a59; border-radius:14px;}
    .project-card small {display:block; color:#aeb4d5 !important; margin-bottom:.2rem;}
    .project-card strong {color:white !important; font-size:.92rem;}
    .stButton > button, .stDownloadButton > button {border-radius:11px; font-weight:700; min-height:42px;}
    .stButton > button[kind="primary"] {background:var(--brand); border-color:var(--brand);}
    [data-testid="stDataFrame"] {border:1px solid var(--line); border-radius:14px; overflow:hidden;}
    @media(max-width:700px){.hero{padding:1.6rem}.journey{display:none}.section-hero{padding:1rem}.block-container{padding-top:.7rem}}
</style>
""", unsafe_allow_html=True)


def databases() -> list[Path]:
    return sorted(DATA_DIR.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)


def scalar(store: Store, sql: str, params=()):
    return store.conn.execute(sql, params).fetchone()[0]


def safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-.") or "gsc.csv"


def section_intro(icon: str, title: str, description: str, tip: str) -> None:
    """Affiche le mode d'emploi compact de chaque espace de travail."""
    st.markdown(
        f'<div class="section-hero"><div class="section-icon">{icon}</div><div>'
        f'<h2>{title}</h2><p>{description}</p><div class="section-tip">→ {tip}</div>'
        f'</div></div>', unsafe_allow_html=True,
    )


st.markdown("""
<div class="hero">
  <div class="hero-kicker">Votre cockpit de croissance organique</div>
  <h1>SEO Ecosystem</h1>
  <p>Du signal brut à la prochaine action : explorez votre site, comprenez ce qui freine sa visibilité
  et transformez chaque insight en décision SEO concrète.</p>
  <div class="hero-badge">✦ Crawl · Search Console · Sémantique · Contenu</div>
</div>
<div class="journey"><span>01 · Diagnostiquer</span><b>›</b><span>02 · Décider</span><b>›</b><span>03 · Agir</span><b>›</b><span>04 · Mesurer</span></div>
""", unsafe_allow_html=True)

dbs = databases()
if not dbs:
    st.warning("Aucun projet disponible. Crée un premier crawl dans l'onglet Administration.")
    selected_db = None
else:
    labels = {p.name: p for p in dbs}
    selected_name = st.sidebar.selectbox("Client / base", list(labels), index=0)
    selected_db = labels[selected_name]
    st.sidebar.markdown(
        f'<div class="project-card"><small>PROJET ACTIF</small><strong>{selected_db.stem}</strong></div>',
        unsafe_allow_html=True,
    )

tabs = st.tabs(["⌂  Vue d'ensemble", "◎  Pré-audit", "✦  Opportunités", "⚡  Priorités", "↗  Maillage",
                "✎  Brief", "⊕  Données", "⚙  Projet"])

if selected_db:
    store = Store(selected_db)

    with tabs[0]:
        section_intro(
            "⌂", "Vue d'ensemble", "Le point de départ pour comprendre la santé du projet et choisir votre prochaine étape.",
            "Commencez par les indicateurs rouges, puis ouvrez l'espace qui correspond à votre objectif.",
        )
        st.markdown("### Votre parcours en 4 étapes")
        process_cols = st.columns(4)
        steps = [
            ("01", "Créer ou choisir un projet", "Un domaine correspond à une base client séparée."),
            ("02", "Collecter les données", "Lancez le crawl puis ajoutez GSC ou vos exports disponibles."),
            ("03", "Comprendre les priorités", "Consultez le pré-audit, les opportunités et le maillage."),
            ("04", "Passer à l'action", "Téléchargez les recommandations, rapports et briefs Word."),
        ]
        for col, (number, title, copy) in zip(process_cols, steps):
            col.markdown(
                f'<div class="process-card"><span class="number">{number}</span>'
                f'<h3>{title}</h3><p>{copy}</p></div>', unsafe_allow_html=True,
            )

        st.markdown("### Quel est votre objectif aujourd'hui ?")
        n1, n2, n3 = st.columns(3)
        n1.markdown('<div class="need-box"><b>Convaincre un prospect</b><br>Ouvrez Pré-audit pour une synthèse illustrée et téléchargeable.</div>', unsafe_allow_html=True)
        n2.markdown('<div class="need-box"><b>Trouver de la croissance</b><br>Ouvrez Opportunités contenu pour créer, optimiser ou consolider.</div>', unsafe_allow_html=True)
        n3.markdown('<div class="need-box"><b>Produire un contenu</b><br>Ouvrez Brief, saisissez le sujet puis téléchargez le document Word.</div>', unsafe_allow_html=True)

        st.divider()
        st.subheader("Vue rapide du projet")
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
        section_intro(
            "◎", "Pré-audit", "Une photographie simple et partageable des principaux signaux techniques du site.",
            "Lisez d'abord le score global, identifiez la dimension la plus faible, puis ouvrez les actions recommandées.",
        )
        audit = preaudit.snapshot(store)
        a1, a2, a3, a4 = st.columns(4)
        a1.metric("Score de contrôle", f"{audit['score']}/100")
        a2.metric("URL observées", audit["total"])
        a3.metric("Erreurs HTTP", audit["errors"])
        a4.metric("Pages orphelines", audit["orphans"])

        left, right = st.columns(2)
        with left:
            st.markdown("#### Qualité par dimension")
            score_frame = pd.DataFrame(
                {"dimension": list(audit["scores"]), "score": list(audit["scores"].values())}
            ).set_index("dimension")
            st.bar_chart(score_frame, horizontal=True)
        with right:
            st.markdown("#### Répartition des statuts HTTP")
            if len(audit["status"]):
                st.bar_chart(audit["status"].set_index("catégorie"))
            st.markdown("#### Profondeur de clic")
            if len(audit["depths"]):
                st.bar_chart(audit["depths"].set_index("profondeur"))

        st.markdown("#### Erreurs et points de vigilance")
        st.dataframe(audit["issues"], width="stretch", hide_index=True)
        st.markdown("#### Premières actions recommandées")
        st.dataframe(
            audit["priorities"], width="stretch", hide_index=True,
            column_config={"url": st.column_config.LinkColumn("URL")},
        )
        audit_md = preaudit.report_markdown(audit, selected_db.stem)
        pa1, pa2 = st.columns(2)
        pa1.download_button("Télécharger le pré-audit (.md)", audit_md.encode("utf-8"),
                            "pre-audit-seo.md", "text/markdown")
        pa2.download_button(
            "Télécharger le pré-audit Word (.docx)",
            briefs.markdown_to_docx(audit_md, f"Pré-audit SEO — {selected_db.stem}"),
            "pre-audit-seo.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        st.info("Le score est une synthèse interne des contrôles affichés, pas une note Google.")

    with tabs[2]:
        section_intro(
            "✦", "Opportunités de contenu", "Les pistes éditoriales détectées à partir des requêtes et des pages existantes.",
            "Choisissez une vue : créer pour couvrir un manque, optimiser pour gagner des positions, consolider pour éviter la cannibalisation.",
        )
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

    with tabs[3]:
        section_intro(
            "⚡", "Priorités", "Votre file de travail SEO, triée pour concentrer l'effort là où il peut avoir le plus d'impact.",
            "Ajustez le seuil d'impressions, filtrez les catégories utiles, puis exportez la sélection pour votre roadmap.",
        )
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

    with tabs[4]:
        section_intro(
            "↗", "Maillage interne", "Des connexions suggérées entre pages pour mieux distribuer l'autorité et guider les visiteurs.",
            "La source accueille le lien, la cible reçoit l'autorité, l'ancre est le texte à utiliser et le score indique la pertinence.",
        )
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

    with tabs[5]:
        section_intro(
            "✎", "Atelier de brief", "Transformez un sujet en cadre de rédaction cohérent avec les données déjà collectées.",
            "Saisissez un sujet, vérifiez l'intention détectée, choisissez le format puis générez le document prêt à transmettre.",
        )
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

    with tabs[6]:
        section_intro(
            "⊕", "Sources de données", "Enrichissez progressivement l'analyse : le crawl est le socle, GSC et Semrush ajoutent la performance et la concurrence.",
            "Les compteurs ci-dessous montrent immédiatement ce qui est prêt. Importez seulement les sources dont vous disposez.",
        )

        st.markdown("#### Où en est ce projet ?")
        source_counts = {
            "Crawl": scalar(store, "SELECT COUNT(*) FROM pages"),
            "GSC URL × requête": scalar(store, "SELECT COUNT(*) FROM gsc"),
            "Positions Semrush": scalar(store, "SELECT COUNT(*) FROM semrush_keywords"),
            "Keyword Gap": scalar(store, "SELECT COUNT(*) FROM keyword_gap"),
            "Backlinks": scalar(store, "SELECT COUNT(*) FROM backlinks"),
        }
        cols = st.columns(5)
        for col, (name, count) in zip(cols, source_counts.items()):
            col.metric(name, count, "chargé" if count else "manquant")

        st.info(
            "**Parcours recommandé :** 1. lancez le crawl dans Administration ; "
            "2. importez GSC si disponible ; 3. ajoutez les positions Semrush ; "
            "4. complétez avec Keyword Gap et Backlinks pour la concurrence et l'off-site."
        )

        def save_upload(upload):
            target = IMPORT_DIR / safe_name(upload.name)
            target.write_bytes(upload.getvalue())
            return target

        with st.expander("1 — GSC : requêtes associées aux URL", expanded=True):
            st.markdown(
                "**But :** repérer les pages à optimiser, les requêtes presque en première page "
                "et les risques de cannibalisation.\n\n"
                "**Colonnes minimales :** `URL`, `Query`, `Impressions`, `Url Clicks`, "
                "`URL CTR`, `Average Position`. Une ligne doit relier une URL à une requête."
            )
            uploaded = st.file_uploader("Choisir le CSV GSC", type=["csv"], key="gsc_upload")
            period = st.text_input("Période couverte", placeholder="ex. 8m-ending-2026-08", key="gsc_period")
            if st.button("Importer les données GSC", disabled=not bool(uploaded)):
                try:
                    count = gsc.import_csv(store, save_upload(uploaded), period=period or None)
                except Exception as exc:
                    st.error(str(exc))
                else:
                    st.success(f"{count} relations URL × requête importées.")
                    st.rerun()

        with st.expander("2 — Semrush : positions organiques et URL"):
            st.markdown(
                "**Export à choisir :** Organic Research / Positions.\n\n"
                "**Colonnes minimales :** `Keyword`, `URL`, `Position`. Recommandées : "
                "`Volume`, `Traffic`, `Keyword Difficulty`, `CPC`, `Intent`."
            )
            upload = st.file_uploader("Choisir l'export Positions Semrush", type=["csv"], key="semrush_pos")
            label = st.text_input("Date ou période de l'export", key="semrush_pos_period")
            if st.button("Importer les positions Semrush", disabled=not bool(upload)):
                try:
                    count = data_imports.import_semrush_keywords(store, save_upload(upload), label or "non précisé")
                except Exception as exc:
                    st.error(str(exc))
                else:
                    st.success(f"{count} positions importées.")
                    st.rerun()

        with st.expander("3 — Semrush : Keyword Gap et concurrents"):
            st.markdown(
                "**But :** trouver les mots-clés couverts par les concurrents mais absents ou faibles "
                "sur le domaine. Exportez le tableau Keyword Gap en CSV.\n\n"
                "**Colonne minimale :** `Keyword`. Recommandées : `Competitor`, positions du domaine "
                "et du concurrent, `Volume`, `KD`, `Intent`, `Status`."
            )
            upload = st.file_uploader("Choisir l'export Keyword Gap", type=["csv"], key="semrush_gap")
            label = st.text_input("Date ou période du gap", key="semrush_gap_period")
            if st.button("Importer le Keyword Gap", disabled=not bool(upload)):
                try:
                    count = data_imports.import_keyword_gap(store, save_upload(upload), label or "non précisé")
                except Exception as exc:
                    st.error(str(exc))
                else:
                    st.success(f"{count} opportunités concurrentielles importées.")
                    st.rerun()

        with st.expander("4 — Semrush : backlinks et domaines référents"):
            st.markdown(
                "**But :** alimenter le volet off-site : volume de liens, domaines référents, "
                "autorité, ancres et liens follow/nofollow.\n\n"
                "**Colonnes minimales :** `Source URL`, `Target URL`. Recommandées : "
                "`Source Domain`, `Authority Score`, `Anchor`, `Link Type`, `First Seen`, `Last Seen`."
            )
            upload = st.file_uploader("Choisir l'export Backlinks", type=["csv"], key="semrush_backlinks")
            label = st.text_input("Date ou période des backlinks", key="semrush_backlinks_period")
            if st.button("Importer les backlinks", disabled=not bool(upload)):
                try:
                    count = data_imports.import_backlinks(store, save_upload(upload), label or "non précisé")
                except Exception as exc:
                    st.error(str(exc))
                else:
                    st.success(f"{count} backlinks importés.")
                    st.rerun()

        periods = store.df(
            """SELECT period période, COUNT(*) lignes, SUM(impressions) impressions,
                      SUM(clicks) clics FROM gsc GROUP BY period ORDER BY period DESC"""
        )
        st.subheader("Historique GSC disponible")
        st.dataframe(periods, width="stretch", hide_index=True)

    store.close()

else:
    for tab in tabs[:7]:
        with tab:
            st.info("Crée d'abord un projet dans Administration.")

with tabs[7]:
    section_intro(
        "⚙", "Projet & crawl", "Créez un espace isolé par domaine ou actualisez les données techniques d'un site existant.",
        "Indiquez l'URL complète, fixez une limite adaptée à la taille du site, puis gardez la page ouverte pendant le crawl.",
    )
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
