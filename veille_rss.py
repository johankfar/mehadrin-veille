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
from urllib.parse import urljoin
import ssl
import html

# ─── Configuration des flux RSS ───
# Le registre des sources vit dans veille_sources.py (existantes + ajoutees le 24/10/2026).
# Chaque flux garde url/name/lang et porte en plus source_id, groupe, filtre, country, kind.

from veille_sources import (
    SOURCES, GROUPE_FRESHPLAZA, equilibrer, pertinent_generaliste, mots_trouves, cle_sujet_multilingue,
)

RSS_FEEDS = [
    {"url": s["url"], "name": s["name"], "lang": s["lang"], "source_id": s["id"],
     "groupe": s["groupe"], "filtre": s["filtre"], "country": s["country"], "kind": s["kind"],
     "format": s.get("format", "rss"), "html": s.get("html"),
     "ignorer_sans_extrait": s.get("ignorer_sans_extrait", False)}
    for s in SOURCES
]

# ─── Mots-cles produits Mehadrin (multi-langue) ───

MEHADRIN_KEYWORDS = [
    # Avocats (Ophelie)
    "avocat", "avocats", "avocado", "avocados", "aguacate", "hass",
    # Mandarines / Orri (Nadia)
    "orri", "or mehadrin", "or shoham", "mandarine", "mandarines",
    "mandarin", "mandarino", "mandarina", "clementine", "clemenvilla",
    "nadorcott", "clemengold", "jaffa",
    # Oranges (Nadia)
    "orange", "oranges", "naranja", "arancia", "arance", "blood orange",
    "sanguine", "sanguinella", "tarocco", "navel", "valencia late",
    # Agrumes generique (Nadia)
    "agrume", "agrumes", "citrus", "agrumi", "citrico", "citricos",
    "zitrus", "zitronen",
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

# Exclusions ABSOLUES — toujours exclure, meme si pays frontalier
EXCLUDE_ALWAYS = [
    # Citrons / Lemons — Mehadrin n'en vend pas
    "citron", "citrons", "lemon", "lemons", "limon", "limone", "limoni",
    "lime ", "limes ", "lima ",
    "zitrone", "zitronen",
    # Phytosanitaire / technique
    "mouche des fruits", "fruit fly", "ceratitis", "mosca de la fruta",
    "mouche mediterraneenne", "drosophila", "thrips", "insecte", "parasite",
    "piégeage", "pesticide", "traitement phytosanitaire",
    # Logistique / fret (sauf impact direct supply)
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
    # Marchés non-européens (hors scope)
    "azerbaidjan", "azerbaijan", "azerbaiyan",
]

# Exclusions PRODUIT — ne s'appliquent PAS si l'article mentionne
# un pays frontalier/concurrent d'Israel ou un marche europeen cle
EXCLUDE_PRODUCTS = [
    "tomate", "tomato", "pomodoro", "carotte", "carrot", "oignon", "onion",
    "salade", "lettuce", "endive", "champignon", "mushroom",
    "pomme de terre", "potato", "patata", "asperge", "asparagus",
    "concombre", "cucumber", "courgette", "zucchini", "aubergine",
    "brocoli", "broccoli", "chou", "cabbage", "artichaut",
    "ananas", "pineapple", "banane", "banana", "pomme ", "apple",
    "poire", "pear", "kiwi", "fraise", "strawberry", "framboise",
    "myrtille", "blueberry",
]

# Pays frontaliers/concurrents d'Israel — si l'article en mentionne un,
# les exclusions produit ne s'appliquent PAS (on garde tout F&L de ces pays)
NEIGHBORING_COUNTRIES = [
    "israel", "egypte", "egypt", "egitto", "egipto", "agypten",
    "jordanie", "jordan", "giordania", "jordanien",
    "turquie", "turkey", "turchia", "turkei",
    "maroc", "morocco", "marocco", "marruecos", "marokko",
    "espagne", "spain", "spagna", "spanien",
    "grece", "greece", "grecia", "griechenland",
    "kenya", "peru", "perou",
    "afrique du sud", "south africa", "sudafrica",
    "colombie", "colombia", "kolumbien",
    "chili", "chile",
]

# Combine pour compatibilite (la fonction _matches_mehadrin utilise EXCLUDE_KEYWORDS)
EXCLUDE_KEYWORDS = EXCLUDE_ALWAYS + EXCLUDE_PRODUCTS

# ─── Mapping produit → commercial ───

PRODUCT_TO_COMMERCIAL = {
    # Ophélie — Avocats, Mangues, Patates douces
    "avocat": "Ophélie", "avocats": "Ophélie", "hass": "Ophélie",
    "avocado": "Ophélie", "avocados": "Ophélie", "aguacate": "Ophélie",
    "mangue": "Ophélie", "mangues": "Ophélie", "mango": "Ophélie",
    "mangos": "Ophélie", "mangoes": "Ophélie",
    "patate douce": "Ophélie", "patates douces": "Ophélie",
    "sweet potato": "Ophélie", "sweet potatoes": "Ophélie", "batata": "Ophélie",
    # Nadia — Agrumes (Oranges, Citrons, Orri, Star Ruby, Sweetie, Nadorcott, Clemengold)
    "orange": "Nadia", "oranges": "Nadia", "naranja": "Nadia",
    "arancia": "Nadia", "arance": "Nadia", "blood orange": "Nadia",
    "sanguine": "Nadia", "sanguinella": "Nadia", "tarocco": "Nadia",
    "navel": "Nadia", "valencia late": "Nadia",
        "agrume": "Nadia", "agrumes": "Nadia", "citrus": "Nadia",
    "agrumi": "Nadia", "citrico": "Nadia", "citricos": "Nadia",
    "zitrus": "Nadia", "zitronen": "Nadia",
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

        if feed_config.get("format", "rss") == "wpjson":
            articles = parse_wpjson(text, feed_config)
            print(f"  WPJSON {name}: {len(articles)} articles")
            return articles
        if feed_config.get("format", "rss") == "html":
            articles = parse_html(text, feed_config)
            print(f"  HTML {name}: {len(articles)} articles")
            return articles

        root = ET.fromstring(text.strip())  # tolerant : certains serveurs mettent un espace avant <?xml

        # Handle RSS 2.0
        for item in root.findall(".//item"):
            title = _get_text(item, "title")
            link = _get_text(item, "link")
            desc = _get_text(item, "description")
            pub_date = _get_text(item, "pubDate")

            if title and link:
                articles.append({
                    "title": _clean_html(title),
                    "link": _safe_link(urljoin(url, link)),
                    "summary": _clean_html(desc)[:500] if desc else "",
                    "pub_date": _parse_rss_date(pub_date),
                    "source": name,
                    "lang": lang,
                    "source_id": feed_config.get("source_id", name),
                    "groupe": feed_config.get("groupe", feed_config.get("source_id", name)),
                    "filtre": feed_config.get("filtre", "specialise"),
                    "country": feed_config.get("country", ""),
                    "kind": feed_config.get("kind", ""),
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
                    "link": _safe_link(urljoin(url, link)),
                    "summary": _clean_html(desc)[:500] if desc else "",
                    "pub_date": _parse_rss_date(pub_date),
                    "source": name,
                    "lang": lang,
                    "source_id": feed_config.get("source_id", name),
                    "groupe": feed_config.get("groupe", feed_config.get("source_id", name)),
                    "filtre": feed_config.get("filtre", "specialise"),
                    "country": feed_config.get("country", ""),
                    "kind": feed_config.get("kind", ""),
                })

        print(f"  RSS {name}: {len(articles)} articles")
    except Exception as e:
        print(f"  RSS {name}: ERREUR ({type(e).__name__}: {e})")

    return articles


def parse_wpjson(texte, feed_config):
    """Lecteur WordPress REST (/wp-json/wp/v2/posts) : JSON -> articles bruts (meme forme que le RSS)."""
    import json
    posts = json.loads(texte)
    if not isinstance(posts, list):
        raise ValueError("reponse wp-json inattendue (liste de posts attendue)")
    name = feed_config["name"]
    articles = []
    for p in posts:
        if not isinstance(p, dict):
            continue
        title = _clean_html(((p.get("title") or {}).get("rendered")) or "")
        link = _safe_link(urljoin(feed_config.get("url", ""), p.get("link", "")))
        corps = ((p.get("excerpt") or {}).get("rendered")) or ((p.get("content") or {}).get("rendered")) or ""
        date = p.get("date_gmt") or p.get("date") or ""  # _fields=...date : heure du site, lue comme UTC (approximation)
        if date and not re.search(r"(Z|[+-]\d\d:?\d\d)$", date):
            date += "Z"
        if not (title and link):
            continue
        extrait = _clean_html(corps)
        if feed_config.get("ignorer_sans_extrait") and not extrait:
            continue  # ex. AgriMaroc : items reserves aux abonnes (extrait vide, titre brouille)
        articles.append({
            "title": title,
            "link": link,
            "summary": extrait[:500],
            "pub_date": _parse_rss_date(date) if date else None,
            "source": name,
            "lang": feed_config["lang"],
            "source_id": feed_config.get("source_id", name),
            "groupe": feed_config.get("groupe", feed_config.get("source_id", name)),
            "filtre": feed_config.get("filtre", "specialise"),
            "country": feed_config.get("country", ""),
            "kind": feed_config.get("kind", ""),
        })
    return articles


def parse_html(texte, feed_config):
    """Adaptateur HTML minimal : feed_config["html"] = {"item": regex (groupes nommes title, href, [resume], [date]),
    "date_format": strptime, "tz_offset_hours": decalage de l'heure locale}. Page 1 seulement (jamais de pagination)."""
    cfg = feed_config["html"]
    rx = re.compile(cfg["item"], re.S)
    vus, articles = set(), []
    for m in rx.finditer(texte):
        g = m.groupdict()
        title = _clean_html(g.get("title") or "")
        link = _safe_link(urljoin(feed_config.get("url", ""), html.unescape(g.get("href") or "")))
        if not (title and link) or link in vus:
            continue
        vus.add(link)
        pub = None
        if g.get("date") and cfg.get("date_format"):
            try:
                dt = datetime.strptime(g["date"].strip(), cfg["date_format"])
                pub = dt.replace(tzinfo=timezone(timedelta(hours=cfg.get("tz_offset_hours", 0)))).astimezone(timezone.utc)
            except ValueError:
                pub = None
        name = feed_config["name"]
        articles.append({
            "title": title, "link": link, "summary": _clean_html(g.get("resume") or "")[:500], "pub_date": pub,
            "source": name, "lang": feed_config["lang"],
            "source_id": feed_config.get("source_id", name),
            "groupe": feed_config.get("groupe", feed_config.get("source_id", name)),
            "filtre": feed_config.get("filtre", "specialise"),
            "country": feed_config.get("country", ""), "kind": feed_config.get("kind", ""),
        })
    return articles


def _get_text(el, tag, ns=None):
    """Extrait le texte d'un sous-element."""
    child = el.find(tag, ns) if ns else el.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return ""


def _safe_link(url):
    # F10 : le lien est reproduit VERBATIM dans un href par le prompt Gemini.
    # On n'autorise que http/https et on retire les caracteres qui casseraient
    # l'attribut (guillemets, chevrons, backtick, espaces/retours). Defense-in-depth.
    u = str(url or '').strip()
    if not re.match(r'^https?://', u, re.IGNORECASE):
        return ''
    return re.sub(r'[\s"\'<>`]', '', u)


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

    Logique d'exclusion a 2 niveaux :
    - EXCLUDE_ALWAYS : toujours exclure (phyto, tech, B2C)
    - EXCLUDE_PRODUCTS : exclure SAUF si l'article mentionne un pays
      frontalier/concurrent d'Israel (on garde tout F&L de ces pays)
    """
    text = f"{article['title']} {article['summary']}".lower()

    # Exclusions absolues (toujours)
    for kw in EXCLUDE_ALWAYS:
        if kw in text:
            return 0

    # Exclusions produit (conditionnelles)
    # Si l'article mentionne un pays frontalier → on garde quand meme
    mentions_neighbor = any(country in text for country in NEIGHBORING_COUNTRIES)
    if not mentions_neighbor:
        for kw in EXCLUDE_PRODUCTS:
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


# ─── Dedup semantique (produit+pays) ───

_SUBJECT_PRODUCTS = {
    "avocat": "avocat", "avocado": "avocat", "aguacate": "avocat", "hass": "avocat",
    "avocados": "avocat",
    "mangue": "mangue", "mangues": "mangue", "mango": "mangue", "mangos": "mangue",
    "mangoes": "mangue",
    "datte": "datte", "dattes": "datte", "medjoul": "datte", "medjool": "datte",
    "datteri": "datte",
    "mandarin": "mandarin", "mandarine": "mandarin", "mandarines": "mandarin",
    "orri": "mandarin", "clementine": "mandarin", "nadorcott": "mandarin",
    "clemengold": "mandarin",
    "pamplemousse": "pamplemousse", "grapefruit": "pamplemousse", "pomelo": "pamplemousse",
    "star ruby": "pamplemousse",
    "patate douce": "patate", "patates douces": "patate", "sweet potato": "patate",
    "sweet potatoes": "patate", "batata": "patate",
    "grenade": "grenade", "grenades": "grenade", "pomegranate": "grenade",
    "melon": "melon", "melons": "melon",
    "pasteque": "pasteque", "watermelon": "pasteque",
    "cerise": "cerise", "cerises": "cerise", "cherry": "cerise", "cherries": "cerise",
    "raisin": "raisin", "raisins": "raisin", "grape": "raisin", "grapes": "raisin",
    "uva": "raisin",
    "agrume": "agrume", "agrumes": "agrume", "citrus": "agrume",
}

_SUBJECT_ORIGINS = {
    "kenya": "kenya", "kenyan": "kenya",
    "rwanda": "rwanda", "rwandais": "rwanda", "rwandan": "rwanda",
    "maroc": "maroc", "morocco": "maroc", "marocain": "maroc", "moroccan": "maroc",
    "israel": "israel", "israelien": "israel", "israeli": "israel",
    "grece": "grece", "grec": "grece", "greek": "grece", "greece": "grece",
    "perou": "perou", "peru": "perou", "peruvian": "perou",
    "egypte": "egypte", "egypt": "egypte", "egyptien": "egypte", "egyptian": "egypte",
    "afrique du sud": "afrique_sud", "south africa": "afrique_sud",
    "sud-africain": "afrique_sud", "south african": "afrique_sud",
    "espagne": "espagne", "spain": "espagne", "espagnol": "espagne", "spanish": "espagne",
    "turquie": "turquie", "turkey": "turquie",
    "inde": "inde", "india": "inde", "indian": "inde",
    "chili": "chili", "chile": "chili", "chilean": "chili",
    "bresil": "bresil", "brazil": "bresil",
    "colombie": "colombie", "colombia": "colombie",
    "argentine": "argentine", "argentina": "argentine",
    "zambie": "zambie", "zambia": "zambie",
    "cote d'ivoire": "cote_ivoire", "ivory coast": "cote_ivoire",
}


def _extract_subject_key(title):
    """Extrait (produit, pays) du titre pour dedup semantique.
    Retourne un frozenset ou None si pas assez d'info."""
    t = title.lower()
    found_prods = set()
    found_origins = set()
    for kw, norm in _SUBJECT_PRODUCTS.items():
        if kw in t:
            found_prods.add(norm)
    for kw, norm in _SUBJECT_ORIGINS.items():
        if kw in t:
            found_origins.add(norm)
    if found_prods and found_origins:
        return frozenset(found_prods | found_origins)
    return None


# ─── API publique ───

def _matches_generaliste(article):
    """Presse generaliste : exclusions absolues (EXCLUDE_ALWAYS) puis produit ET (origine OU marche).
    Retourne le score (nombre de termes trouves) ou 0."""
    text = f"{article['title']} {article['summary']}".lower()
    for kw in EXCLUDE_ALWAYS:
        if kw in text:
            return 0
    if re.search(r"(?<![a-z])lim[oó]n", text):  # « limón » (accent) : citron, non Mehadrin
        return 0
    if not pertinent_generaliste(article["title"], article["summary"]):
        return 0
    prod, orig, march = mots_trouves(f"{article['title']} {article['summary']}")
    article["_matched_keywords"] = prod + orig + march
    article["_score"] = len(article["_matched_keywords"])
    return article["_score"]


def _matches_specialise_produit(article):
    """Comme _matches_mehadrin, mais un mot PRODUIT (veille_sources.mots_trouves) est obligatoire."""
    score = _matches_mehadrin(article)
    if score > 0 and not mots_trouves(f"{article['title']} {article['summary']}")[0]:
        return 0
    return score


def _score_apres(article):
    """Score d'un article selon le `filtre` de sa source (mode "apres")."""
    f = article.get("filtre")
    if f == "generaliste":
        return _matches_generaliste(article)
    if f == "specialise_produit":  # sources AJOUTEES de presse specialisee / donnees
        return _matches_specialise_produit(article)
    return _matches_mehadrin(article)  # sources d'origine : comportement d'origine, aucune perte


def cle_sujet(titre):
    """Cle de sujet pour le dedoublonnage : multilingue (produit+origine canoniques) sinon ancienne cle."""
    return cle_sujet_multilingue(titre) or _extract_subject_key(titre)


def selectionner(articles_bruts, maintenant, mode="apres", max_age_hours=48):
    """Fonction pure : articles bruts -> candidats pertinents, dedoublonnes (sans reseau).

    mode="avant" : comportement d'origine (filtre mots-cles Mehadrin pour tous, tri par score,
                   dedup 4 niveaux, aucun equilibre).
    mode="apres" : filtre selon `filtre` de la source (specialise = inchange ; generaliste =
                   exclusions absolues + produit ET origine/marche), tri par score,
                   equilibre avant dedup (le dedup par sujet prefere les sources non-FreshPlaza),
                   dedup 4 niveaux inchange, equilibre apres.
    """
    # Filtrer par age
    cutoff = maintenant - timedelta(hours=max_age_hours)
    fresh = []
    for a in articles_bruts:
        if a["pub_date"] and a["pub_date"] > cutoff:
            fresh.append(a)
        elif a["pub_date"] is None:
            fresh.append(a)  # Garder si pas de date (on filtrera avec Gemini)

    print(f"  Apres filtre age (<{max_age_hours}h): {len(fresh)} articles")

    # Filtrer par mots-cles Mehadrin
    candidates = []
    for a in fresh:
        if mode == "apres":
            score = _score_apres(a)
        else:
            score = _matches_mehadrin(a)
        if score > 0:
            candidates.append(a)

    # Trier par score decroissant
    candidates.sort(key=lambda a: a.get("_score", 0), reverse=True)

    print(f"  Apres filtre mots-cles Mehadrin: {len(candidates)} candidats")

    if mode == "apres":
        candidates = equilibrer(candidates)

    # Dedup 4 niveaux : article ID cross-langue > URL > titre > sujet-cle
    seen_article_ids = set()  # FreshPlaza/AGF article IDs (cross-language)
    seen_urls = set()
    seen_titles = set()
    seen_subjects = set()  # (produit, pays) pour dedup semantique
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

        # Niveau 4 : Sujet-cle (meme produit + meme pays = meme sujet)
        subject = cle_sujet(a["title"]) if mode == "apres" else _extract_subject_key(a["title"])
        if subject and subject in seen_subjects:
            print(f"    Dedup sujet {subject}: {a['title'][:50]} ({a['source']})")
            continue
        if subject:
            seen_subjects.add(subject)

        unique.append(a)

    print(f"  Apres dedup 4-niveaux (IDs+URLs+titres+sujets): {len(unique)} uniques")

    if mode == "apres":
        unique = equilibrer(unique)

    # Tagging commercial pour chaque article
    for a in unique:
        a["_commercials"] = detect_commercials(a)

    return unique


def fetch_all_feeds(max_age_hours=48):
    """Telecharge tous les flux RSS en parallele puis applique selectionner().

    Args:
        max_age_hours: Ne garder que les articles de moins de N heures

    Returns:
        Liste d'articles pertinents (score, puis equilibre entre sources). Un flux en erreur
        n'arrete jamais le cycle : son statut est journalise.
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

    return selectionner(all_articles, datetime.now(timezone.utc), mode="apres",
                        max_age_hours=max_age_hours)


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
