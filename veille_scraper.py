#!/usr/bin/env python3
"""
veille_scraper.py -- Scraper de VRAIS articles multi-sources RSS
================================================================
Pipeline V3 : multi-sources, multi-langues (FR/EN/IT/ES/DE).
Chaque article retourne = vrai article, vrai lien, vrai contenu.
ZERO invention.
"""

import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from urllib.parse import quote

import feedparser

# ─── Mots-cles de filtrage (multi-langue FR/EN/IT/ES/DE) ───
# Chaque concept a un "group" pour eviter le double-comptage :
# ex: "avocado" (EN) et "avocat" (FR) = meme concept, compte 1 seule fois

# Mapping mot-cle -> groupe conceptuel (pour dedup scoring)
KEYWORD_GROUPS = {
    # Avocat/Hass
    "avocat": "avocat", "avocats": "avocat", "hass": "hass", "avocado": "avocat",
    "aguacate": "avocat",  # ES
    # Mandarine
    "mandarine": "mandarine", "mandarines": "mandarine", "clementine": "mandarine",
    "clemenvilla": "mandarine", "orri": "orri", "nadorcott": "nadorcott",
    "clemengold": "clemengold", "mandarino": "mandarine", "mandarini": "mandarine",
    "mandarina": "mandarine", "mandarinas": "mandarine",
    "clementina": "mandarine", "Mandarine": "mandarine",
    # Pamplemousse
    "pamplemousse": "pamplemousse", "pamplemousses": "pamplemousse",
    "pomelo": "pamplemousse", "star ruby": "star_ruby", "sweetie": "sweetie",
    "grapefruit": "pamplemousse", "pompelmo": "pamplemousse", "Grapefruit": "pamplemousse",
    # Mangue
    "mangue": "mangue", "mangues": "mangue", "mango": "mangue",
    # Datte
    "datte": "datte", "dattes": "datte", "medjoul": "medjoul", "medjool": "medjoul",
    "dattero": "datte", "datteri": "datte",  # IT
    "datil": "datte", "datiles": "datte",  # ES
    "Dattel": "datte", "Datteln": "datte",  # DE
    # Grenade
    "grenade": "grenade", "grenades": "grenade", "pomegranate": "grenade",
    "melograno": "grenade",  # IT
    "granada": "grenade",  # ES
    "Granatapfel": "grenade",  # DE
    # Kumquat
    "kumquat": "kumquat",
    # Melon
    "melon": "melon", "melons": "melon", "melone": "melon",
    # Pasteque
    "pasteque": "pasteque", "pasteques": "pasteque", "watermelon": "pasteque",
    "anguria": "pasteque", "cocomero": "pasteque",  # IT
    "sandia": "pasteque",  # ES
    "Wassermelone": "pasteque",  # DE
    # Cerise
    "cerise": "cerise", "cerises": "cerise", "cherry": "cerise", "cherries": "cerise",
    "ciliegia": "cerise", "ciliegie": "cerise",  # IT
    "cereza": "cerise", "cerezas": "cerise",  # ES
    "Kirsche": "cerise", "Kirschen": "cerise",  # DE
    # Raisin
    "raisin": "raisin", "raisins": "raisin", "grape": "raisin", "grapes": "raisin",
    "uva": "raisin", "uve": "raisin",  # IT/ES
    "Traube": "raisin", "Trauben": "raisin",  # DE
    # Patate douce
    "patate douce": "patate_douce", "patates douces": "patate_douce",
    "sweet potato": "patate_douce", "patata dolce": "patate_douce",  # IT
    "batata": "patate_douce", "boniato": "patate_douce",  # ES
    "Susskartoffel": "patate_douce",  # DE
    # Marque
    "mehadrin": "mehadrin", "israel": "israel",
}

MEHADRIN_KEYWORDS = list(KEYWORD_GROUPS.keys()) + [
    # Origines concurrentes (pas de groupe necessaire, pas de doublon)
    "israelien", "israeli",
    "maroc", "marocain", "marocco", "Marokko",
    "egypte", "egyptien", "egitto", "egipto", "Agypten",
    "perou", "peruvien", "peru",
    "afrique du sud", "sud-africain", "sudafrica", "Sudafrika",
    "espagne", "espagnol", "spagna", "Spanien",
    "bresil", "bresilien", "brasile", "brasil",
    "colombie", "colombia", "Kolumbien",
    "turquie", "turchia", "turquia",
    "chili", "chilien", "cile",
    "cote d'ivoire", "costa d'avorio",
    # Marche / distribution (multi-langue)
    "rungis", "min de rungis",
    "import", "export", "importacion", "exportacion",
    "esportazione", "importazione",  # IT
    "Einfuhr", "Ausfuhr",  # DE
    "calibre", "calibro", "Kaliber",
    "cotation", "cours", "cotizacion", "quotazione", "Notierung",
    "grande distribution", "gms", "enseigne",
    "GDO",  # IT (grande distribuzione organizzata)
    "supermercado", "hipermercado",  # ES
]

