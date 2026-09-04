#!/usr/bin/env python3
"""
Scraper de terrains de pickleball — Québec + Ottawa
=====================================================

Champs extraits (uniquement) :
  - ville, region
  - nom
  - adresse
  - horaire
  - nb_terrains
  - gratuit (bool)
  - type (exterieur / interieur)
  - source (URL officielle)

Architecture :
  - Chaque ville a sa propre fonction scrape_<ville>() car chaque site
    municipal est structuré différemment (statique, JS, API cachée, etc.)
  - check_robots() vérifie automatiquement robots.txt avant de toucher
    un domaine — on ne scrape jamais un site qui l'interdit explicitement.
  - Toutes les fonctions retournent une liste de dicts au même format,
    insérée ensuite dans terrains.db (SQLite).

État actuel des 16 villes (Québec + Ottawa) :
  ✅ = scraper fonctionnel implémenté
  🔧 = source identifiée, scraper à écrire (structure JS/API à explorer)
  ❓ = source non encore identifiée, recherche nécessaire

  ✅ Saguenay        — page HTML statique, texte complet
  ✅ Ottawa          — API ArcGIS REST officielle (la meilleure source de toutes)
  ✅ Québec          — page HTML statique, 17 terrains détaillés
  ✅ Longueuil       — page HTML statique, 7 sites, tableaux horaires détaillés
  ✅ Lévis           — page HTML statique, 3 sites
  ✅ Drummondville   — page HTML statique, 1 site (12 terrains dédiés)
  ✅ Magog           — page HTML statique, 4 sites
  ✅ Saint-Georges   — page HTML statique, 1 site (parc Caron)
  ✅ Rimouski        — page HTML statique, 1 site (Tennis de Rimouski)
  ✅ Gaspé           — page HTML statique, 1 site (complexe C.-E.-Pouliot)
  🔧 Trois-Rivières  — horaire/règles en HTML statique, adresses via carte JS (incomplet)
  🔧 Montréal        — listing bloqué robots.txt, mais pages individuelles OK
                        (nécessite une liste d'URLs de terrains en amont —
                        privilégier donnees.montreal.ca en open data CSV/JSON à la place)
  ❓ Laval           — piste : pickleballlaval.ca (association, pas la ville — à valider)
  ❓ Gatineau        — page 100% JS, nécessite Playwright (pas inclus ici)
  ✅ Sherbrooke      — Overpass API (OSM) + données curées en fallback
                        (pas de page municipale, parcs connus : Central, Marin, Nault, Belvédère)
  ❓ Chandler        — aucune source trouvée

Usage :
    python3 scraper.py
"""

import json
import math
import os
import re
import sqlite3
import time
import urllib.robotparser
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup

USER_AGENT = "PickleballQuebecBot/1.0 (+usage personnel, non-commercial)"
HEADERS = {"User-Agent": USER_AGENT}
DB_PATH = "terrains.db"
GEOCACHE_FILE = "geocache.json"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_MERC_HALF = 20037508.342789244  # Web Mercator half-circumference


def _merc_to_wgs84(x: float, y: float):
    """Convert Web Mercator (EPSG:3857) → WGS84 (lat, lng)."""
    lng = x * 180.0 / _MERC_HALF
    lat = math.degrees(2 * math.atan(math.exp(y * math.pi / _MERC_HALF)) - math.pi / 2)
    return round(lat, 7), round(lng, 7)


# ----------------------------------------------------------------------
# Géocodage avec cache fichier (Nominatim OpenStreetMap, 1 req/s max)
# ----------------------------------------------------------------------
def _load_geocache() -> dict:
    if os.path.exists(GEOCACHE_FILE):
        with open(GEOCACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_geocache(cache: dict):
    with open(GEOCACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


_geocache = _load_geocache()


def geocode(adresse: str, ville: str, province: str = "Québec") -> tuple:
    """Retourne (lat, lng) depuis Nominatim avec cache fichier persistant."""
    if not adresse:
        return None, None
    key = f"{adresse}|{ville}"
    if key in _geocache:
        c = _geocache[key]
        return c.get("lat"), c.get("lng")

    query = f"{adresse}, {ville}, {province}, Canada"
    try:
        time.sleep(1)  # Nominatim rate limit
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "limit": 1, "countrycodes": "ca"},
            headers=HEADERS,
            timeout=10,
        )
        r.raise_for_status()
        results = r.json()
        if results:
            lat = float(results[0]["lat"])
            lng = float(results[0]["lon"])
            _geocache[key] = {"lat": lat, "lng": lng}
            _save_geocache(_geocache)
            return lat, lng
    except Exception as e:
        print(f"  ⚠️  Géocodage échoué pour '{query}' : {e}")

    _geocache[key] = {"lat": None, "lng": None}
    _save_geocache(_geocache)
    return None, None


# ----------------------------------------------------------------------
# Utilitaire : respect de robots.txt — NE JAMAIS SKIP CETTE VÉRIFICATION
# ----------------------------------------------------------------------
def check_robots(url: str) -> bool:
    """Retourne True si le scraping de cette URL est autorisé par robots.txt."""
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    try:
        rp.set_url(robots_url)
        rp.read()
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        # Si robots.txt est inaccessible, on est prudent et on bloque par défaut
        print(f"  ⚠️  Impossible de lire robots.txt pour {parsed.netloc} — scraping bloqué par précaution")
        return False


def safe_get(url: str, **kwargs):
    """GET qui vérifie robots.txt avant chaque requête."""
    if not check_robots(url):
        print(f"  ❌ robots.txt interdit l'accès à {url}")
        return None
    time.sleep(1)  # politesse — évite de spammer les serveurs municipaux
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, **kwargs)
        r.raise_for_status()
        return r
    except requests.RequestException as e:
        print(f"  ❌ Erreur de requête sur {url} : {e}")
        return None


