#!/usr/bin/env python3
"""
veille_generator.py -- Generateur de veille marche (cron toutes les 2h)
========================================================================
Pipeline :
  1. Charger les articles existants (anti-doublon)
  2. Generer 4-8 nouveaux articles via Gemini Flash + Google Search Grounding
  3. Fact-check via Gemini Flash + Google Search Grounding (pass 2)
  4. Traduire FR -> EN, FR -> HE
  5. Stocker dans veille_data.json (accumulation 48h)
  6. Exporter veille_live.json pour le front-end
"""

import os
import re
import sys
import time
from datetime import datetime

# Variables d'environnement (GitHub Actions) ou config.py (local)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_API_KEY_DEFAULT = os.environ.get("GEMINI_API_KEY_DEFAULT", "")
GEMINI_MODEL_FLASH = os.environ.get("GEMINI_MODEL_FLASH", "gemini-3-flash-preview")

from veille_prompt import VEILLE_PROMPT_TEMPLATE, FACTCHECK_PROMPT, get_seasonal_products
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
    # Extract only HTML content
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


def _clean_ghost_news(news_html):
    """Supprime les blocs d'actus fantomes (supprimees mais affichees avec placeholder)."""
    ghost_pattern = re.compile(
        r'<div\s+class="news-item">[^<]*(?:<(?!/div>)[^<]*)*'
        r'(?:supprim[ée]|place laissée vide|antérieur au|aucune actualité.*?identifi[ée])'
        r'[^<]*(?:<(?!/div>)[^<]*)*</div>\s*(?:</div>)?',
        re.DOTALL | re.IGNORECASE
    )
    cleaned = ghost_pattern.sub('', news_html)
    if any(w in cleaned.lower() for w in ['supprimé', 'place laissée vide', 'antérieur au']):
        parts = re.split(r'(<div\s+class="news-item">)', cleaned)
        result = []
        skip = False
        for part in parts:
            if '<div class="news-item">' in part:
                skip = False
                result.append(part)
            elif skip:
                continue
            else:
                low = part.lower()
                if any(w in low for w in ['supprimé', 'place laissée vide', 'antérieur au']):
                    if result and '<div class="news-item">' in result[-1]:
                        result.pop()
                    skip = True
                    continue
                result.append(part)
        cleaned = ''.join(result)
    return cleaned


def _strip_emojis(text):
    """Supprime tous les emojis du texte."""
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map
        "\U0001F1E0-\U0001F1FF"  # flags
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "\U0001f900-\U0001f9FF"
        "\U0001fa00-\U0001fa6f"
        "\U0001fa70-\U0001faff"
        "\U00002600-\U000026FF"
        "\U0000FE00-\U0000FE0F"
        "\U0000200D"
        "]+",
        flags=re.UNICODE,
    )
    return emoji_pattern.sub('', text)


