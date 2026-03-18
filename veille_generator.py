#!/usr/bin/env python3
"""
veille_generator.py -- Generateur de veille marche (cron toutes les 2h)
========================================================================
Pipeline V3 -- VRAIS ARTICLES UNIQUEMENT :
  1. Scraper RSS multi-sources multi-langues (vrais articles, vrais liens)
  2. Filtrer par mots-cles produits Mehadrin
  3. Traduire articles non-FR vers FR (Gemini Flash)
  4. Validation pertinence Gemini (OUI/NON par article)
  5. Categoriser + impact tactique (Gemini Flash)
  6. Formater HTML FR, traduire FR -> EN, FR -> HE
  7. Stocker dans veille_data.json (accumulation 48h)
  8. Exporter veille_live.json pour le front-end

ZERO invention. Chaque article = vrai article avec vrai lien.
"""

import json
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
from veille_translate import translate_html

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


def _translate_foreign_articles_to_fr(client, articles):
    """Traduit les articles non-FR vers FR via Gemini Flash.

    Supporte EN, IT, ES, DE. Batch par groupes de 3 max pour eviter
    les reponses tronquees sur les prompts longs.
    """
    LANG_NAMES = {"en": "anglais", "it": "italien", "es": "espagnol", "de": "allemand"}
    BATCH_SIZE = 3

    # Group by source language
    by_lang = {}
    for art in articles:
        lang = art.get("source_lang", "en")
        if lang == "fr":
            continue
        by_lang.setdefault(lang, []).append(art)

    for lang, lang_articles in by_lang.items():
        lang_name = LANG_NAMES.get(lang, lang)
        print(f"  Traduction {lang_name} -> FR ({len(lang_articles)} articles)...")

        # Process in batches of BATCH_SIZE
        for batch_start in range(0, len(lang_articles), BATCH_SIZE):
            batch = lang_articles[batch_start:batch_start + BATCH_SIZE]

            if len(batch) == 1:
                # Single article: simple prompt
                art = batch[0]
                prompt = (
                    f"Traduis ce titre et ce resume d'article du {lang_name} en francais. "
                    f"Garde le meme sens, meme longueur. PAS de markdown.\n\n"
                    f"Titre: {art['title']}\n\n"
                    f"Contenu: {art.get('content', '')[:800]}\n\n"
                    f"Reponds STRICTEMENT en JSON:\n"
                    f'{{"title_fr": "...", "content_fr": "..."}}'
                )
                try:
                    resp = _gemini_call_with_retry(
                        client, max_retries=2, initial_wait=2,
                        model=GEMINI_MODEL_FLASH,
                        contents=[prompt],
                    )
                    raw = resp.text or ""
                    raw = re.sub(r'```\w*\s*', '', raw).strip().strip('`')
                    result = json.loads(raw)
                    art["title_fr"] = result.get("title_fr", art["title"])
                    art["content_fr"] = result.get("content_fr", art.get("content", ""))
                    print(f"    FR: {art['title_fr'][:55]}")
                except Exception as e:
                    print(f"    Echec trad '{art['title'][:40]}': {e}")
                    art["title_fr"] = art["title"]
                    art["content_fr"] = art.get("content", "")
            else:
                # Batch prompt for 2-3 articles
                articles_text = ""
                for i, art in enumerate(batch):
                    articles_text += f"\n--- Article {i} ---\n"
                    articles_text += f"Titre: {art['title']}\n"
                    articles_text += f"Contenu: {art.get('content', '')[:500]}\n"

                prompt = (
                    f"Traduis ces {len(batch)} articles du {lang_name} en francais. "
                    f"Garde le meme sens, meme longueur. PAS de markdown.\n\n"
                    f"{articles_text}\n\n"
                    f"Reponds STRICTEMENT en JSON (un objet par article):\n"
                    f'[{{"index": 0, "title_fr": "...", "content_fr": "..."}}, ...]'
                )
                try:
                    resp = _gemini_call_with_retry(
                        client, max_retries=2, initial_wait=2,
                        model=GEMINI_MODEL_FLASH,
                        contents=[prompt],
                    )
                    raw = resp.text or ""
                    raw = re.sub(r'```\w*\s*', '', raw).strip().strip('`')
                    results = json.loads(raw)
                    for entry in results:
                        idx = entry.get("index", -1)
                        if 0 <= idx < len(batch):
                            batch[idx]["title_fr"] = entry.get("title_fr", batch[idx]["title"])
                            batch[idx]["content_fr"] = entry.get("content_fr", batch[idx].get("content", ""))
                            print(f"    FR: {batch[idx]['title_fr'][:55]}")
                except Exception as e:
                    print(f"    Echec trad batch {lang_name}: {e}")
                    for art in batch:
                        art["title_fr"] = art.get("title_fr", art["title"])
                        art["content_fr"] = art.get("content_fr", art.get("content", ""))


