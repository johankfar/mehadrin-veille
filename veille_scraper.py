#!/usr/bin/env python3
"""
veille_scraper.py -- Scraper de VRAIS articles depuis FreshPlaza RSS
====================================================================
Remplace la generation synthetique par des articles REELS avec VRAIS liens.

Pipeline :
  1. Fetch RSS FreshPlaza.fr (articles FR)
  2. Filtrer par mots-cles produits Mehadrin
  3. Fetch le contenu de chaque article pertinent
  4. Resumer avec Gemini Flash (resume, PAS invention)
  5. Categoriser (PRIX & VOLUMES, ALERTES SUPPLY, etc.)

Chaque article retourne = vrai article, vrai lien, vrai contenu.
"""

import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from urllib.parse import quote

# ─── Mots-cles de filtrage ───

MEHADRIN_KEYWORDS = [
    # Fruits Mehadrin
    "avocat", "avocats", "hass", "avocado",
    "mandarine", "mandarines", "clementine", "clemenvilla", "orri", "nadorcott", "clemengold",
    "pamplemousse", "pamplemousses", "pomelo", "star ruby", "sweetie", "grapefruit",
    "mangue", "mangues", "mango",
    "datte", "dattes", "medjoul", "medjool",
    "grenade", "grenades", "pomegranate",
    "kumquat",
    "melon", "melons",
    "pasteque", "pasteques", "watermelon",
    "cerise", "cerises", "cherry",
    "raisin", "raisins", "grape",
    "patate douce", "patates douces", "sweet potato",
    # Origines concurrentes
    "israel", "israelien", "israeli",
    "maroc", "marocain",
    "egypte", "egyptien",
    "perou", "peruvien",
    "afrique du sud", "sud-africain",
    "espagne", "espagnol",
    "bresil", "bresilien",
    "colombie",
    "turquie",
    "chili", "chilien",
    "cote d'ivoire",
    # Marche / distribution
    "rungis", "min de rungis",
    "import", "export",
    "calibre", "cotation", "cours",
    "grande distribution", "gms", "enseigne",
]

# Mots-cles FORTS (fruits Mehadrin directs) — score x3
STRONG_KEYWORDS = [
    "avocat", "avocats", "hass", "avocado",
    "mandarine", "mandarines", "orri", "nadorcott", "clemengold",
    "pamplemousse", "pamplemousses", "pomelo", "star ruby", "sweetie", "grapefruit",
    "mangue", "mangues", "mango",
    "datte", "dattes", "medjoul", "medjool",
    "kumquat",
    "patate douce", "patates douces", "sweet potato",
    "mehadrin", "israel",
]

# Mots-cles d'exclusion (produits hors catalogue)
EXCLUDE_KEYWORDS = [
    "tomate", "carotte", "oignon", "salade", "endive", "champignon",
    "poireau", "echalote", "pomme de terre", "asperge", "haricot",
    "laitue", "concombre", "poivron", "courgette", "aubergine",
    "chou", "brocoli", "artichaut", "betterave", "navet",
    "ail ", "persil", "basilic", "herbe",
    # Autres exclusions
    "robot", "technologie", "piege", "lygus", "thrips",
    "salon", "conference", "emballage", "packaging",
    "conteneur", "fret maritime", "transport routier",
]

# Sources RSS
RSS_FEEDS = [
    {
        "name": "FreshPlaza FR",
        "url": "https://www.freshplaza.fr/rss.xml/",
        "lang": "fr",
        "base_url": "https://www.freshplaza.fr",
    },
    {
        "name": "FreshPlaza EN",
        "url": "https://www.freshplaza.com/europe/rss.xml/",
        "lang": "en",
        "base_url": "https://www.freshplaza.com",
    },
]


def fetch_rss(feed_url, timeout=15):
    """Fetch et parse un flux RSS. Retourne une liste d'articles bruts."""
    import urllib.request

    req = urllib.request.Request(feed_url)
    req.add_header("User-Agent", "MehadrinVeille/1.0")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        xml_data = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  ERREUR fetch RSS {feed_url}: {e}")
        return []

    articles = []
    try:
        root = ET.fromstring(xml_data)
        # RSS 2.0 format
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            description = (item.findtext("description") or "").strip()

            if title and link:
                articles.append({
                    "title": title,
                    "url": link,
                    "pub_date": pub_date,
                    "description": description,
                })
    except ET.ParseError as e:
        print(f"  ERREUR parse RSS: {e}")

    return articles


def filter_relevant_articles(articles, max_age_hours=48):
    """Filtre les articles pertinents pour Mehadrin."""
    relevant = []

    for art in articles:
        text = (art["title"] + " " + art.get("description", "")).lower()

        # Check exclusions first
        excluded = False
        for kw in EXCLUDE_KEYWORDS:
            if kw.lower() in text:
                # Check if it's not a false positive (e.g., "asperge" in a general market article)
                # Only exclude if no Mehadrin keyword is present
                has_mehadrin = any(mk.lower() in text for mk in MEHADRIN_KEYWORDS[:30])  # Fruit keywords only
                if not has_mehadrin:
                    excluded = True
                    break

        if excluded:
            continue

        # Check if article matches Mehadrin keywords
        score = 0
        matched_keywords = []
        # Strong keywords worth 3 points
        for kw in STRONG_KEYWORDS:
            if kw.lower() in text:
                score += 3
                matched_keywords.append(kw)
        # Regular keywords worth 1 point
        for kw in MEHADRIN_KEYWORDS:
            if kw.lower() in text and kw not in matched_keywords:
                score += 1
                matched_keywords.append(kw)

        if score >= 3:  # At least 1 strong keyword or 3 weak ones
            art["relevance_score"] = score
            art["matched_keywords"] = matched_keywords
            relevant.append(art)

    # Sort by relevance score (most relevant first)
    relevant.sort(key=lambda x: x["relevance_score"], reverse=True)
    return relevant


def scrape_real_articles(max_articles=8):
    """Pipeline complet : fetch RSS -> filter -> utilise description RSS comme contenu.

    FreshPlaza est derriere un paywall, mais le RSS contient des descriptions
    substantielles (1-2 paragraphes) qui suffisent pour un resume.

    Returns:
        list[dict]: Articles reels avec {title, url, pub_date, content, keywords, source_name}
    """
    all_articles = []

    for feed in RSS_FEEDS:
        print(f"  Fetching RSS: {feed['name']}...")
        raw = fetch_rss(feed["url"])
        print(f"    {len(raw)} articles dans le flux")

        relevant = filter_relevant_articles(raw)
        print(f"    {len(relevant)} articles pertinents Mehadrin")

        for art in relevant[:max_articles]:
            art["source_name"] = feed["name"]
            art["source_lang"] = feed["lang"]
            # Use RSS description as content (no paywall needed)
            art["content"] = art.get("description", "").strip()
            all_articles.append(art)

    # Filter out articles with no content
    top = [a for a in all_articles[:max_articles] if a.get("content")]
    print(f"  {len(top)} articles avec contenu")

    for i, a in enumerate(top):
        print(f"    {i+1}. [{a.get('relevance_score',0)}pts] {a['title'][:55]}")

    return top


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    articles = scrape_real_articles()
    for i, a in enumerate(articles):
        print(f"\n{'='*60}")
        print(f"  {i+1}. {a['title']}")
        print(f"  URL: {a['url']}")
        print(f"  Keywords: {a.get('matched_keywords', [])}")
        print(f"  Content: {a.get('content', '')[:200]}...")