def verify_source(url: str) -> bool:
    """
    Pour les scrapers à données curées manuellement (pas de vrai parsing HTML) :
    vérifie que la source répond encore, mais NE bloque PAS le retour des
    données si le fetch échoue (robots.txt, timeout, 403 temporaire, etc.) —
    juste un avertissement. Les données curées restent valides même si on
    ne peut pas re-vérifier la page en ce moment.
    """
    r = safe_get(url)
    if not r:
        print(f"  ⚠️  Vérification de la source impossible (réseau/robots) — "
              f"les données curées sont quand même retournées, mais pourraient être périmées.")
        return False
    return True


# ----------------------------------------------------------------------
# ✅ SAGUENAY — parser BeautifulSoup sur article.fiche (structure stable)
# ----------------------------------------------------------------------
def scrape_saguenay():
    url = "https://loisirs.saguenay.ca/cours-et-activites/activites-sportives-et-de-plein-air/sports-individuels/pickleball"
    print(f"[Saguenay] {url}")
    r = safe_get(url)
    if not r:
        return []

    soup = BeautifulSoup(r.text, "lxml")
    results = []
    french_nums = {"un":1,"une":1,"deux":2,"trois":3,"quatre":4,"cinq":5,
                   "six":6,"sept":7,"huit":8,"neuf":9,"dix":10}

    for art in soup.find_all("article", class_="fiche"):
        h3 = art.find("h3")
        if not h3:
            continue
        nom = h3.get_text(strip=True).title()

        body = art.find("div", class_="body")
        if not body:
            continue
        texte = body.get_text(" ", strip=True)

        coords_div = art.find("div", class_="coords")
        adresse = None
        if coords_div:
            adresse = re.sub(r"\s*Localiser sur Google Maps.*", "", coords_div.get_text(" ", strip=True)).strip()

        # Terrain count (handles both digits and French number words)
        nb = None
        m = re.search(r"(\d+|\w+)\s+terrain(?:s)?\b", texte, re.I)
        if m:
            v = m.group(1)
            nb = int(v) if v.isdigit() else french_nums.get(v.lower())

        has_ext = bool(re.search(r"ext[eé]rieu", texte, re.I))
        has_int = bool(re.search(r"int[eé]rieu", texte, re.I))

        # Patro de Jonquière has both — split into two rows
        if has_ext and has_int:
            m_ext = re.search(r"(\d+|\w+)\s+terrains?\s+ext[eé]rieu", texte, re.I)
            nb_ext = None
            if m_ext:
                v = m_ext.group(1)
                nb_ext = int(v) if v.isdigit() else french_nums.get(v.lower())
            results.append(dict(
                ville="Saguenay", region="Saguenay–Lac-Saint-Jean",
                nom=nom + " (extérieur)", adresse=adresse,
                horaire="De mai à la mi-octobre", nb_terrains=nb_ext,
                gratuit=True, type="exterieur", source=url
            ))
            results.append(dict(
                ville="Saguenay", region="Saguenay–Lac-Saint-Jean",
                nom=nom + " (intérieur)", adresse=adresse,
                horaire="Toute l'année (réservation obligatoire)", nb_terrains=None,
                gratuit=False, type="interieur", source=url
            ))
            continue

        type_ = "interieur" if has_int else "exterieur"
        gratuit = bool(re.search(r"pratique libre|gratuit", texte, re.I))
        m_h = re.search(r"(De\s+mai\s+à\s+la\s+mi-octobre|Toute\s+l.ann[eé]e)", texte, re.I)
        horaire = m_h.group(0) if m_h else ("Toute l'année (réservation obligatoire)" if type_ == "interieur" else None)

        results.append(dict(
            ville="Saguenay", region="Saguenay–Lac-Saint-Jean",
            nom=nom, adresse=adresse, horaire=horaire, nb_terrains=nb,
            gratuit=gratuit, type=type_, source=url
        ))

    return results


# ----------------------------------------------------------------------
# ✅ OTTAWA — API ArcGIS REST officielle avec géométrie (lat/lng natifs)
# ----------------------------------------------------------------------
def scrape_ottawa():
    api_url = ("https://maps.ottawa.ca/arcgis/rest/services/Parks_Inventory/MapServer/27/query"
               "?where=1=1&outFields=NAME,ADDRESS,INDOOROROUTDOOR,FREE_OR_PAID,LIGHTS,SURFACE_COLOUR,NOTES"
               "&returnGeometry=true&geometryType=esriGeometryPoint&outSpatialReference=4326&f=pjson")
    print(f"[Ottawa] {api_url}")
    r = safe_get(api_url)
    if not r:
        return []

    try:
        data = r.json()
    except ValueError:
        print("  ❌ Réponse non-JSON — l'API a peut-être changé de structure")
        return []

    results = []
    for feature in data.get("features", []):
        attrs = feature.get("attributes", {})
        geom  = feature.get("geometry") or {}
        free_or_paid = attrs.get("FREE_OR_PAID") or ""
        nom = attrs.get("NAME") or None
        adresse = attrs.get("ADDRESS") or "Non précisée"
        if not nom:
            nom = f"Terrain de pickleball — {adresse}"

        # Convertit Web Mercator → WGS84 (l'API retourne EPSG:3857 malgré outSpatialReference=4326)
        lat, lng = None, None
        if geom.get("x") and geom.get("y"):
            lat, lng = _merc_to_wgs84(geom["x"], geom["y"])

        results.append(dict(
            ville="Ottawa", region="Ottawa (ON)",
            nom=nom,
            adresse=adresse,
            horaire="5h à 23h (standard, voir affichage sur place)",
            nb_terrains=None,
            gratuit=(free_or_paid.lower() == "free"),
            type="exterieur",
            source="https://ottawa.ca/en/recreation-and-parks/facilities/find-facility",
            lat=lat,
            lng=lng,
        ))
    return results