def _validate_relevance_gemini(client, articles, max_keep=20):
    """Valide la pertinence des articles via Gemini Flash (OUI/NON par article).

    Envoie un batch de titres + descriptions courtes, Gemini repond OUI/NON.
    Garde max max_keep articles pertinents.
    Fallback: si Gemini echoue, garde tous les articles.
    """
    if not articles:
        return articles

    # Build prompt with truncated content
    articles_text = ""
    for i, art in enumerate(articles):
        title = art.get("title_fr", art["title"])
        content = art.get("content_fr", art.get("content", ""))[:300]
        articles_text += f"\n{i}. {title}\n   {content}\n"

    prompt = f"""Tu es le FILTRE FINAL de pertinence pour la veille commerciale de Mehadrin France.
Mehadrin = exportateur ISRAELIEN de fruits frais vers l'Europe (France + Italie principalement).

PRODUITS MEHADRIN (les SEULS qui comptent) :
Avocats Hass, mandarines Orri/Nadorcott/Clemengold, pamplemousses Star Ruby/Sweetie, mangues, dattes Medjoul, grenades, kumquat, melon, pasteque, cerises, raisin, patates douces.

ORIGINES CONCURRENTES : Israel, Maroc, Perou, Bresil, Colombie, Afrique du Sud, Espagne, Egypte, Cote d'Ivoire, Chili, Turquie.

Pour chaque article, reponds OUI ou NON. Sois TRES STRICT :

OUI uniquement si l'article donne une info qu'un COMMERCIAL Mehadrin peut utiliser EN RENDEZ-VOUS avec un acheteur GMS/grossiste :
- Prix/cotations d'un produit Mehadrin (pas un produit hors catalogue)
- Volumes import/export d'un produit Mehadrin
- Probleme de production/qualite sur une origine concurrente (gel, secheresse, mouche du fruit SUR un produit Mehadrin)
- Arrivee/fin de campagne d'une origine concurrente SUR un produit Mehadrin
- Mouvement d'une enseigne GMS sur un produit Mehadrin (appel d'offres, changement fournisseur)

NON si :
- L'article est GENERIQUE (politique agricole, accords commerciaux vagues, "le secteur agroalimentaire", macro-economie)
- Produits HORS catalogue (tomate, carotte, oignon, salade, pomme de terre, banane, pomme, poire, agrumes generiques)
- Sante/nutrition/etudes scientifiques (meme sur avocat ou mangue)
- Logistique/fret/conteneurs/ports/shipping
- Technologie/robots/emballage/packaging/conservation
- B2C/consommateur/recettes/promos rayon
- RSE/developpement durable SAUF impact direct sur prix ou approvisionnement

Reponds en JSON STRICT : [{{"index": 0, "relevant": true}}, ...]
PAS de markdown, PAS de commentaires.

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
        results = json.loads(raw)

        kept = []
        rejected = 0
        for entry in results:
            idx = entry.get("index", -1)
            if 0 <= idx < len(articles) and entry.get("relevant", False):
                kept.append(articles[idx])
            else:
                rejected += 1

        print(f"  Validation Gemini : {len(kept)} pertinents, {rejected} rejetes")
        return kept[:max_keep]

    except Exception as e:
        print(f"  Validation Gemini echouee ({e}) -- garde tous les articles")
        return articles[:max_keep]


def _format_articles_html(articles, date_str, lang="fr"):
    """Formate les articles reels en HTML pour le front-end.

    lang="fr" : utilise content_fr (traduit) ou content original si source FR
    lang="en" : utilise content original si source EN, ou content pour source FR

    Chaque article contient un VRAI lien vers l'article source.
    """
    html_parts = []
    for art in articles:
        cat = art.get("category", "PRIX & VOLUMES")
        title = art["title"]
        url = art["url"]
        impact = art.get("impact", "")
        source_name = art.get("source_name", "FreshPlaza")
        source_lang = art.get("source_lang", "fr")

        if lang == "fr":
            # Use translated FR content for EN articles, original for FR articles
            content = art.get("content_fr", art.get("content", ""))
            title = art.get("title_fr", title)
        elif lang == "en":
            # Use original EN content for EN articles, original for FR articles
            content = art.get("content", "")
        else:
            content = art.get("content", "")

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

    Pipeline V3 : vrais articles uniquement, multi-langue, validation Gemini.

    Args:
        force: Si True, ignore le rate limit (pour tests)

    Returns:
        dict: Donnees JSON pour le front-end, ou None si echec/rate-limited
    """
    print("=" * 60)
    print(f"  VEILLE MARCHE V3 -- {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print("  Mode : VRAIS ARTICLES (scraping RSS multi-sources)")
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

    # 3. Scraper les VRAIS articles depuis RSS (multi-sources)
    print("\n  === SCRAPING RSS ===")
    real_articles = scrape_real_articles()

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

    # 5. Init Gemini client
    gemini_key = GEMINI_API_KEY or GEMINI_API_KEY_DEFAULT or os.environ.get("GEMINI_API_KEY", "")
    client = None
    if gemini_key:
        try:
            from google import genai
            client = genai.Client(api_key=gemini_key)
        except Exception as e:
            print(f"  Init Gemini echouee ({e})")

    # 6. Traduire les articles non-FR vers FR (AVANT categorisation)
    foreign_articles = [a for a in new_articles if a.get("source_lang", "fr") != "fr"]
    if foreign_articles and client:
        print(f"\n  === TRADUCTION -> FR ({len(foreign_articles)} articles) ===")
        try:
            _translate_foreign_articles_to_fr(client, foreign_articles)
        except Exception as e:
            print(f"  Traduction -> FR echouee ({e})")

    # 7. Validation pertinence Gemini (OUI/NON, max 20 gardes)
    if client:
        print(f"\n  === VALIDATION PERTINENCE GEMINI ({len(new_articles)} candidats) ===")
        new_articles = _validate_relevance_gemini(client, new_articles, max_keep=20)

    if not new_articles:
        print("  Aucun article valide apres filtrage Gemini")
        frontend_json = get_articles_json_for_frontend(data)
        _save_frontend_json(frontend_json)
        return frontend_json

    # 8. Categoriser avec Gemini (PAS inventer -- juste categoriser + impact)
    if client:
        print("\n  === CATEGORISATION GEMINI ===")
        try:
            new_articles = _categorize_articles(client, new_articles)
        except Exception as e:
            print(f"  Categorisation echouee ({e})")
    else:
        print("  Pas de cle Gemini -- categories par defaut")

    # 9. Formater en HTML (FR et EN separement)
    now = datetime.now()
    date_str = now.strftime("%d/%m/%Y %H:%M")
    news_html_fr = _format_articles_html(new_articles, date_str, lang="fr")
    news_html_en = _format_articles_html(new_articles, date_str, lang="en")
    print(f"\n  HTML FR: {len(news_html_fr)} chars, EN: {len(news_html_en)} chars")

    # 10. Traduire FR -> HE
    print("\n  === TRADUCTION HE ===")
    html_he = ""
    if client:
        try:
            html_he = translate_html(news_html_fr, "he")
            print(f"  HE: {len(html_he)} chars")
        except Exception as e:
            print(f"  Traduction HE echouee ({e})")
    html_en = news_html_en

    # 11. Stocker les nouveaux articles
    added = add_articles(data, news_html_fr, html_en, html_he)
    print(f"\n  {added} nouveaux articles ajoutes (total: {len(data['articles'])})")

    save_data(data)

    # 12. Exporter le JSON front-end
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
