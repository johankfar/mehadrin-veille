#!/usr/bin/env python3
"""
veille_generator.py -- Generateur de veille marche (cron toutes les 2h)
========================================================================
Pipeline V2 -- VRAIS ARTICLES UNIQUEMENT :
  1. Scraper RSS FreshPlaza FR + EN (vrais articles, vrais liens)
  2. Filtrer par mots-cles produits Mehadrin
  3. Gemini Flash : categoriser + ajouter impact tactique (PAS inventer)
  4. Traduire FR -> EN, FR -> HE
  5. Stocker dans veille_data.json (accumulation 48h)
  6. Exporter veille_live.json pour le front-end

ZERO invention. Chaque article = vrai article avec vrai lien.
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

from veille_scraper import scrape_real_articles
from veille_storage import (
    load_data, save_data, purge_old_articles, can_generate,
    get_previous_titles, add_articles, get_articles_json_for_frontend,
)
from veille_translate import translate_all

# Chemin du JSON front-end
DATA_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_JSON = os.path.join(DATA_DIR, "veille_live.json")

# Categories de renseignement
CATEGORIES = ["PRIX & VOLUMES", "ALERTES SUPPLY", "MOUVEMENTS ENSEIGNES", "CONCURRENCE ORIGINES"]


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


def _categorize_articles(client, articles):
    """Utilise Gemini Flash pour categoriser chaque article et ajouter un impact tactique.

    NE genere PAS de contenu — utilise uniquement le titre et la description existants.
    """
    if not articles:
        return articles

    # Build prompt with all articles
    articles_text = ""
    for i, art in enumerate(articles):
        articles_text += f"\n--- Article {i+1} ---\n"
        articles_text += f"Titre: {art['title']}\n"
        articles_text += f"Contenu: {art.get('content', '')[:500]}\n"

    prompt = f"""Tu es un analyste commercial B2B fruits et legumes pour Mehadrin France (exportateur israelien).

Pour chaque article ci-dessous, donne :
1. La CATEGORIE parmi : PRIX & VOLUMES, ALERTES SUPPLY, MOUVEMENTS ENSEIGNES, CONCURRENCE ORIGINES
2. Un IMPACT TACTIQUE en 1 phrase (que fait le commercial avec cette info en rendez-vous ?)

Reponds STRICTEMENT en JSON, un objet par article :
[
  {{"index": 0, "category": "PRIX & VOLUMES", "impact": "Phrase d'impact tactique"}},
  ...
]

PAS de markdown, PAS de commentaires. Juste le JSON.

Articles :
{articles_text}"""

    try:
        resp = _gemini_call_with_retry(
            client,
            model=GEMINI_MODEL_FLASH,
            contents=[prompt],
        )
        raw = resp.text or ""
        raw = re.sub(r'```\w*\s*', '', raw).strip().strip('`')

        import json
        results = json.loads(raw)
        for entry in results:
            idx = entry.get("index", -1)
            if 0 <= idx < len(articles):
                articles[idx]["category"] = entry.get("category", "PRIX & VOLUMES")
                articles[idx]["impact"] = entry.get("impact", "")
        print(f"  {len(results)} articles categorises")

    except Exception as e:
        print(f"  Categorisation echouee ({e}) -- categories par defaut")
        for art in articles:
            if "category" not in art:
                art["category"] = "PRIX & VOLUMES"
            if "impact" not in art:
                art["impact"] = ""

    return articles


def _format_articles_html(articles, date_str):
    """Formate les articles reels en HTML pour le front-end.

    Chaque article contient un VRAI lien vers l'article source.
    """
    html_parts = []
    for art in articles:
        cat = art.get("category", "PRIX & VOLUMES")
        title = art["title"]
        url = art["url"]
        content = art.get("content", "")
        impact = art.get("impact", "")
        source_name = art.get("source_name", "FreshPlaza")

        # Build body
        body = content
        if impact:
            body += f' <strong>Impact tactique :</strong> {impact}'

        html = (
            f'<div class="news-item">\n'
            f'  <div class="news-cat">{cat}</div>\n'
            f'  <div class="news-title">{title} -- <span class="news-date">{date_str}</span></div>\n'
            f'  <div class="news-body">{body}</div>\n'
            f'  <div class="news-source"><a href="{url}" target="_blank">{source_name} — Lire l\'article</a></div>\n'
            f'</div>'
        )
        html_parts.append(html)

    return "\n".join(html_parts)


def generate_veille(force=False):
    """Execute un cycle complet de generation de veille.

    Pipeline V2 : vrais articles uniquement.

    Args:
        force: Si True, ignore le rate limit (pour tests)

    Returns:
        dict: Donnees JSON pour le front-end, ou None si echec/rate-limited
    """
    print("=" * 60)
    print(f"  VEILLE MARCHE V2 -- {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print("  Mode : VRAIS ARTICLES (scraping RSS)")
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

    # 3. Scraper les VRAIS articles depuis RSS
    print("\n  === SCRAPING RSS ===")
    real_articles = scrape_real_articles(max_articles=8)

    if not real_articles:
        print("  Aucun article pertinent trouve dans les RSS")
        frontend_json = get_articles_json_for_frontend(data)
        _save_frontend_json(frontend_json)
        return frontend_json

    # 4. Deduplication vs articles existants
    previous_titles = get_previous_titles(data)
    prev_set = set(t.lower()[:50] for t in previous_titles)
    new_articles = [a for a in real_articles if a["title"].lower()[:50] not in prev_set]
    print(f"  {len(new_articles)} nouveaux (vs {len(real_articles)} trouves, {len(prev_set)} existants)")

    if not new_articles:
        print("  Tous les articles sont deja connus")
        frontend_json = get_articles_json_for_frontend(data)
        _save_frontend_json(frontend_json)
        return frontend_json

    # 5. Categoriser avec Gemini (PAS inventer -- juste categoriser + impact)
    gemini_key = GEMINI_API_KEY or GEMINI_API_KEY_DEFAULT or os.environ.get("GEMINI_API_KEY", "")
    if gemini_key:
        print("\n  === CATEGORISATION GEMINI ===")
        try:
            from google import genai
            client = genai.Client(api_key=gemini_key)
            new_articles = _categorize_articles(client, new_articles)
        except Exception as e:
            print(f"  Categorisation echouee ({e})")
    else:
        print("  Pas de cle Gemini -- categories par defaut")

    # 6. Formater en HTML
    now = datetime.now()
    date_str = now.strftime("%d/%m/%Y %H:%M")
    news_html = _format_articles_html(new_articles, date_str)
    print(f"\n  HTML genere : {len(news_html)} chars, {len(new_articles)} articles")

    # 7. Traduire FR -> EN, HE
    print("\n  === TRADUCTIONS ===")
    html_en, html_he = "", ""
    if gemini_key:
        try:
            html_en, html_he = translate_all(news_html)
        except Exception as e:
            print(f"  Traduction echouee ({e}) -- EN/HE vides")

    # 8. Stocker les nouveaux articles
    added = add_articles(data, news_html, html_en, html_he)
    print(f"\n  {added} nouveaux articles ajoutes (total: {len(data['articles'])})")

    save_data(data)

    # 9. Exporter le JSON front-end
    frontend_json = get_articles_json_for_frontend(data)
    _save_frontend_json(frontend_json)

    print("=" * 60)
    print(f"  VEILLE TERMINEE -- {len(frontend_json['articles'])} articles (VRAIS)")
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