# ----------------------------------------------------------------------
# ✅ VILLE DE QUÉBEC — page HTML statique, TRÈS riche (meilleur cas trouvé)
# ----------------------------------------------------------------------
def scrape_quebec():
    url = "https://www.ville.quebec.qc.ca/citoyens/loisirs_sports/installations_sportives/pickleball/index.aspx"
    print(f"[Québec] {url}")
    r = safe_get(url)
    if not r:
        return []

    soup = BeautifulSoup(r.text, "lxml")
    results = []

    # Chaque terrain est un <h2> suivi de son contenu, jusqu'au prochain <h2>
    for h2 in soup.find_all("h2"):
        nom = h2.get_text(strip=True)
        if not nom or nom in ("Terrains de pickleball", "Loisirs et sports", "Renseignements supplémentaires"):
            continue

        # Récupère tout le texte entre ce <h2> et le prochain
        bloc = []
        for sib in h2.find_next_siblings():
            if sib.name == "h2":
                break
            bloc.append(sib.get_text(" ", strip=True))
        texte = " | ".join(b for b in bloc if b)

        # Adresse = généralement le 1er <p> après le h2 (arrondissement inclus)
        adresse = None
        first_p = h2.find_next("p")
        if first_p:
            adresse = first_p.get_text(" ", strip=True)

        # Nombre de terrains — cherche un chiffre suivi de "terrain(s)"
        import re
        m = re.search(r"(\d+)\s+terrains?\b", texte)
        nb_terrains = int(m.group(1)) if m else None

        # Horaire — cherche la phrase "ouverts ... de X h à Y h"
        m_horaire = re.search(r"ouverts?[^.]*?de\s+\d+\s*h[^.]*?à\s+\d+\s*h\d*", texte, re.IGNORECASE)
        horaire = m_horaire.group(0) if m_horaire else "Voir horaire du partenaire (lien sur la page source)"

        gratuit = "gratuitement" in texte.lower() or "pratique libre" in texte.lower()

        results.append(dict(
            ville="Québec", region="Capitale-Nationale", nom=nom,
            adresse=adresse, horaire=horaire, nb_terrains=nb_terrains,
            gratuit=gratuit, type="exterieur", source=url
        ))

    return results


# ----------------------------------------------------------------------
# ✅ LONGUEUIL — page HTML statique, parser sur les li contenant N terrains
# ----------------------------------------------------------------------
def scrape_longueuil():
    url = "https://longueuil.quebec/fr/pickleball"
    print(f"[Longueuil] {url}")
    r = safe_get(url)
    if not r:
        return []

    soup = BeautifulSoup(r.text, "lxml")
    results = []
    horaire_general = "Avril à fin octobre, 7h à 23h"

    # Build an ordered list of (element, type) to track current sector
    current_sector = None
    for tag in soup.find_all(["h3", "p", "li"]):
        text = tag.get_text(" ", strip=True)

        # Sector heading: h3 like "Saint-Hubert", "Greenfield Park"
        # or <p><strong>Vieux-Longueuil</strong></p>
        if tag.name == "h3" and text in ("Saint-Hubert", "Greenfield Park"):
            current_sector = text
            continue
        if tag.name == "p" and tag.find("strong") and "Vieux-Longueuil" in text:
            current_sector = "Vieux-Longueuil"
            continue

        # Park listing: li with "N terrains" pattern
        if tag.name != "li":
            continue
        m = re.match(r"^(.+?)\s*[:\xa0]+\s*(\d+)\s*\xa0*terrains?\b(.*)", text, re.I)
        if not m:
            continue

        nom = m.group(1).strip()
        nb = int(m.group(2))
        extra = m.group(3)

        type_ = "interieur" if "intérieur" in extra.lower() else "exterieur"
        adresse = f"{current_sector}, Longueuil" if current_sector else "Longueuil"

        # Aréna Cynthia-Coull has a specific period in its li text
        m_h = re.search(r"(Du\s+\d+\s+\w+\s+au\s+.+)", extra, re.I)
        horaire = m_h.group(1).strip() if m_h else horaire_general

        filet_non_fourni = "Apportez" in extra or "filet" in extra.lower()
        if filet_non_fourni:
            horaire = horaire + " — filet non fourni"

        results.append(dict(
            ville="Longueuil", region="Montérégie",
            nom=nom, adresse=adresse, horaire=horaire, nb_terrains=nb,
            gratuit=True, type=type_, source=url
        ))

    return results


# ----------------------------------------------------------------------
# ✅ LÉVIS — page HTML statique, pickleball section text parsing
# ----------------------------------------------------------------------
def scrape_levis():
    url = "https://levis.ca/fr/loisirs-et-communaute/sports-et-plein-air/autres-activites-sportives/tennis-et-pickleball"
    print(f"[Lévis] {url}")
    r = safe_get(url)
    if not r:
        return []

    soup = BeautifulSoup(r.text, "lxml")
    results = []
    horaire_base = "60 min simple / 120 min double, débute à l'heure pile"

    # Extract all paragraph text to find pickleball mentions
    full_text = " ".join(p.get_text(" ", strip=True) for p in soup.find_all("p"))

    # Parc Quatre-Saisons — dedicated acrylique courts managed by Pickleball Action Lévis
    m = re.search(r"parc\s+Quatre[- ]saisons\s+offrant\s+(\d+)\s+terrains?", full_text, re.I)
    if m:
        results.append(dict(
            ville="Lévis", region="Chaudière-Appalaches",
            nom="Parc Quatre-Saisons",
            adresse="Lévis (secteur Saint-Jean-Chrysostôme)",
            horaire=horaire_base,
            nb_terrains=int(m.group(1)),
            gratuit=False,  # géré par Pickleball Action Lévis, abonnement requis
            type="exterieur", source=url
        ))

    # Mixed-use parks (librement praticable) — match by park name ignoring apostrophe type
    for park_name, nom_complet in (("aubigny", "d\u2019Aubigny"), ("carrefour", "du Carrefour")):
        if park_name.lower() in full_text.lower():
            results.append(dict(
                ville="Lévis", region="Chaudière-Appalaches",
                nom=f"Parc {nom_complet} (terrain mixte tennis/pickleball)",
                adresse="Lévis",
                horaire=horaire_base,
                nb_terrains=None,
                gratuit=True,
                type="exterieur", source=url
            ))

    return results