# Mots-cles FORTS (fruits Mehadrin directs) — score x3
STRONG_KEYWORDS = [
    # FR
    "avocat", "avocats", "hass",
    "mandarine", "mandarines", "orri", "nadorcott", "clemengold",
    "pamplemousse", "pamplemousses", "star ruby", "sweetie",
    "mangue", "mangues",
    "datte", "dattes", "medjoul", "medjool",
    "kumquat",
    "patate douce", "patates douces",
    "mehadrin", "israel",
    # EN
    "avocado", "grapefruit", "mango", "sweet potato", "pomegranate",
    # IT
    "avocado", "pompelmo", "mandarino", "mandarini", "dattero", "datteri",
    "melograno", "anguria",
    # ES
    "aguacate", "pomelo", "mandarina", "datil", "granada", "sandia",
    # DE
    "Mandarine", "Grapefruit", "Dattel", "Datteln", "Granatapfel", "Wassermelone",
]

# Mots-cles d'exclusion (produits hors catalogue, multi-langue)
EXCLUDE_KEYWORDS = [
    # FR
    "tomate", "carotte", "oignon", "salade", "endive", "champignon",
    "poireau", "echalote", "pomme de terre", "asperge", "haricot",
    "laitue", "concombre", "poivron", "courgette", "aubergine",
    "chou", "brocoli", "artichaut", "betterave", "navet",
    "ail ", "persil", "basilic", "herbe",
    # EN
    "tomato", "carrot", "onion", "lettuce", "cucumber", "pepper",
    "zucchini", "eggplant", "cabbage", "broccoli", "asparagus",
    "potato", "bean", "pea",
    # IT
    "pomodoro", "pomodori", "carota", "cipolla", "insalata",
    "cetriolo", "peperone", "zucchina", "melanzana", "cavolo",
    "patata", "fagiolo", "pisello",
    # ES
    "tomate", "zanahoria", "cebolla", "lechuga", "pepino",
    "pimiento", "calabacin", "berenjena", "col", "brocoli",
    "patata", "judia",
    # DE
    "Tomate", "Karotte", "Zwiebel", "Salat", "Gurke",
    "Paprika", "Zucchini", "Aubergine", "Kohl", "Brokkoli",
    "Kartoffel", "Bohne", "Erbse", "Spargel",
    # Autres exclusions (language-neutral)
    "robot", "technologie", "piege", "lygus", "thrips",
    "salon", "conference", "emballage", "packaging",
    "conteneur", "fret maritime", "transport routier",
    "hapag-lloyd", "zim", "shipping", "container",
]

# Sources RSS (V3 multi-sources)
RSS_FEEDS = [
    # ─── Existants ───
    {
        "name": "FreshPlaza FR",
        "url": "https://www.freshplaza.fr/rss.xml/",
        "lang": "fr",
    },
    {
        "name": "FreshPlaza EN",
        "url": "https://www.freshplaza.com/europe/rss.xml/",
        "lang": "en",
    },
    # ─── Phase B : nouvelles sources ───
    {
        "name": "FreshPlaza IT",
        "url": "https://www.freshplaza.it/rss.xml/",
        "lang": "it",
    },
    {
        "name": "Fresh Fruit Portal",
        "url": "https://www.freshfruitportal.com/feed/",
        "lang": "en",
    },
    {
        "name": "Hortoinfo",
        "url": "https://hortoinfo.es/feed/",
        "lang": "es",
    },
]


USER_AGENTS = [
    "MehadrinVeille/3.0 (+market-watch)",
    "Mozilla/5.0 (compatible; MehadrinBot/3.0)",
]


