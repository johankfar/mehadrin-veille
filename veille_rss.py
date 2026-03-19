#!/usr/bin/env python3
"""
veille_rss.py -- Scraper RSS multi-sources pour la veille Mehadrin
===================================================================
Ratisse 10+ flux RSS B2B fruits & legumes, filtre par mots-cles Mehadrin,
retourne les articles candidats avec titre, resume, lien, source, langue.
"""

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import urlopen, Request
from urllib.error import URLError
import ssl
import html

# ─── Configuration des flux RSS ───

RSS_FEEDS = [
    # Priorite haute — couverture directe produits Mehadrin
    {"url": "https://www.freshplaza.com/rss.xml",       "name": "FreshPlaza",      "lang": "en"},
    {"url": "https://www.freshplaza.fr/rss.xml",        "name": "FreshPlaza FR",   "lang": "fr"},
    {"url": "https://www.freshplaza.it/rss.xml",        "name": "FreshPlaza IT",   "lang": "it"},
    {"url": "https://www.freshplaza.es/rss.xml",        "name": "FreshPlaza ES",   "lang": "es"},
    {"url": "https://www.freshplaza.de/rss.xml",        "name": "FreshPlaza DE",   "lang": "de"},
    {"url": "https://www.agf.nl/rss.xml",               "name": "AGF.nl",          "lang": "nl"},
    {"url": "https://www.freshfruitportal.com/feed/",    "name": "Fresh Fruit Portal", "lang": "en"},
    {"url": "https://east-fruit.com/en/feed/",           "name": "EastFruit",       "lang": "en"},
    {"url": "https://www.hortidaily.com/rss.xml",        "name": "HortiDaily",      "lang": "en"},
    # Priorite moyenne — marche francais, espagnol
    {"url": "https://www.reussir.fr/fruits-legumes/rss.xml", "name": "Reussir F&L", "lang": "fr"},
    {"url": "https://hortoinfo.es/feed/",                "name": "Hortoinfo",       "lang": "es"},
    {"url": "https://citrusindustry.net/feed/",          "name": "Citrus Industry", "lang": "en"},
    # FreshPlaza editions regionales
    {"url": "https://www.freshplaza.com/asia/rss.xml/",           "name": "FreshPlaza Asia",    "lang": "en"},
    {"url": "https://www.freshplaza.com/north-america/rss.xml/",  "name": "FreshPlaza NA",      "lang": "en"},
    {"url": "https://www.freshplaza.com/latin-america/rss.xml/",  "name": "FreshPlaza LatAm",   "lang": "en"},
    {"url": "https://www.freshplaza.com/africa/rss.xml/",         "name": "FreshPlaza Africa",  "lang": "en"},
    {"url": "https://www.freshplaza.com/europe/rss.xml/",         "name": "FreshPlaza Europe", "lang": "en"},
    # Maroc — origine concurrente majeure (avocats, agrumes)
    {"url": "https://www.agrimaroc.ma/feed/",            "name": "AgriMaroc",       "lang": "fr"},
    # Priorite basse — couverture plus large
    {"url": "https://www.producereport.com/rss.xml",     "name": "Produce Report",  "lang": "en"},
    {"url": "https://freshfel.org/feed/",                "name": "Freshfel Europe", "lang": "en"},
]

# ─── Mots-cles produits Mehadrin (multi-langue) ───