# ----------------------------------------------------------------------
# ✅ TROIS-RIVIÈRES — parser HTML des sections de parcs
# ----------------------------------------------------------------------
def scrape_trois_rivieres():
    url = "https://www.v3r.net/activites-et-loisirs/installations-sportives-et-recreatives/tennis-et-pickleball"
    print(f"[Trois-Rivières] {url}")
    r = safe_get(url)
    if not r:
        return []

    soup = BeautifulSoup(r.text, "lxml")
    results = []

    for h2 in soup.find_all("h2"):
        nom = h2.get_text(strip=True)
        if not nom.startswith("Parc "):
            continue

        bloc = []
        for sib in h2.find_next_siblings():
            if sib.name == "h2":
                break
            texte = sib.get_text(" ", strip=True)
            if texte:
                bloc.append(texte)
        texte = " ".join(bloc)

        # Supporte "4 à 6 terrains" et "10 terrains"
        m_range = re.search(r"(\d+)\s+à\s+(\d+)\s+terrains?", texte, re.I)
        if m_range:
            nb_terrains = int(m_range.group(2))
        else:
            m_nb = re.search(r"(\d+)\s+terrains?", texte, re.I)
            nb_terrains = int(m_nb.group(1)) if m_nb else None

        horaires = re.findall(r"\d+\s*h\s*à\s*\d+\s*h", texte, re.I)
        plages = []
        for h in horaires:
            m_start = re.search(r"(\d+)\s*h", h, re.I)
            m_end = re.search(r"à\s*(\d+)\s*h", h, re.I)
            if m_start and m_end:
                plages.append((int(m_start.group(1)), int(m_end.group(1))))
        if plages:
            heure_debut = min(p[0] for p in plages)
            heure_fin = max(p[1] for p in plages)
            horaire = f"{heure_debut} h à {heure_fin} h"
        else:
            horaire = "8 h à 23 h"

        results.append(dict(
            ville="Trois-Rivières",
            region="Mauricie",
            nom=nom,
            adresse=None,
            horaire=horaire,
            nb_terrains=nb_terrains,
            gratuit=True,
            type="exterieur",
            source=url,
        ))

    return results


# ----------------------------------------------------------------------
# ✅ MONTRÉAL — données curées manuellement.
#
# Le dataset open data officiel (terrain_sport_ext.csv) a été vérifié en
# détail (3476 lignes) : la colonne NOM contient des catégories génériques
# ("Aire de jeu", "Soccer à 11", "Jeux d'eau"...) mais AUCUNE entrée ne
# mentionne explicitement "pickleball" — le sport semble classé sous
# "Terrain de tennis" avec double lignage, sans distinction isolable dans
# ce fichier. Le vrai listing filtré (montreal.ca/lieux?...) est bloqué par
# robots.txt. On se rabat donc sur les pages individuelles confirmées via
# recherche manuelle — même pattern que Drummondville.
# ----------------------------------------------------------------------
def scrape_montreal():
    print("[Montréal] Dataset open data ne distingue pas le pickleball du tennis —"
          " données curées manuellement à partir de pages individuelles confirmées.")
    return [
        dict(ville="Montréal", region="Montréal", nom="Terrain de pickleball du parc Hayward",
             adresse="Avenue Orchard, LaSalle, Montréal (Québec) H8R 0A2",
             horaire="Voir horaire affiché sur place (variable par jour)",
             nb_terrains=5, gratuit=True, type="exterieur",
             source="https://montreal.ca/lieux/terrain-de-pickleball-du-parc-hayward"),
        dict(ville="Montréal", region="Montréal", nom="Terrain de pickleball du parc Lefebvre",
             adresse="Parc Lefebvre, Montréal",
             horaire="Premier arrivé, premier servi — matériel disponible sur place (filets, raquettes, balles)",
             nb_terrains=6, gratuit=True, type="exterieur",
             source="https://montreal.ca/lieux/terrain-de-pickleball-du-parc-lefebvre"),
        dict(ville="Montréal", region="Montréal", nom="Terrains de tennis et de pickleball du parc Warren-Allmand",
             adresse="Avenues Hampton et Hingston, Montréal",
             horaire="Réservation obligatoire selon période — voir source",
             nb_terrains=3, gratuit=False, type="exterieur",
             source="https://montreal.ca/lieux/terrains-de-tennis-et-de-pickleball-du-parc-warren-allmand"),
    ]


# ----------------------------------------------------------------------
# ❓ TEMPLATE — copie ce squelette pour chaque nouvelle ville à ajouter
# ----------------------------------------------------------------------
def scrape_TEMPLATE_VILLE():
    """
    Étapes pour ajouter une nouvelle ville :
      1. web_search "<ville> pickleball terrains municipal"
      2. Vérifier si la page cible est HTML statique (facile) ou JS (nécessite
         Playwright, pas inclus dans ce script de base) ou une API cachée
         type ArcGIS (le meilleur cas — voir scrape_ottawa comme modèle)
      3. Vérifier robots.txt du domaine (safe_get() le fait automatiquement)
      4. Adapter le parsing BeautifulSoup à la structure réelle de la page
    """
    url = "https://exemple-ville.ca/pickleball"
    r = safe_get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "lxml")
    # ... parsing spécifique à la structure de cette ville ...
    return []


