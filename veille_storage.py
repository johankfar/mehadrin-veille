#!/usr/bin/env python3
"""
veille_storage.py -- Stockage JSON pour la veille marche (accumulation 48h)
============================================================================
- Stocke les articles avec timestamp de generation
- Accumule les articles des dernieres 48h
- Anti-doublon par titre
- Purge auto des articles > 48h
- Garde le dernier JSON valide si nouvelle generation echoue
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone

# Chemin du fichier de donnees
DATA_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(DATA_DIR, "veille_data.json")

# Duree de retention des articles
RETENTION_HOURS = 48

# Rate limit : intervalle minimum entre deux generations
MIN_INTERVAL_MINUTES = 110  # ~2h, avec marge


def _now_utc():
    return datetime.now(timezone.utc)


def _parse_iso(s):
    """Parse ISO timestamp, handling both aware and naive formats."""
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return _now_utc()


def load_data():
    """Charge le fichier JSON. Retourne la structure par defaut si absent/corrompu."""
    default = {
        "last_generated": None,
        "articles": [],  # list of {id, timestamp, title_hash, content_fr, content_en, content_he}
    }
    if not os.path.exists(DATA_FILE):
        return default
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "articles" not in data:
            data["articles"] = []
        return data
    except (json.JSONDecodeError, OSError):
        return default


def save_data(data):
    """Sauvegarde le fichier JSON."""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _title_hash(title):
    """Normalise un titre pour comparaison anti-doublon.

    V3: Supprime aussi les dates/heures du titre pour eviter que
    le meme sujet avec des dates differentes passe le filtre.
    Ex: "Avocats Hass 17/03/2026 14:15" et "Avocats Hass 18/03/2026 09:00"
    doivent avoir le meme hash si le sujet est identique.
    """
    # Enlever HTML
    t = re.sub(r'<[^>]+>', '', title)
    # Enlever dates (DD/MM/YYYY, YYYY-MM-DD, DD.MM.YYYY)
    t = re.sub(r'\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}', '', t)
    # Enlever heures (HH:MM, HHhMM)
    t = re.sub(r'\d{1,2}[h:]\d{2}', '', t)
    # Enlever nombres isoles (annees, timestamps)
    t = re.sub(r'\b\d{4,}\b', '', t)
    # Enlever ponctuation, lowercase
    t = re.sub(r'[^\w\s]', '', t.lower())
    # Enlever mots tres courts (le, la, de, du, des, un, une, et, a, en)
    t = re.sub(r'\b(le|la|les|de|du|des|un|une|et|a|en|au|aux|pour|par|sur|dans|avec|son|sa|ses)\b', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def _titles_similar(hash1, hash2, threshold=0.75):
    """Verifie si deux hashes de titre sont similaires (Jaccard sur les mots).

    Permet de detecter les doublons meme avec des reformulations legeres.
    Ex: "22 millions cartons avocats sud-africains" vs
        "exportations avocats sud-africains millions cartons"
    """
    words1 = set(hash1.split())
    words2 = set(hash2.split())
    if not words1 or not words2:
        return False
    intersection = words1 & words2
    union = words1 | words2
    jaccard = len(intersection) / len(union) if union else 0
    return jaccard >= threshold


def _extract_titles(html):
    """Extrait les titres depuis le HTML de veille."""
    return re.findall(r'<div class="news-title">(.*?)</div>', html, re.DOTALL)


def _extract_articles(html):
    """Extrait les blocs news-item individuels depuis le HTML."""
    # Split by news-item blocks
    pattern = r'(<div class="news-item">.*?</div>\s*</div>\s*</div>\s*</div>)'
    articles = re.findall(pattern, html, re.DOTALL)
    if not articles:
        # Fallback: try simpler split
        parts = re.split(r'(?=<div class="news-item">)', html)
        articles = [p.strip() for p in parts if '<div class="news-item">' in p]
    return articles


def _extract_category(article_html):
    """Extrait la categorie d'un article HTML."""
    m = re.search(r'<div class="news-cat">(.*?)</div>', article_html)
    if m:
        cat = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        # Normaliser (enlever emojis residuels)
        cat = re.sub(r'[^\w\s&]', '', cat).strip()
        return cat
    return "PRIX & VOLUMES"