MEHADRIN_KEYWORDS = [
    # Avocats (Ophelie)
    "avocat", "avocats", "avocado", "avocados", "aguacate", "hass",
    # Mandarines / Orri (Nadia)
    "orri", "or mehadrin", "or shoham", "mandarine", "mandarines",
    "mandarin", "mandarino", "mandarina", "clementine", "clemenvilla",
    "nadorcott", "clemengold", "jaffa",
    # Pamplemousses (Nadia)
    "pamplemousse", "pamplemousses", "star ruby", "pomelo", "grapefruit",
    "pompelmo", "sweetie",
    # Mangues (Ophelie)
    "mangue", "mangues", "mango", "mangos", "mangoes",
    # Dattes (Jessica)
    "datte", "dattes", "medjoul", "medjool", "datteri", "datil", "dattel",
    # Grenades (Jessica)
    "grenade", "grenades", "pomegranate", "melagrana", "granada", "granatapfel",
    # Kumquat (Jessica)
    "kumquat",
    # Melon / Pasteque (Sebastien)
    "melon", "melons", "melone", "pasteque", "pasteques",
    "watermelon", "anguria", "sandia", "wassermelone",
    # Cerises (Sebastien)
    "cerise", "cerises", "cherry", "cherries", "ciliegia", "cereza", "kirsche",
    # Raisin (Sebastien)
    "raisin", "raisins", "grape", "grapes", "uva", "traube",
    # Patates douces (Ophelie)
    "patate douce", "patates douces", "sweet potato", "sweet potatoes",
    "batata", "susskartoffel",
    # Origines concurrentes
    "israel", "maroc", "morocco", "marocco", "peru", "perou",
    "south africa", "afrique du sud", "sudafrica",
    "egypt", "egypte", "egitto", "cote d'ivoire", "ivory coast",
    "colombia", "colombie", "chile", "chili", "kenya", "jordan", "jordanie",
    "turquie", "turkey", "turchia",
    # Enseignes / marche
    "rungis", "cotation", "prix import", "prix export", "fob",
    "cif", "rnm", "franceagrimer", "ismea",
]

# Mots-cles d'exclusion (articles a ignorer meme si un mot-cle matche)
EXCLUDE_KEYWORDS = [
    # Phytosanitaire / technique
    "mouche des fruits", "fruit fly", "ceratitis", "mosca de la fruta",
    "mouche mediterraneenne", "drosophila", "thrips", "insecte", "parasite",
    "piégeage", "pesticide", "traitement phytosanitaire",
    # Logistique / fret
    "conteneur", "container", "shipping", "fret", "freight",
    "hapag-lloyd", "zim", "cma cgm", "maersk", "route maritime",
    # Sante / nutrition / B2C
    "recette", "recipe", "ricetta", "receta",
    "cholesterol", "vitamin", "health study", "etude sante",
    "bienfait", "nutrient", "antioxidant", "vascular function",
    # Technologie
    "blockchain", "robot", "emballage innovant", "atmosphere controlee",
    "packaging innovant", "robotic",
    # B2C / promo
    "top chef", "sponsoring", "carte fidelite", "appli consommateur",
    # Produits HORS catalogue Mehadrin (eviter faux positifs)
    "tomate", "tomato", "pomodoro", "carotte", "carrot", "oignon", "onion",
    "salade", "lettuce", "endive", "champignon", "mushroom",
    "pomme de terre", "potato", "patata", "asperge", "asparagus",
    "concombre", "cucumber", "courgette", "zucchini", "aubergine",
    "brocoli", "broccoli", "chou", "cabbage", "artichaut",
    "ananas", "pineapple", "banane", "banana", "pomme ", "apple",
    "poire", "pear", "kiwi", "fraise", "strawberry", "framboise",
    "myrtille", "blueberry",
]

# ─── Mapping produit → commercial ───

PRODUCT_TO_COMMERCIAL = {
    # Ophélie — Avocats, Mangues, Patates douces
    "avocat": "Ophélie", "avocats": "Ophélie", "hass": "Ophélie",
    "avocado": "Ophélie", "avocados": "Ophélie", "aguacate": "Ophélie",
    "mangue": "Ophélie", "mangues": "Ophélie", "mango": "Ophélie",
    "mangos": "Ophélie", "mangoes": "Ophélie",
    "patate douce": "Ophélie", "patates douces": "Ophélie",
    "sweet potato": "Ophélie", "sweet potatoes": "Ophélie", "batata": "Ophélie",
    # Nadia — Agrumes (Orri, Star Ruby, Sweetie, Nadorcott, Clemengold)
    "orri": "Nadia", "or mehadrin": "Nadia", "or shoham": "Nadia",
    "mandarine": "Nadia", "mandarines": "Nadia", "mandarin": "Nadia",
    "mandarino": "Nadia", "mandarina": "Nadia",
    "clementine": "Nadia", "clemenvilla": "Nadia",
    "nadorcott": "Nadia", "clemengold": "Nadia",
    "pamplemousse": "Nadia", "pamplemousses": "Nadia",
    "star ruby": "Nadia", "pomelo": "Nadia", "grapefruit": "Nadia",
    "pompelmo": "Nadia", "sweetie": "Nadia",
    # Jessica — Dattes, Grenades, Kumquat
    "datte": "Jessica", "dattes": "Jessica", "medjoul": "Jessica",
    "medjool": "Jessica", "datteri": "Jessica", "datil": "Jessica",
    "grenade": "Jessica", "grenades": "Jessica", "pomegranate": "Jessica",
    "melagrana": "Jessica", "granada": "Jessica",
    "kumquat": "Jessica",
    # Sébastien — Melons, Pastèques, Cerises, Raisin
    "melon": "Sébastien", "melons": "Sébastien", "melone": "Sébastien",
    "pasteque": "Sébastien", "pasteques": "Sébastien",
    "watermelon": "Sébastien", "anguria": "Sébastien", "sandia": "Sébastien",
    "cerise": "Sébastien", "cerises": "Sébastien", "cherry": "Sébastien",
    "cherries": "Sébastien", "ciliegia": "Sébastien", "cereza": "Sébastien",
    "raisin": "Sébastien", "raisins": "Sébastien", "grape": "Sébastien",
    "grapes": "Sébastien", "uva": "Sébastien",
}

