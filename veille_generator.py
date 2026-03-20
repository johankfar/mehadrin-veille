#!/usr/bin/env python3
"""
veille_generator.py -- Generateur de veille marche HYBRID (RSS + Gemini)
=========================================================================
V4 HYBRID Pipeline :
  1. Scraper RSS (10+ sources, ~200-300 articles)
  2. Filtre mots-cles Mehadrin (~20-40 candidats)
  3. Gemini Flash filtre pertinence + ecrit impact tactique (4-8 articles)
  4. Traduire FR -> EN, FR -> HE
  5. Auto-fix traductions manquantes
  6. Stocker dans veille_data.json (accumulation 48h)
  7. Exporter veille_live.json pour le front-end

ZERO HALLUCINATION : les articles viennent du RSS, les liens sont reels.
Gemini ne GENERE pas, il FILTRE et ENRICHIT.
"""

import json
import os
import re
import sys
import time
from datetime import datetime

# Ajouter le dossier parent pour importer config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from config import GEMINI_API_KEY, GEMINI_API_KEY_DEFAULT, GEMINI_MODEL_FLASH
except ImportError:
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
    GEMINI_API_KEY_DEFAULT = os.environ.get("GEMINI_API_KEY_DEFAULT", "")
    GEMINI_MODEL_FLASH = "gemini-3-flash-preview"

from veille_prompt import HYBRID_FILTER_PROMPT, get_seasonal_products, get_off_season_products
from veille_rss import fetch_all_feeds
from veille_storage import (
    load_data, save_data, purge_old_articles, can_generate,
    get_previous_titles, add_articles, get_articles_json_for_frontend,
)
from veille_translate import translate_all

# Chemin du JSON front-end
DATA_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_JSON = os.path.join(DATA_DIR, "veille_live.json")


def _gemini_call_with_retry(client, max_retries=3, initial_wait=5, **kwargs):
    """Call Gemini API with automatic retry on transient errors."""
    for attempt in range(max_retries):
        try:
            return client.models.generate_content(**kwargs)
        except Exception as e:
            err_str = str(e).lower()
            retryable = any(k in err_str for k in ("429", "500", "503", "timeout", "resource_exhausted", "unavailable"))
            if retryable and attempt < max_retries - 1:
                wait = initial_wait * (2 ** attempt)
                print(f"  Attente {wait}s avant retry ({attempt+1}/{max_retries})...")
                time.sleep(wait)
            else:
                raise


def _clean_api_html(text):
    """Clean API response: remove markdown fences, backticks, extract HTML."""
    text = re.sub(r'```\w*\s*', '', text.strip())
    text = text.replace('`', '')
    text = re.sub(r'\n\s*---\s*\n', '\n', text)
    text = re.sub(r'\n\s*\*\*\*\s*\n', '\n', text)
    first_div = text.find('<div')
    if first_div > 0:
        text = text[first_div:]
    last_div = text.rfind('</div>')
    if last_div > 0:
        text = text[:last_div + 6]
    else:
        opens = text.count('<div')
        closes = text.count('</div>')
        while closes < opens:
            text += '</div>'
            closes += 1
    return text


def _strip_emojis(text):
    """Supprime tous les emojis du texte."""
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF"
        "\U00002702-\U000027B0\U000024C2-\U0001F251"
        "\U0001f900-\U0001f9FF\U0001fa00-\U0001fa6f"
        "\U0001fa70-\U0001faff\U00002600-\U000026FF"
        "\U0000FE00-\U0000FE0F\U0000200D"
        "]+",
        flags=re.UNICODE,
    )
    return emoji_pattern.sub('', text)


def _format_rss_articles_for_prompt(articles, max_articles=30):
    """Formate les articles RSS pour le prompt Gemini."""
    lines = []
    for i, a in enumerate(articles[:max_articles], 1):
        pub = ""
        if a.get("pub_date"):
            pub = a["pub_date"].strftime("%d/%m/%Y %H:%M")
        kws = ", ".join(a.get("_matched_keywords", [])[:5])
        lines.append(
            f"--- ARTICLE {i} ---\n"
            f"Titre: {a['title']}\n"
            f"Source: {a['source']} ({a['lang']})\n"
            f"Date: {pub}\n"
            f"Lien: {a['link']}\n"
            f"Resume: {a['summary'][:400]}\n"
            f"Mots-cles Mehadrin: {kws}\n"
        )
    return "\n".join(lines)