def purge_old_articles(data):
    """Supprime les articles > 48h."""
    cutoff = _now_utc() - timedelta(hours=RETENTION_HOURS)
    before = len(data["articles"])
    data["articles"] = [
        a for a in data["articles"]
        if _parse_iso(a.get("timestamp", "")) > cutoff
    ]
    purged = before - len(data["articles"])
    if purged > 0:
        print(f"  Purge : {purged} articles > {RETENTION_HOURS}h supprimes")
    return data


def can_generate(data):
    """Verifie si on peut lancer une nouvelle generation (rate limit)."""
    last = data.get("last_generated")
    if not last:
        return True
    last_dt = _parse_iso(last)
    elapsed = (_now_utc() - last_dt).total_seconds() / 60
    if elapsed < MIN_INTERVAL_MINUTES:
        print(f"  Rate limit : derniere generation il y a {elapsed:.0f} min (min {MIN_INTERVAL_MINUTES} min)")
        return False
    return True


def get_previous_titles(data):
    """Retourne les titres des articles existants (pour anti-doublon dans le prompt)."""
    titles = []
    for a in data.get("articles", []):
        t = a.get("title", "")
        if t:
            titles.append(t)
    return titles


def add_articles(data, articles_html_fr, articles_html_en="", articles_html_he=""):
    """Ajoute les nouveaux articles au stockage, avec deduplication.

    articles_html_fr: HTML brut contenant les news-items en francais
    articles_html_en: HTML traduit en anglais
    articles_html_he: HTML traduit en hebreu

    Returns: nombre d'articles ajoutes
    """
    fr_items = _extract_articles(articles_html_fr)
    en_items = _extract_articles(articles_html_en) if articles_html_en else []
    he_items = _extract_articles(articles_html_he) if articles_html_he else []

    # Titres existants pour dedup (exact + fuzzy)
    existing_hashes = {a.get("title_hash", "") for a in data["articles"]}

    added = 0
    now_iso = _now_utc().isoformat()

    for i, fr_html in enumerate(fr_items):
        titles = _extract_titles(fr_html)
        title_text = titles[0] if titles else f"Article {i}"
        title_text_clean = re.sub(r'<[^>]+>', '', title_text).strip()
        t_hash = _title_hash(title_text)

        # Check exact match
        if t_hash in existing_hashes:
            print(f"  Doublon exact ignore : {title_text_clean[:60]}")
            continue

        # Check fuzzy match (Jaccard similarity)
        is_similar = False
        for existing_hash in existing_hashes:
            if existing_hash and _titles_similar(t_hash, existing_hash):
                print(f"  Doublon similaire ignore : {title_text_clean[:60]}")
                is_similar = True
                break
        if is_similar:
            continue

        article = {
            "id": f"{int(_now_utc().timestamp())}_{i}",
            "timestamp": now_iso,
            "title": title_text_clean,
            "title_hash": t_hash,
            "category": _extract_category(fr_html),
            "content_fr": fr_html,
            "content_en": en_items[i] if i < len(en_items) else "",
            "content_he": he_items[i] if i < len(he_items) else "",
        }
        data["articles"].insert(0, article)  # Plus recents en haut
        existing_hashes.add(t_hash)
        added += 1

    data["last_generated"] = now_iso
    return added


def get_articles_for_display(data, lang="fr"):
    """Retourne le HTML combine des articles pour une langue donnee.
    Articles tries par timestamp decroissant (plus recents en haut).
    """
    key = f"content_{lang}"
    # Trier par timestamp decroissant
    sorted_articles = sorted(
        data.get("articles", []),
        key=lambda a: a.get("timestamp", ""),
        reverse=True,
    )
    parts = [a.get(key, a.get("content_fr", "")) for a in sorted_articles if a.get(key) or a.get("content_fr")]
    return "\n".join(parts)


def get_articles_json_for_frontend(data):
    """Retourne un JSON optimise pour le front-end avec les 3 langues."""
    purge_old_articles(data)
    sorted_articles = sorted(
        data.get("articles", []),
        key=lambda a: a.get("timestamp", ""),
        reverse=True,
    )
    return {
        "last_updated": data.get("last_generated"),
        "article_count": len(sorted_articles),
        "articles": [
            {
                "id": a["id"],
                "timestamp": a["timestamp"],
                "title": a.get("title", ""),
                "category": a.get("category", ""),
                "content_fr": a.get("content_fr", ""),
                "content_en": a.get("content_en", ""),
                "content_he": a.get("content_he", ""),
            }
            for a in sorted_articles
        ],
    }
