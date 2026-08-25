# SEO Ecosystem — outil consultant SEO

Une base SQLite par site, avec des modules qui s'empilent : crawl, graphe interne, Search Console, analyse sémantique, briefs et relecture E-E-A-T/GEO.

```text
crawl ──► pages / headings / links / html brut
            │
            ├─► graph    : PageRank interne, profondeur de clic, orphelines
            ├─► gsc      : requêtes × URL × mois
            ├─► semantic : lexique, embeddings, suggestions de maillage
            ├─► brief    : données du site + GSC → brief éditorial
            └─► review   : audit structurel + relecture E-E-A-T / GEO / rédaction
```

## 1. Installation

Ouvre ce dossier dans VS Code, puis dans le terminal :

### Windows PowerShell

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Si PowerShell bloque l'activation, tu peux lancer les commandes avec `.venv\Scripts\python.exe` directement.

Pour le rendu JavaScript optionnel :

```powershell
python -m playwright install chromium
```

## 2. Premier test recommandé

Commence par un petit crawl de ton propre site :

```powershell
python -m seotool crawl https://ton-domaine.fr --max-pages 100
```

Puis affiche l'audit :

```powershell
python -m seotool --db data/ton-domaine.fr.db audit
```

Le nom réel du fichier SQLite dépend du domaine. Le crawl affiche également la base utilisée.

## Interface locale

Depuis le dossier `seo-ecosystem`, lance :

```powershell
python -m streamlit run app.py
```

Le navigateur ouvre l'application locale (généralement `http://localhost:8501`). Elle permet de
sélectionner une base client, consulter les indicateurs et priorités, importer un CSV GSC,
explorer le maillage, générer un brief déterministe et lancer un nouveau crawl. Les données
restent sur l'ordinateur dans le dossier `data/`.

## 3. Commandes principales

```powershell
# Crawl
python -m seotool crawl https://exemple.fr --max-pages 300

# Graphe interne
python -m seotool graph https://exemple.fr --tree

# Search Console
python -m seotool --db data/exemple.fr.db gsc sc-domain:exemple.fr --months 16

# Import d'un export GSC filtré sur une URL (aucun accès au compte requis)
python -m seotool --db data/exemple.fr.db gsc --import-csv export-gsc.csv --page https://exemple.fr/page/ --period 2026-07

# Analyse sémantique
python -m seotool --db data/exemple.fr.db semantic --method tfidf --top-k 5

# Brief sans LLM
python -m seotool --db data/exemple.fr.db brief "mot clé" --kind article

# Audit déterministe d'un article, gratuit
python -m seotool review draft.md --query "mot clé" --dry

# Audit technique
python -m seotool --db data/exemple.fr.db audit

# Export
python -m seotool --db data/exemple.fr.db export --format xlsx
```

## 4. Relecture E-E-A-T / GEO avec LLM

Le mode `--dry` ne nécessite aucune API et calcule des métriques structurelles reproductibles : réponse directe en tête, chunks autonomes, H2 interrogatifs, statistiques, sources, tableaux, listes, FAQ, phrases longues et formulations creuses.

Le mode LLM ajoute trois passes spécialisées :

- `eeat` : expérience, expertise, autorité et fiabilité ;
- `geo` : citabilité/extractibilité probable par les moteurs génératifs ;
- `redac` : qualité rédactionnelle française.

Il utilise actuellement l'API Anthropic si `ANTHROPIC_API_KEY` est configurée. Sans crédit API, utilise `--dry` : le reste de l'outil (crawl, graphe, GSC, sémantique, brief déterministe) reste utilisable.

```powershell
$env:ANTHROPIC_API_KEY="ta-cle"
python -m seotool --db data/exemple.fr.db review draft.md --query "mot clé"
```

L'éditeur n'invente pas de chiffre ni de source : lorsqu'une correction exige un fait absent du contexte, il doit insérer `[[À VÉRIFIER : ...]]`.

## 5. Important : SERP retirée

La dépendance aux APIs SERP payantes a été retirée de cette version. Les briefs reposent donc sur les données du site, la GSC, les plans Hn internes et le vocabulaire du corpus. L'outil ne prétend pas connaître les concurrents ou la SERP sans source externe.

E-E-A-T n'est pas une note officielle calculée par Google. Le score GEO de l'outil est également une grille interne expérimentale, pas une métrique officielle d'un moteur.

## 6. Structure du projet

```text
seo-ecosystem/
├── seotool/
│   ├── __init__.py
│   ├── __main__.py
│   ├── briefs.py
│   ├── cli.py
│   ├── crawler.py
│   ├── graph.py
│   ├── gsc.py
│   ├── parser.py
│   ├── review.py
│   ├── semantic.py
│   └── store.py
├── README.md
├── requirements.txt
└── config.example.yaml
```

## 7. Étape suivante

Ne passe pas encore à Streamlit/Postgres. Valide d'abord un crawl réel et la commande `audit`. Quand les données extraites sont bonnes, la plateforme web peut être construite au-dessus de ce socle.