def generate_veille(force=False):
    """Execute un cycle complet de veille HYBRID (RSS + Gemini).

    Args:
        force: Si True, ignore le rate limit (pour tests)

    Returns:
        dict: Donnees JSON pour le front-end, ou None si echec/rate-limited
    """
    print("=" * 60)
    print(f"  VEILLE MARCHE HYBRID -- {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print("=" * 60)

    # 1. Charger donnees existantes
    data = load_data()
    purge_old_articles(data)

    # 2. Rate limit check
    if not force and not can_generate(data):
        print("  Cycle ignore (rate limit). Articles existants conserves.")
        frontend_json = get_articles_json_for_frontend(data)
        _save_frontend_json(frontend_json)
        return frontend_json

    # 3. ETAPE 1 : Scraper RSS
    print("\n  ETAPE 1 : Scraping RSS...")
    rss_articles = fetch_all_feeds(max_age_hours=48)

    if not rss_articles:
        print("  Aucun article RSS pertinent trouve. Articles existants conserves.")
        frontend_json = get_articles_json_for_frontend(data)
        _save_frontend_json(frontend_json)
        return frontend_json

    # 4. Filtrer les doublons avec les articles existants
    #    5 niveaux : URL + article ID + titre exact + sujet-cle + titre fuzzy
    existing_titles = set()
    existing_urls = set()
    existing_article_ids = set()
    existing_subject_keys = set()
    existing_title_words = []  # Pour Jaccard fuzzy

    for a in data.get("articles", []):
        # Titre exact
        t = re.sub(r"[^\w]", "", a.get("title", "").lower())
        existing_titles.add(t)
        # Mots du titre pour fuzzy
        t_words = set(re.sub(r"[^\w\s]", "", a.get("title", "").lower()).split())
        if t_words:
            existing_title_words.append(t_words)
        # URLs dans le HTML
        for url_match in re.findall(r'href="([^"]+)"', a.get("content_fr", "")):
            existing_urls.add(re.sub(r"[?#].*$", "", url_match.lower().rstrip("/")))
        # Article IDs FreshPlaza
        for key in ["content_fr", "content_en", "content_he"]:
            for m in re.finditer(r'/article/(\d+)/', a.get(key, "")):
                existing_article_ids.add(m.group(1))
        # Sujet-cle (produit+pays)
        from veille_rss import _extract_subject_key
        sk = _extract_subject_key(a.get("title", ""))
        if sk:
            existing_subject_keys.add(sk)

    new_rss = []
    for a in rss_articles:
        # Niveau 1 : URL exacte
        url_norm = re.sub(r"[?#].*$", "", a["link"].lower().rstrip("/"))
        if url_norm in existing_urls:
            continue
        # Niveau 2 : Article ID FreshPlaza
        aid_match = re.search(r"/article/(\d+)/", a["link"])
        if aid_match and aid_match.group(1) in existing_article_ids:
            continue
        # Niveau 3 : Titre exact
        t_norm = re.sub(r"[^\w]", "", a["title"].lower())
        if t_norm in existing_titles:
            continue
        # Niveau 4 : Sujet-cle (meme produit + meme pays = deja couvert)
        sk = _extract_subject_key(a["title"])
        if sk and sk in existing_subject_keys:
            print(f"    Dedup sujet-cle vs existants: {a['title'][:60]}")
            continue
        # Niveau 5 : Titre fuzzy (Jaccard >= 0.55 = trop similaire)
        a_words = set(re.sub(r"[^\w\s]", "", a["title"].lower()).split())
        is_fuzzy_dup = False
        if a_words:
            for ex_words in existing_title_words:
                intersection = a_words & ex_words
                union = a_words | ex_words
                if union and len(intersection) / len(union) >= 0.55:
                    is_fuzzy_dup = True
                    break
        if is_fuzzy_dup:
            print(f"    Dedup fuzzy vs existants: {a['title'][:60]}")
            continue

        new_rss.append(a)

    print(f"  Apres dedup 5-niveaux vs existants: {len(new_rss)} nouveaux articles")

    if not new_rss:
        print("  Tous les articles RSS sont deja connus. Articles existants conserves.")
        frontend_json = get_articles_json_for_frontend(data)
        _save_frontend_json(frontend_json)
        return frontend_json

    # 5. ETAPE 2 : Gemini filtre + enrichit
    gemini_key = GEMINI_API_KEY or GEMINI_API_KEY_DEFAULT or os.environ.get("GEMINI_API_KEY", "")
    if not gemini_key:
        print("  ERREUR : Aucune cle API Gemini disponible")
        return None

    now = datetime.now()
    date_str = now.strftime("%d/%m/%Y %H:%M")
    week_num = now.isocalendar()[1]
    seasonal = get_seasonal_products(week_num)
    seasonal_str = ", ".join(seasonal) if seasonal else "Dattes Medjoul (toute l'annee)"
    off_season = get_off_season_products(week_num)
    off_season_str = ", ".join(off_season) if off_season else "Aucun"

    # Injecter les commerciaux dans chaque article pour le prompt
    articles_text = _format_rss_articles_for_prompt(new_rss)

    # Ajouter les titres deja publies pour eviter doublons Gemini
    existing_titles = get_previous_titles(data)
    existing_titles_text = ""
    if existing_titles:
        titles_list = "\n".join(f"- {t}" for t in existing_titles[:20])
        existing_titles_text = (
            f"\n\nARTICLES DEJA PUBLIES (NE PAS reproduire un article sur le meme sujet) :\n{titles_list}\n"
        )

    prompt = HYBRID_FILTER_PROMPT.format(
        article_count=len(new_rss[:30]),
        date=date_str,
        week_num=week_num,
        seasonal_products=seasonal_str,
        off_season_products=off_season_str,
        articles_text=articles_text,
    ) + existing_titles_text

    enriched_html = ""
    try:
        from google import genai

        client = genai.Client(api_key=gemini_key)

        print(f"\n  ETAPE 2 : Gemini Flash filtre + enrichit {len(new_rss[:30])} articles...")

        response = _gemini_call_with_retry(
            client,
            model=GEMINI_MODEL_FLASH,
            contents=[prompt],
        )
        raw = response.text or ""
        print(f"  Gemini raw: {len(raw)} chars")

        if "AUCUN_ARTICLE_PERTINENT" in raw:
            print("  Gemini: aucun article pertinent. Articles existants conserves.")
            frontend_json = get_articles_json_for_frontend(data)
            _save_frontend_json(frontend_json)
            return frontend_json

        enriched_html = _clean_api_html(raw)
        enriched_html = _strip_emojis(enriched_html)
        print(f"  Gemini cleaned: {len(enriched_html)} chars")

    except Exception as e:
        import traceback
        print(f"  ERREUR Gemini: {e}")
        traceback.print_exc()
        frontend_json = get_articles_json_for_frontend(data)
        _save_frontend_json(frontend_json)
        return frontend_json

    if not enriched_html or len(enriched_html) < 100:
        print("  Gemini output trop court. Articles existants conserves.")
        frontend_json = get_articles_json_for_frontend(data)
        _save_frontend_json(frontend_json)
        return frontend_json

    # 6. ETAPE 3 : Traductions EN / HE
    print("\n  ETAPE 3 : Traductions...")
    html_en, html_he = translate_all(enriched_html)

    # 7. Stocker les nouveaux articles
    added = add_articles(data, enriched_html, html_en, html_he)
    print(f"  {added} nouveaux articles ajoutes (total: {len(data['articles'])})")

    # 7b. Auto-fix traductions manquantes sur TOUS les articles
    _fix_missing_translations(data)

    save_data(data)

    # 8. Exporter le JSON front-end
    frontend_json = get_articles_json_for_frontend(data)
    _save_frontend_json(frontend_json)

    print("\n" + "=" * 60)
    print(f"  VEILLE HYBRID TERMINEE -- {len(frontend_json['articles'])} articles")
    print("=" * 60)

    return frontend_json


def _fix_missing_translations(data):
    """Auto-traduit les articles qui n'ont pas de traduction EN ou HE."""
    from veille_translate import translate_html

    fixed = 0
    for a in data.get("articles", []):
        fr = a.get("content_fr", "")
        if not fr or len(fr) < 50:
            continue

        if not a.get("content_en") or len(a["content_en"]) < 20:
            print(f"  Auto-trad EN: {a.get('title', '')[:50]}")
            a["content_en"] = translate_html(fr, "en")
            fixed += 1
            time.sleep(0.5)

        if not a.get("content_he") or len(a["content_he"]) < 20:
            print(f"  Auto-trad HE: {a.get('title', '')[:50]}")
            a["content_he"] = translate_html(fr, "he")
            fixed += 1
            time.sleep(0.5)

    if fixed:
        print(f"  {fixed} traductions manquantes corrigees")
    else:
        print(f"  Toutes les traductions sont completes")


def _save_frontend_json(frontend_json):
    """Sauvegarde le JSON optimise pour le front-end."""
    with open(LIVE_JSON, "w", encoding="utf-8") as f:
        json.dump(frontend_json, f, ensure_ascii=False, indent=2)
    print(f"  JSON front-end sauvegarde : {LIVE_JSON}")


if __name__ == "__main__":
    result = generate_veille(force=True)
    if result:
        print(f"\nResultat : {result['article_count']} articles")
        for a in result["articles"][:5]:
            print(f"  - [{a['category']}] {a['title'][:80]}")
    else:
        print("\nAucun resultat genere.")