def fetch_rss(feed_url, timeout=10):
    """Fetch et parse un flux RSS via feedparser. Gere RSS 2.0, Atom, etc."""
    ua = USER_AGENTS[hash(feed_url) % len(USER_AGENTS)]
    try:
        parsed = feedparser.parse(feed_url, request_headers={"User-Agent": ua})
        if parsed.bozo and not parsed.entries:
            print(f"  ERREUR parse RSS {feed_url}: {parsed.bozo_exception}")
            return []
    except Exception as e:
        print(f"  ERREUR fetch RSS {feed_url}: {e}")
        return []

    articles = []
    for entry in parsed.entries:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        pub_date = entry.get("published") or entry.get("updated") or ""
        # Description: try summary, then content
        description = ""
        if entry.get("summary"):
            description = entry.summary.strip()
        elif entry.get("content"):
            description = entry.content[0].get("value", "").strip()

        if title and link:
            articles.append({
                "title": title,
                "url": link,
                "pub_date": pub_date,
                "description": description,
            })

    return articles


def filter_relevant_articles(articles, max_age_hours=48):
    """Filtre les articles pertinents pour Mehadrin.

    Scoring avec deduplication conceptuelle : "avocado" (EN) et "avocat" (FR)
    dans le meme article = 1 seul concept, compte 1 seule fois.
    """
    relevant = []

    for art in articles:
        text = (art["title"] + " " + art.get("description", "")).lower()

        # Check exclusions first
        excluded = False
        for kw in EXCLUDE_KEYWORDS:
            if kw.lower() in text:
                # Only exclude if no strong Mehadrin keyword is present
                has_strong = any(sk.lower() in text for sk in STRONG_KEYWORDS)
                if not has_strong:
                    excluded = True
                    break

        if excluded:
            continue

        # Scoring with concept dedup:
        # Track which concept groups have already been counted
        scored_groups = set()
        score = 0
        matched_keywords = []

        # Strong keywords worth 3 points (deduped by group)
        for kw in STRONG_KEYWORDS:
            if kw.lower() in text:
                group = KEYWORD_GROUPS.get(kw.lower(), kw.lower())
                if group not in scored_groups:
                    score += 3
                    scored_groups.add(group)
                    matched_keywords.append(kw)

        # Regular keywords worth 1 point (deduped by group)
        for kw in MEHADRIN_KEYWORDS:
            if kw.lower() in text:
                group = KEYWORD_GROUPS.get(kw.lower(), kw.lower())
                if group not in scored_groups:
                    score += 1
                    scored_groups.add(group)
                    matched_keywords.append(kw)

        if score >= 3:  # At least 1 strong keyword or 3 weak ones
            art["relevance_score"] = score
            art["matched_keywords"] = matched_keywords
            relevant.append(art)

    # Sort by relevance score (most relevant first)
    relevant.sort(key=lambda x: x["relevance_score"], reverse=True)
    return relevant


def _fetch_and_filter(feed):
    """Fetch un flux RSS et filtre les articles pertinents. Thread-safe."""
    try:
        raw = fetch_rss(feed["url"])
        relevant = filter_relevant_articles(raw)
        for art in relevant:
            art["source_name"] = feed["name"]
            art["source_lang"] = feed["lang"]
            art["content"] = art.get("description", "").strip()
        return feed["name"], len(raw), relevant
    except Exception as e:
        print(f"  ERREUR {feed['name']}: {e}")
        return feed["name"], 0, []


def scrape_real_articles():
    """Pipeline complet : fetch RSS parallele -> filter -> retourne vrais articles.

    Pas de cap max : la validation Gemini en aval filtre la pertinence.

    Returns:
        list[dict]: Articles reels avec {title, url, pub_date, content, keywords, source_name, source_lang}
    """
    all_articles = []

    # Fetch all feeds in parallel
    print(f"  Fetching {len(RSS_FEEDS)} sources en parallele...")
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(_fetch_and_filter, feed): feed for feed in RSS_FEEDS}
        for future in as_completed(futures):
            name, total, relevant = future.result()
            print(f"    {name}: {total} articles, {len(relevant)} pertinents")
            all_articles.extend(relevant)

    # Filter out articles with no content, sort by score
    all_articles = [a for a in all_articles if a.get("content")]
    all_articles.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
    print(f"  {len(all_articles)} articles avec contenu")

    for i, a in enumerate(all_articles[:15]):
        print(f"    {i+1}. [{a.get('relevance_score',0)}pts] [{a['source_name']}] {a['title'][:50]}")

    return all_articles


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    articles = scrape_real_articles()
    for i, a in enumerate(articles):
        print(f"\n{'='*60}")
        print(f"  {i+1}. {a['title']}")
        print(f"  URL: {a['url']}")
        print(f"  Keywords: {a.get('matched_keywords', [])}")
        print(f"  Content: {a.get('content', '')[:200]}...")