COMMERCIAL_COLORS = {
    "Ophélie": "#7c3aed",   # violet
    "Nadia": "#0891b2",     # teal
    "Jessica": "#c2410c",   # orange
    "Sébastien": "#15803d", # vert
}


def detect_commercials(article):
    """Detecte quel(s) commercial(aux) sont concernes par un article.
    Retourne une liste triee de noms uniques."""
    text = f"{article.get('title', '')} {article.get('summary', '')}".lower()
    commercials = set()
    for keyword, commercial in PRODUCT_TO_COMMERCIAL.items():
        if keyword in text:
            commercials.add(commercial)
    return sorted(commercials)


# ─── Parsing RSS ───

# Bypass SSL verification for some feeds
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _fetch_feed(feed_config, timeout=15):
    """Telecharge et parse un flux RSS. Retourne une liste d'articles bruts."""
    url = feed_config["url"]
    name = feed_config["name"]
    lang = feed_config["lang"]
    articles = []

    try:
        req = Request(url, headers={"User-Agent": "MehadrinVeille/1.0"})
        with urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            raw = resp.read()
            # Try UTF-8 first, then latin-1 fallback
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("latin-1")

        root = ET.fromstring(text)

        # Handle RSS 2.0
        for item in root.findall(".//item"):
            title = _get_text(item, "title")
            link = _get_text(item, "link")
            desc = _get_text(item, "description")
            pub_date = _get_text(item, "pubDate")

            if title and link:
                articles.append({
                    "title": _clean_html(title),
                    "link": link.strip(),
                    "summary": _clean_html(desc)[:500] if desc else "",
                    "pub_date": _parse_rss_date(pub_date),
                    "source": name,
                    "lang": lang,
                })

        # Handle Atom feeds
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall(".//atom:entry", ns):
            title = _get_text(entry, "atom:title", ns)
            link_el = entry.find("atom:link[@rel='alternate']", ns)
            if link_el is None:
                link_el = entry.find("atom:link", ns)
            link = link_el.get("href", "") if link_el is not None else ""
            desc = _get_text(entry, "atom:summary", ns) or _get_text(entry, "atom:content", ns)
            pub_date = _get_text(entry, "atom:published", ns) or _get_text(entry, "atom:updated", ns)

            if title and link:
                articles.append({
                    "title": _clean_html(title),
                    "link": link.strip(),
                    "summary": _clean_html(desc)[:500] if desc else "",
                    "pub_date": _parse_rss_date(pub_date),
                    "source": name,
                    "lang": lang,
                })

        print(f"  RSS {name}: {len(articles)} articles")
    except Exception as e:
        print(f"  RSS {name}: ERREUR ({e})")

    return articles


def _get_text(el, tag, ns=None):
    """Extrait le texte d'un sous-element."""
    child = el.find(tag, ns) if ns else el.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return ""


def _clean_html(text):
    """Nettoie les tags HTML et les entites."""
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_rss_date(date_str):
    """Parse les dates RSS en datetime UTC."""
    if not date_str:
        return None
    # RFC 822 format (RSS 2.0)
    for fmt in [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
    ]:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    # Fallback: try dateutil if available
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(date_str)
    except Exception:
        return None


