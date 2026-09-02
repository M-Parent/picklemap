# 🏓 Picklemap — Terrains de pickleball • Québec & Ottawa

Application web statique qui agrège, géocode et affiche les terrains de pickleball municipaux du Québec et d'Ottawa. Construite pour tourner entièrement dans le navigateur (sql.js + Leaflet) et hébergée gratuitement sur **GitHub Pages** via un pipeline CI/CD automatique.

**Site en ligne :** https://m-parent.github.io/picklemap

---

## Fonctionnalités

- 🗺️ **Carte interactive** — visible sur mobile ET desktop, marqueurs verts/bleus selon type de terrain
- 📍 **GPS** — bouton pour se localiser et voir les terrains autour de soi
- 🔍 **Recherche** — filtre par ville ou par nom de parc
- 🏙️ **Chips de ville** — clic = zoom sur la ville dans la carte
- 📋 **Popup au clic** sur chaque marqueur — infos + bouton Itinéraire (Google Maps)
- 🔄 **Mise à jour automatique** chaque lundi via GitHub Actions

---

## Architecture du projet

```
picklemap/
├── scraper.py          # Collecte toutes les villes → construit terrains.db
├── index.html          # Application web (sql.js + Leaflet, zéro serveur)
├── terrains.db         # Base SQLite générée par le scraper (commitée pour CI/CD)
├── geocache.json       # Cache Nominatim (commitée pour éviter de re-géocoder)
├── requirements.txt    # Dépendances Python
└── .github/
    └── workflows/
        └── deploy.yml  # CI/CD : build → commit → déploiement GitHub Pages
```

---

## Scripts Python

### `scraper.py` — Le coeur du projet

Collecte les données de toutes les villes, les géocode et reconstruit `terrains.db`.

**Lancement :**
```bash
pip install -r requirements.txt
playwright install chromium   # uniquement pour Gatineau (page JS)
python3 scraper.py
```

**Ce qu'il fait :**

| Étape | Description |
|-------|-------------|
| Scraping | Appelle une fonction `scrape_<ville>()` par ville |
| Gatineau | Playwright (page rendue côté client) |
| Drummondville | API Overpass OpenStreetMap (contourne le robots.txt) |
| Ottawa | API ArcGIS REST → géométrie Web Mercator convertie en WGS84 |
| Autres villes | BeautifulSoup sur HTML statique |
| Géocodage | Nominatim OSM avec cache fichier (`geocache.json`) |
| Base de données | SQLite `terrains.db` avec colonnes lat/lng |

**Ordre de scraping :** Saguenay → Gatineau → Ottawa → Drummondville → autres villes

**Villes couvertes (176 terrains, 91 % géocodés) :**

| Ville | Méthode | Terrains |
|-------|---------|---------|
| Saguenay | HTML statique (`article.fiche`) | 13 |
| Gatineau | Playwright + `h2.__se__format__replace_h2` | 15 |
| Ottawa | API ArcGIS REST (géométrie native) | 103 |
| Drummondville | Overpass OSM (polygone → centroïde) | 1 |
| Montréal | Données curées (robots.txt) | 3 |
| Longueuil | HTML statique (`li` avec nb terrains) | 7 |
| Lévis | HTML statique (section pickleball) | 3 |
| Trois-Rivières | HTML statique (sections `h2` de parcs) | 5 |
| Québec | HTML statique (`h2` → contenu) | 18 |
| Magog | HTML statique + pages parcs individuelles | 3 |
| Saint-Georges | Blocs Kadence (WordPress) | 2 |
| Rimouski | HTML statique (`h4` sections) | 2 |
| Gaspé | Données curées (onglets JS) | 1 |

---

## Déploiement GitHub Pages

### Premier setup (une seule fois)

1. Fork ou clone ce repo sur ton compte GitHub
2. **Settings → Pages → Source → GitHub Actions**
3. Push sur `main` → le workflow tourne automatiquement

### Ce que fait `.github/workflows/deploy.yml`

```
push main / cron lundi 7h
    │
    ▼
Checkout repo (avec geocache.json)
    │
    ▼
pip install -r requirements.txt
playwright install chromium
    │
    ▼
python3 scraper.py
(geocache.json mis à jour en mémoire)
    │
    ▼
git commit geocache.json + terrains.db [skip ci]
git push
    │
    ▼
Copie index.html + terrains.db → _site/
    │
    ▼
actions/deploy-pages → GitHub Pages
```

---

## Développement local

```bash
# 1. Installer les dépendances
pip install -r requirements.txt
playwright install chromium

# 2. Générer / rafraîchir la base de données
python3 scraper.py

# 3. Lancer un serveur local (requis pour fetch() de terrains.db)
python3 -m http.server 8080
# → Ouvrir http://localhost:8080
```

> `index.html` charge `terrains.db` via `fetch()` — il faut un serveur HTTP,
> l'ouverture directe depuis le système de fichiers (`file://`) ne fonctionnera pas.

---

## Stack technique

| Composant | Technologie |
|-----------|-------------|
| Scraping | Python 3 · requests · BeautifulSoup · Playwright |
| Base de données | SQLite (généré côté serveur, lu côté client) |
| Frontend | HTML/CSS/JS vanilla — zéro framework |
| Carte | [Leaflet.js](https://leafletjs.com) + OpenStreetMap |
| SQL dans le navigateur | [sql.js](https://sql.js.org) (SQLite compilé en WASM) |
| Géocodage | [Nominatim](https://nominatim.org) (OSM) avec cache fichier |
| CI/CD | GitHub Actions → GitHub Pages |

---

## Sources de données

- Pages municipales officielles de chaque ville (HTML statique ou API)
- API ArcGIS REST de la Ville d'Ottawa (données live)
- [OpenStreetMap Overpass API](https://overpass-api.de) pour Drummondville (ODbL)
- [Nominatim](https://nominatim.openstreetmap.org) pour le géocodage

---

## Licence

Code source : MIT  
Données terrains : issues de sources publiques municipales et d'OpenStreetMap (ODbL).