def generate_veille(force=False):
    """Execute un cycle complet de generation de veille.

    Args:
        force: Si True, ignore le rate limit (pour tests)

    Returns:
        dict: Donnees JSON pour le front-end, ou None si echec/rate-limited
    """
    print("=" * 60)
    print(f"  VEILLE MARCHE -- {datetime.now().strftime('%d/%m/%Y %H:%M')}")
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

    # 3. Preparer le prompt
    gemini_key = GEMINI_API_KEY or GEMINI_API_KEY_DEFAULT or os.environ.get("GEMINI_API_KEY", "")
    if not gemini_key:
        print("  ERREUR : Aucune cle API Gemini disponible")
        return None

    now = datetime.now()
    date_str = now.strftime("%d/%m/%Y %H:%M")
    week_num = now.isocalendar()[1]
    seasonal = get_seasonal_products(week_num)
    seasonal_str = ", ".join(seasonal) if seasonal else "Dattes Medjoul (toute l'annee)"

    previous_titles = get_previous_titles(data)
    prev_titles_str = "\n".join(f"- {t}" for t in previous_titles[-20:]) if previous_titles else "Aucun (premier cycle)"

    prompt = VEILLE_PROMPT_TEMPLATE.format(
        date=date_str,
        week_num=week_num,
        seasonal_products=seasonal_str,
        previous_titles=prev_titles_str,
    )

    # 4. PASS 1 : Generation via Gemini Flash + Google Search Grounding
    raw_news = ""
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=gemini_key)
        search_config = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
        )

        print(f"  Pass 1/2 : Gemini Flash + Google Search (sem {week_num})...")
        print(f"  Produits en saison : {seasonal_str}")

        response = _gemini_call_with_retry(
            client,
            model=GEMINI_MODEL_FLASH,
            contents=[prompt],
            config=search_config,
        )
        raw_news = response.text or ""
        print(f"  Pass 1 -- raw: {len(raw_news)} chars")
        raw_news = _clean_api_html(raw_news)
        raw_news = _strip_emojis(raw_news)
        print(f"  Pass 1 -- cleaned: {len(raw_news)} chars")

    except Exception as e:
        import traceback
        print(f"  ERREUR Pass 1 (Gemini): {e}")
        traceback.print_exc()
        # Conserver les articles existants
        frontend_json = get_articles_json_for_frontend(data)
        _save_frontend_json(frontend_json)
        return frontend_json

    if not raw_news or len(raw_news) < 100:
        print("  Pass 1 vide ou trop courte -- articles existants conserves")
        frontend_json = get_articles_json_for_frontend(data)
        _save_frontend_json(frontend_json)
        return frontend_json

    # 5. PASS 2 : Fact-check
    checked_news = raw_news  # fallback
    try:
        factcheck_prompt = FACTCHECK_PROMPT.format(
            news_html=raw_news,
            report_date=date_str,
        )

        print("  Pass 2/2 : Fact-check Gemini Flash + Google Search...")
        response2 = _gemini_call_with_retry(
            client,
            model=GEMINI_MODEL_FLASH,
            contents=[factcheck_prompt],
            config=search_config,
        )
        fc_result = _clean_api_html(response2.text or "")
        fc_result = _strip_emojis(fc_result)
        print(f"  Pass 2 -- cleaned: {len(fc_result)} chars")

        if fc_result and len(fc_result) > 200:
            checked_news = _clean_ghost_news(fc_result)
            print("  Fact-check OK")
        else:
            print("  Pass 2 trop courte -- utilisation Pass 1")
            checked_news = _clean_ghost_news(raw_news)

    except Exception as e:
        print(f"  Fact-check echoue ({e}) -- utilisation Pass 1")
        checked_news = _clean_ghost_news(raw_news)

    # 6. Traduire FR -> EN, HE
    print("  Traductions EN/HE...")
    try:
        html_en, html_he = translate_all(checked_news)
    except Exception as e:
        print(f"  Traduction echouee ({e}) -- EN/HE vides")
        html_en, html_he = "", ""

    # 7. Stocker les nouveaux articles (FR + EN + HE)
    added = add_articles(data, checked_news, html_en, html_he)
    print(f"  {added} nouveaux articles ajoutes (total: {len(data['articles'])})")

    save_data(data)

    # 8. Exporter le JSON front-end
    frontend_json = get_articles_json_for_frontend(data)
    _save_frontend_json(frontend_json)

    print("=" * 60)
    print(f"  VEILLE TERMINEE -- {len(frontend_json['articles'])} articles disponibles")
    print("=" * 60)

    return frontend_json


def _save_frontend_json(frontend_json):
    """Sauvegarde le JSON optimise pour le front-end."""
    import json
    with open(LIVE_JSON, "w", encoding="utf-8") as f:
        json.dump(frontend_json, f, ensure_ascii=False, indent=2)
    print(f"  JSON front-end sauvegarde : {LIVE_JSON}")


if __name__ == "__main__":
    # Execution directe = force (ignore rate limit)
    result = generate_veille(force=True)
    if result:
        print(f"\nResultat : {result['article_count']} articles")
        for a in result["articles"][:3]:
            print(f"  - [{a['category']}] {a['title'][:80]}")
    else:
        print("\nAucun resultat genere.")