# ─── Filtrage par mots-cles ───

def _matches_mehadrin(article):
    """Verifie si un article contient au moins un mot-cle Mehadrin.
    Retourne le score (nombre de mots-cles trouves) ou 0 si non pertinent.
    """
    text = f"{article['title']} {article['summary']}".lower()

    # Exclusions d'abord
    for kw in EXCLUDE_KEYWORDS:
        if kw in text:
            return 0

    # Comptage des mots-cles
    score = 0
    matched_keywords = []
    for kw in MEHADRIN_KEYWORDS:
        if kw in text:
            score += 1
            matched_keywords.append(kw)

    if score > 0:
        article["_matched_keywords"] = matched_keywords
        article["_score"] = score

    return score


# ─── API publique ───

def fetch_all_feeds(max_age_hours=48):
    """Telecharge tous les flux RSS en parallele et filtre par mots-cles Mehadrin.

    Args:
        max_age_hours: Ne garder que les articles de moins de N heures

    Returns:
        Liste d'articles pertinents tries par score decroissant
    """
    print(f"  Scraping {len(RSS_FEEDS)} flux RSS...")

    all_articles = []

    # Fetch en parallele (max 8 threads)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_fetch_feed, feed): feed for feed in RSS_FEEDS}
        for future in as_completed(futures):
            try:
                articles = future.result()
                all_articles.extend(articles)
            except Exception as e:
                feed = futures[future]
                print(f"  RSS {feed['name']}: EXCEPTION ({e})")

    print(f"  Total brut: {len(all_articles)} articles")

    # Filtrer par age
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    fresh = []
    for a in all_articles:
        if a["pub_date"] and a["pub_date"] > cutoff:
            fresh.append(a)
        elif a["pub_date"] is None:
            fresh.append(a)  # Garder si pas de date (on filtrera avec Gemini)

    print(f"  Apres filtre age (<{max_age_hours}h): {len(fresh)} articles")

    # Filtrer par mots-cles Mehadrin
    candidates = []
    for a in fresh:
        score = _matches_mehadrin(a)
        if score > 0:
            candidates.append(a)

    # Trier par score decroissant
    candidates.sort(key=lambda a: a.get("_score", 0), reverse=True)

    print(f"  Apres filtre mots-cles Mehadrin: {len(candidates)} candidats")

    # Dedup 3 niveaux : article ID cross-langue > URL > titre
    seen_article_ids = set()  # FreshPlaza/AGF article IDs (cross-language)
    seen_urls = set()
    seen_titles = set()
    unique = []
    for a in candidates:
        # Niveau 1 : Article ID (meme article sur freshplaza.com/.fr/.es/.it/.de/agf.nl)
        aid_match = re.search(r"/article/(\d+)/", a["link"])
        if aid_match:
            aid = aid_match.group(1)
            if aid in seen_article_ids:
                print(f"    Dedup article ID {aid}: {a['title'][:50]} ({a['source']})")
                continue
            seen_article_ids.add(aid)

        # Niveau 2 : URL exacte (meme article, meme source)
        url_norm = re.sub(r"[?#].*$", "", a["link"].lower().rstrip("/"))
        if url_norm in seen_urls:
            continue
        seen_urls.add(url_norm)

        # Niveau 3 : Titre normalise (articles similaires de sources differentes)
        t_norm = re.sub(r"[^\w]", "", a["title"].lower())
        if t_norm in seen_titles:
            continue
        seen_titles.add(t_norm)
        unique.append(a)

    print(f"  Apres dedup 3-niveaux (IDs+URLs+titres): {len(unique)} uniques")

    # Tagging commercial pour chaque article
    for a in unique:
        a["_commercials"] = detect_commercials(a)

    return unique


if __name__ == "__main__":
    articles = fetch_all_feeds()
    print(f"\n{'='*60}")
    print(f"  {len(articles)} articles pertinents Mehadrin")
    print(f"{'='*60}")
    for a in articles[:15]:
        kws = ", ".join(a.get("_matched_keywords", [])[:3])
        print(f"  [{a['source']}] ({a['lang']}) score={a['_score']}")
        print(f"    {a['title'][:90]}")
        print(f"    Mots-cles: {kws}")
        print(f"    {a['link'][:80]}")
        print()