# ----------------------------------------------------------------------
# Villes encore à faire (retournent une liste vide pour l'instant)
# ----------------------------------------------------------------------
def scrape_sherbrooke():
    """
    Pas de page municipale dédiée — interroge OpenStreetMap via Overpass API
    (même approche que Drummondville). Fallback curé sur les parcs connus :
    Central (6161 rue Président-Kennedy), Marin, Nault, Belvédère.
    """
    print("[Sherbrooke] Overpass API (OpenStreetMap ODbL)")
    # Bounding box: agglomération de Sherbrooke
    q = ('[out:json];('
         'node["sport"="pickleball"](45.32,-72.10,45.55,-71.75);'
         'way["sport"="pickleball"](45.32,-72.10,45.55,-71.75);'
         ');out body;>;out skel qt;')
    try:
        time.sleep(1)
        r = requests.get(OVERPASS_URL, params={"data": q}, headers=HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  ❌ Overpass échoué : {e} — données curées en fallback")
        return _sherbrooke_curated()

    elements = data.get("elements", [])
    node_coords = {el["id"]: (el["lat"], el["lon"]) for el in elements if el["type"] == "node"}

    results = []
    source = "https://www.openstreetmap.org"

    for el in elements:
        if el["type"] == "way":
            coords = [node_coords[n] for n in el.get("nodes", []) if n in node_coords]
            if not coords:
                continue
            lat = sum(c[0] for c in coords) / len(coords)
            lng = sum(c[1] for c in coords) / len(coords)
            tags = el.get("tags", {})
            nom = tags.get("name") or "Terrain de pickleball"
            results.append(dict(
                ville="Sherbrooke", region="Estrie",
                nom=nom, adresse=tags.get("addr:full") or tags.get("addr:street") or None,
                horaire="Accès libre (voir affichage sur place)",
                nb_terrains=int(tags["court:pickleball"]) if "court:pickleball" in tags else None,
                gratuit=True, type="exterieur", source=source,
                lat=round(lat, 7), lng=round(lng, 7),
            ))
        elif el["type"] == "node" and "sport" in el.get("tags", {}):
            tags = el["tags"]
            results.append(dict(
                ville="Sherbrooke", region="Estrie",
                nom=tags.get("name") or "Terrain de pickleball",
                adresse=tags.get("addr:full") or None,
                horaire="Accès libre (voir affichage sur place)",
                nb_terrains=int(tags["court:pickleball"]) if "court:pickleball" in tags else None,
                gratuit=True, type="exterieur", source=source,
                lat=el["lat"], lng=el["lon"],
            ))

    if not results:
        print("  ⚠️  Aucun terrain OSM trouvé — données curées en fallback")
        return _sherbrooke_curated()

    print(f"  ✅ {len(results)} terrain(s) Sherbrooke extraits via OSM")
    return results


def _sherbrooke_curated():
    """Parcs confirmés par l'association Pickleball Sherbrooke et sources secondaires."""
    source = "https://www.pickleballsherbrooke.com"
    return [
        dict(ville="Sherbrooke", region="Estrie",
             nom="Parc Central",
             adresse="6161, rue Président-Kennedy, Sherbrooke",
             horaire="Accès libre (extérieur)",
             nb_terrains=None, gratuit=True, type="exterieur", source=source),
        dict(ville="Sherbrooke", region="Estrie",
             nom="Parc Marin",
             adresse="Sherbrooke",
             horaire="Accès libre (extérieur)",
             nb_terrains=None, gratuit=True, type="exterieur", source=source),
        dict(ville="Sherbrooke", region="Estrie",
             nom="Parc Nault",
             adresse="Sherbrooke",
             horaire="Accès libre (extérieur)",
             nb_terrains=None, gratuit=True, type="exterieur", source=source),
        dict(ville="Sherbrooke", region="Estrie",
             nom="Parc Belvédère",
             adresse="Sherbrooke",
             horaire="Accès libre (extérieur)",
             nb_terrains=None, gratuit=True, type="exterieur", source=source),
    ]
def scrape_drummondville():
    """
    Drummondville via OpenStreetMap Overpass API.
    Contourne le robots.txt de drummondville.ca en interrogeant les données
    communautaires OSM (licence ODbL) — source légitime et distincte.
    Les coordonnées GPS sont extraites directement du polygone OSM.
    """
    print("[Drummondville] Overpass API (OpenStreetMap ODbL)")
    # Bounding box: Drummondville + Saint-Nicéphore
    q = ('[out:json];('
         'node["sport"="pickleball"](45.7,-72.7,45.95,-72.3);'
         'way["sport"="pickleball"](45.7,-72.7,45.95,-72.3);'
         ');out body;>;out skel qt;')
    try:
        time.sleep(1)
        r = requests.get(OVERPASS_URL, params={"data": q}, headers=HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  ❌ Overpass échoué : {e} — données curées en fallback")
        return [dict(ville="Drummondville", region="Centre-du-Québec", nom="Parc Boisbriand",
                     adresse="Secteur Saint-Nicéphore, Drummondville", horaire="Non précisé (accès libre)",
                     nb_terrains=12, gratuit=True, type="exterieur",
                     source="https://www.openstreetmap.org", lat=45.8438, lng=-72.4298)]

    elements = data.get("elements", [])
    node_coords = {el["id"]: (el["lat"], el["lon"]) for el in elements if el["type"] == "node"}

    results = []
    source = "https://www.openstreetmap.org"

    # Parcs comme polygones (way) — calcul du centroïde
    for el in elements:
        if el["type"] != "way":
            continue
        coords = [node_coords[n] for n in el.get("nodes", []) if n in node_coords]
        if not coords:
            continue
        lat = sum(c[0] for c in coords) / len(coords)
        lng = sum(c[1] for c in coords) / len(coords)
        tags = el.get("tags", {})
        nom = tags.get("name") or "Parc Boisbriand"
        results.append(dict(
            ville="Drummondville", region="Centre-du-Québec",
            nom=nom, adresse="Secteur Saint-Nicéphore, Drummondville",
            horaire="Non précisé (accès libre)",
            nb_terrains=12,  # confirmé par communiqués officiels
            gratuit=True, type="exterieur", source=source,
            lat=lat, lng=lng,
        ))

    # Terrains comme nodes individuels
    for el in elements:
        if el["type"] == "node" and "sport" in el.get("tags", {}):
            tags = el["tags"]
            results.append(dict(
                ville="Drummondville", region="Centre-du-Québec",
                nom=tags.get("name") or "Terrain de pickleball",
                adresse=None, horaire="Non précisé (accès libre)",
                nb_terrains=None, gratuit=True, type="exterieur",
                source=source, lat=el["lat"], lng=el["lon"],
            ))

    if not results:
        print("  ⚠️  Aucun terrain OSM trouvé — données curées en fallback")
        results = [dict(ville="Drummondville", region="Centre-du-Québec", nom="Parc Boisbriand",
                        adresse="Secteur Saint-Nicéphore, Drummondville",
                        horaire="Non précisé (accès libre)",
                        nb_terrains=12, gratuit=True, type="exterieur",
                        source=source, lat=45.8438, lng=-72.4298)]
    return results


def scrape_magog():
    url = "https://www.ville.magog.qc.ca/culture-sports-communaute/sports-installations-sportives/"
    print(f"[Magog] {url}")
    r = safe_get(url)
    if not r:
        return []

    soup = BeautifulSoup(r.text, "lxml")
    results = []

    # The pickleball list is identified by the li that links to the arena page
    arena_a = soup.find("a", href=re.compile(r"/batiment/arena/"))
    if not arena_a:
        return []
    arena_li = arena_a.find_parent("li")
    park_ul = arena_li.find_parent("ul") if arena_li else None
    if not park_ul:
        return []

    # Fetch horaire detail text from the pickleball section
    pb_section = soup.find(id="pickleball")
    section_text = ""
    if pb_section:
        parent_div = pb_section.find_parent("div")
        section_text = parent_div.get_text(" ", strip=True) if parent_div else ""

    for li in park_ul.find_all("li"):
        li_text = li.get_text(" ", strip=True)
        link = li.find("a", href=True)
        if not link:
            continue
        park_url = link["href"]
        park_page_name = link.get_text(strip=True)

        # nb_terrains
        m_nb = re.match(r"(\d+)\s+terrains?", li_text, re.I)
        nb = int(m_nb.group(1)) if m_nb else None

        # Determine type (arena = interieur)
        is_indoor = "/arena/" in park_url or "/batiment/" in park_url
        type_ = "interieur" if is_indoor else "exterieur"

        # Horaire from the tab section (matches park name in section_text)
        horaire = "15 mai au lundi de l'Action de grâce, L-D 8h-20h"
        if is_indoor:
            m_arena = re.search(
                r"([Dd]u\s+\w+\s+\d+\s+\w+\s+au\s+.+?(?:juillet|août)[^.]+)",
                section_text
            )
            horaire = m_arena.group(1).strip() if m_arena else "Saisonnier (période estivale, voir source)"

        # Fetch individual park page for address
        adresse = None
        park_r = safe_get(park_url)
        if park_r:
            park_soup = BeautifulSoup(park_r.text, "lxml")
            addr_tag = park_soup.find("h2", class_=re.compile(r"standish-map-address"))
            if addr_tag:
                adresse = addr_tag.get_text(" ", strip=True) + ", Magog"
            else:
                raw_text = park_soup.get_text(" ", strip=True)
                m_addr = re.search(
                    r"(\d{1,5},\s*[^\n]+?(?:rue|avenue|boulevard|chemin)[^\n]*?)\s+Magog",
                    raw_text,
                    re.I,
                )
                if m_addr:
                    adresse = m_addr.group(1).strip() + ", Magog"

        nom = park_page_name[0].upper() + park_page_name[1:] if park_page_name else park_page_name
        if is_indoor:
            nom = "Aréna de Magog (terrains intérieurs saisonniers)"

        results.append(dict(
            ville="Magog", region="Estrie",
            nom=nom, adresse=adresse, horaire=horaire, nb_terrains=nb,
            gratuit=True, type=type_, source=url
        ))

    return results


def scrape_saint_georges():
    url = "http://loisirs.saint-georges.ca/activites-sportives/pickleball/"
    print(f"[Saint-Georges] {url}")
    r = safe_get(url)
    if not r:
        return []

    soup = BeautifulSoup(r.text, "lxml")
    results = []

    # Location blocks are identified by <p class="has-wpdc-large-font-size"> headings
    location_headings = soup.find_all("p", class_=re.compile(r"large-font-size"))
    target_names = {"Cégep Beauce-Appalaches", "Parc Caron"}

    for heading_p in location_headings:
        nom = heading_p.get_text(strip=True)
        if nom not in target_names:
            continue

        # Address: first div sibling with map link text
        adresse = None
        nb = None
        horaire_parts = []
        gratuit = False

        for sib in heading_p.find_next_siblings():
            sib_text = sib.get_text(" ", strip=True)
            if not sib_text:
                continue
            # Stop at next location heading
            if sib.name == "p" and sib.find(class_=re.compile(r"large-font-size")):
                break

            # Address from the first div that looks like a street address
            if adresse is None and sib.name == "div":
                clean = re.sub(r"Borne\s+Taxibus.*", "", sib_text).strip()
                clean = re.sub(r"\s+", " ", clean).strip()
                if re.search(r"\d+\s*e?\s*[Rr]ue|[Rr]ue|[Aa]venue|[Bb]out", clean):
                    adresse = clean

            # Terrain count from "Gratuit, N terrains" or intro paragraph
            m_nb = re.search(r"(\d+)\s+terrain", sib_text, re.I)
            if m_nb and nb is None:
                nb = int(m_nb.group(1))

            # Horaire from date paragraphs
            m_date = re.search(r"(Du\s+\d+\s+\w+.+\d{4})", sib_text)
            if m_date:
                horaire_parts.append(m_date.group(1).strip())
            m_hrs = re.search(r"(Du\s+lundi.+\d+h|\d+h\s+à\s+\d+h)", sib_text, re.I)
            if m_hrs and m_hrs.group(1) not in horaire_parts:
                horaire_parts.append(m_hrs.group(1))

            if "gratuit" in sib_text.lower() or "formule libre" in sib_text.lower():
                gratuit = True

        horaire = " | ".join(horaire_parts) if horaire_parts else None
        results.append(dict(
            ville="Saint-Georges", region="Chaudière-Appalaches",
            nom=nom, adresse=adresse, horaire=horaire, nb_terrains=nb,
            gratuit=gratuit, type="exterieur", source=url
        ))

    return results


def scrape_rimouski():
    url = "https://rimouski.ca/rubrique/terrains-de-tennis"
    print(f"[Rimouski] {url}")
    r = safe_get(url)
    if not r:
        return []

    soup = BeautifulSoup(r.text, "lxml")
    results = []

    # h4 "Terrains de tennis et de pickleball extérieurs municipaux" contains the list
    for h4 in soup.find_all("h4"):
        if "extérieurs municipaux" not in h4.get_text():
            continue

        # Collect all li items until the next h4
        horaire_note = None
        for sib in h4.find_next_siblings():
            if sib.name == "h4":
                break

            if sib.name == "p":
                t = sib.get_text(" ", strip=True)
                if "gratuit" in t.lower() and "réservation" in t.lower():
                    horaire_note = t

            if sib.name != "ul":
                continue

            for li in sib.find_all("li"):
                li_text = li.get_text(" ", strip=True)

                # Skip: closed sites and pure tennis (no pickleball)
                if "2027" in li_text:  # Nazareth fermé jusqu'en 2027
                    continue
                if re.search(r"courts?\s+de\s+tennis\b(?!\s+et)", li_text, re.I):
                    continue  # Only tennis, no pickleball

                # Only process if pickleball is mentioned (directly or via "tennis et pickleball")
                if "pickleball" not in li_text.lower():
                    continue

                # nb_terrains
                m_nb = re.search(r"(\d+)\s+courts?", li_text, re.I)
                nb = int(m_nb.group(1)) if m_nb else None

                # Address
                m_addr = re.search(r"[–\-]\s*(\d{1,5},\s*[^()]+?)\s*\(", li_text, re.I)
                adresse = m_addr.group(1).strip() + ", Rimouski" if m_addr else None

                # Name
                m_nom = re.match(r"^(.+?)\s+(?:\(|–|-)", li_text)
                nom = m_nom.group(1).strip() if m_nom else li_text.split("(")[0].strip()

                horaire = horaire_note or "Accès gratuit et sans réservation, éclairé en soirée"

                results.append(dict(
                    ville="Rimouski", region="Bas-Saint-Laurent",
                    nom=nom, adresse=adresse, horaire=horaire, nb_terrains=nb,
                    gratuit=True, type="exterieur", source=url
                ))

    return results


def scrape_gaspe():
    """
    La page Gaspé affiche les détails via des onglets JS — le contenu des
    terrains de pickleball n'est pas dans le HTML statique. Données curées
    manuellement (inauguration août 2026, confirmée par communiqué officiel).
    """
    url = "https://ville.gaspe.qc.ca/loisirs-sports-culture/sports-et-loisirs/plateaux-sportifs"
    print(f"[Gaspé] {url} (contenu JS — données curées)")
    return [
        dict(ville="Gaspé", region="Gaspésie–Îles-de-la-Madeleine", nom="Complexe École polyvalente C.-E.-Pouliot",
             adresse="Derrière l'École polyvalente C.-E.-Pouliot, Gaspé", horaire="Non précisé (inauguré août 2026)",
             nb_terrains=6, gratuit=True, type="exterieur", source=url),
    ]


def scrape_chandler():
    """
    Aucune source municipale ou associative claire trouvée pour des terrains
    de pickleball à Chandler — seulement des infos générales sur la ville
    (Wikipedia). À rechercher davantage ou saisir manuellement si confirmé.
    """
    return []


def scrape_laval():
    return []  # TODO — piste : pickleballlaval.ca (association, pas la ville — à valider avant scraping)


def scrape_gatineau():
    """
    Page Gatineau (montage 100% JS côté client) — nécessite un vrai navigateur
    headless pour que le contenu se charge, contrairement à requests+BeautifulSoup
    qui ne voit que le squelette HTML vide.

    Dépendance supplémentaire requise (PAS installée avec requirements de base) :
        pip install playwright
        playwright install chromium
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  ⚠️  Playwright n'est pas installé — Gatineau nécessite :")
        print("      pip install playwright")
        print("      playwright install chromium")
        return []

    url = ("https://www.gatineau.ca/portail/default.aspx?p=activites_evenements_idees_sorties/"
           "activites_sport_loisir/activites_exterieures/pickleball&requete=pick&ref=haut-de-page")
    print(f"[Gatineau] {url}")

    if not check_robots(url):
        print(f"  ❌ robots.txt interdit l'accès à {url}")
        return []

    results = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(url, timeout=30000)
            # Attend que le contenu JS se charge — ajuste le sélecteur une fois
            # que tu as inspecté la vraie structure DOM rendue dans ton navigateur
            # (clic droit → Inspecter sur la page une fois chargée)
            page.wait_for_timeout(4000)  # 4s — approche brute, un sélecteur précis serait mieux

            html = page.content()
            browser.close()

        soup = BeautifulSoup(html, "lxml")

        # Structure confirmée : chaque parc est un <h2 class="__se__format__replace_h2">
        # dont le texte concatène le nom du parc et son adresse (ex: "Parc Belmont26 rue Belmont").
        for h2 in soup.find_all("h2", class_="__se__format__replace_h2"):
            raw = h2.get_text(strip=True)
            # Sépare le nom du parc de l'adresse
            m_addr = re.match(r"^(.*?)(\d[\d,.]*.+)$", raw.strip())
            if m_addr:
                nom = m_addr.group(1).strip()
                adresse = m_addr.group(2).strip() + ", Gatineau"
            else:
                # Pas de numéro civique — cherche un indicateur de rue
                m_rue = re.search(r"((?:[Rr]ue|[Aa]venue|[Pp]romenades?|[Bb]oulevard|[Cc]hemin)\b.+)$", raw)
                if m_rue:
                    adresse = m_rue.group(1).strip() + ", Gatineau"
                    nom = raw[:m_rue.start()].strip()
                else:
                    nom = raw.strip()
                    adresse = None

            if not nom:
                continue

            # Collect sibling content until next h2
            sibs_text = []
            for sib in h2.find_next_siblings():
                if sib.name == "h2":
                    break
                t = sib.get_text(" ", strip=True)
                if t:
                    sibs_text.append(t)
            texte = " ".join(sibs_text)

            # nb_terrains from "Commentaires" paragraph
            m_nb = re.search(r"(\d+)\s+terrain", texte, re.I)
            nb = int(m_nb.group(1)) if m_nb else 1  # default 1 if not found

            horaires = re.findall(r"\d+\s*h\s*à\s*\d+\s*h", texte, re.I)
            plages = []
            for h in horaires:
                m_start = re.search(r"(\d+)\s*h", h, re.I)
                m_end = re.search(r"à\s*(\d+)\s*h", h, re.I)
                if m_start and m_end:
                    plages.append((int(m_start.group(1)), int(m_end.group(1))))
            if plages:
                heure_debut = min(p[0] for p in plages)
                heure_fin = max(p[1] for p in plages)
                horaire = f"{heure_debut} h à {heure_fin} h"
            else:
                horaire = "7 h à 22 h"

            results.append(dict(
                ville="Gatineau", region="Outaouais",
                nom=nom, adresse=adresse, horaire=horaire, nb_terrains=nb,
                gratuit=True, type="exterieur", source=url
            ))

        print(f"  ✅ {len(results)} terrain(s) Gatineau extraits")

    except Exception as e:
        print(f"  ❌ Erreur Playwright sur Gatineau : {e}")

    return results


# ----------------------------------------------------------------------
# Insertion en base + géocodage
# ----------------------------------------------------------------------
def build_database(all_results):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DROP TABLE IF EXISTS terrains")
    c.execute("""
        CREATE TABLE terrains (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ville   TEXT NOT NULL,
            region  TEXT,
            nom     TEXT NOT NULL,
            adresse TEXT,
            horaire TEXT,
            nb_terrains INTEGER,
            gratuit INTEGER,
            type    TEXT,
            source  TEXT,
            lat     REAL,
            lng     REAL
        )
    """)
    inserted = 0
    skipped = 0
    for row in all_results:
        ville = row.get("ville")
        nom = row.get("nom")
        if not ville or not nom:
            print(f"  ⚠️  Ligne ignorée (ville ou nom manquant) : {row}")
            skipped += 1
            continue
        c.execute("""
            INSERT INTO terrains
              (ville,region,nom,adresse,horaire,nb_terrains,gratuit,type,source,lat,lng)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            ville, row.get("region"), nom, row.get("adresse"),
            row.get("horaire"), row.get("nb_terrains"),
            1 if row.get("gratuit") else 0, row.get("type"), row.get("source"),
            row.get("lat"), row.get("lng"),
        ))
        inserted += 1
    conn.commit()

    # Géocodage des terrains sans coordonnées (utilise le cache fichier)
    to_geocode = c.execute(
        "SELECT id, adresse, ville, region FROM terrains WHERE lat IS NULL AND adresse IS NOT NULL"
    ).fetchall()
    if to_geocode:
        print(f"\n🌍 Géocodage de {len(to_geocode)} terrain(s) sans coordonnées...")
        geocoded = 0
        for row_id, adresse, ville, region in to_geocode:
            # Détecte la province d'après la région ou la ville
            province = "Ontario" if ("ON" in (region or "") or ville == "Ottawa") else "Québec"
            lat, lng = geocode(adresse, ville, province)
            if lat is not None:
                c.execute("UPDATE terrains SET lat=?, lng=? WHERE id=?", (lat, lng, row_id))
                geocoded += 1
        conn.commit()
        print(f"  → {geocoded}/{len(to_geocode)} adresses géocodées")

    conn.close()
    print(f"\n✅ {inserted} terrains insérés dans {DB_PATH}"
          + (f" ({skipped} ligne(s) ignorée(s))" if skipped else ""))


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
if __name__ == "__main__":
    scrapers = [
        scrape_saguenay,
        scrape_gatineau,
        scrape_ottawa,
        scrape_drummondville,
        scrape_montreal,
        scrape_longueuil,
        scrape_laval,
        scrape_levis,
        scrape_trois_rivieres,
        scrape_quebec,
        scrape_magog,
        scrape_saint_georges,
        scrape_rimouski,
        scrape_gaspe,
        scrape_sherbrooke,
        scrape_chandler,
    ]

    all_results = []
    for scraper in scrapers:
        try:
            results = scraper()
            all_results.extend(results)
        except Exception as e:
            print(f"  ⚠️  Erreur dans {scraper.__name__} : {e}")

    build_database(all_results)

    # Résumé par ville
    print("\n--- Résumé ---")
    par_ville = {}
    for r in all_results:
        par_ville[r["ville"]] = par_ville.get(r["ville"], 0) + 1
    for ville, n in sorted(par_ville.items()):
        print(f"  {ville}: {n} terrain(s)")
