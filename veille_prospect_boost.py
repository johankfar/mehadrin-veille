#!/usr/bin/env python3
"""
veille_prospect_boost.py — Cross-reference veille articles avec prospects Mehadrin
pour auto-booster les scores prospects en fonction des signaux marche.

Utilisation:
    python veille_prospect_boost.py --veille veille_live.json --prospects prospects_enriched.json --output veille_prospect_signals.json
"""

import argparse
import json
import sys
import os
from datetime import datetime, timedelta, timezone

# --- Mappings pays (code ISO -> mots-cles dans les articles) ---
COUNTRY_MAP = {
    "FR": ["france", "français", "francais", "rungis", "paris"],
    "IT": ["italie", "italien", "italy", "italian", "roma", "sicile", "sicily", "calabr"],
    "ES": ["espagne", "espagnol", "spain", "spanish", "almeria", "murcia", "valencia", "huelva"],
    "PT": ["portugal", "portugais", "portuguese", "algarve", "lisbonne"],
}

# --- Mappings produits (cle normalisee -> mots-cles dans les articles) ---
PRODUCT_MAP = {
    "avocat": ["avocat", "avocado", "aguacate", "hass"],
    "mangue": ["mangue", "mango", "mangos"],
    "mandarine": ["mandarine", "mandarin", "orri", "clementine", "nadorcott"],
    "orange": ["orange", "naranja", "arancia", "blood orange", "sanguine", "tarocco"],
    "grenade": ["grenade", "pomegranate", "melagrana"],
    "datte": ["datte", "medjoul", "medjool", "datteri"],
    "melon": ["melon", "melone"],
    "pasteque": ["pasteque", "watermelon", "anguria", "sandia"],
    "raisin": ["raisin", "grape", "uva"],
    "cerise": ["cerise", "cherry", "cereza", "ciliegia"],
}

# --- Mots-cles de sentiment ---
POSITIVE_KEYWORDS = [
    "hausse", "croissance", "demande forte", "record", "opportunité", "opportunite",
    "augmentation", "progression", "boom", "expansion", "excellente", "forte demande",
    "prix élevé", "prix eleve", "bénéfice", "benefice", "reprise", "succès", "succes",
    "export record", "volumes en hausse", "marché porteur", "marche porteur",
]
NEGATIVE_KEYWORDS = [
    "baisse", "pénurie", "penurie", "shortage", "problème", "probleme", "crise",
    "chute", "déclin", "declin", "effondrement", "surproduction", "gel", "grêle",
    "grele", "sécheresse", "secheresse", "embargo", "fermeture", "faillite",
    "prix bas", "mévente", "mevente", "invendus",
]

# --- Boost maximum par prospect ---
MAX_BOOST = 15
MAX_SCORE = 100

# --- Points par type de match ---
BOOST_COUNTRY_AND_PRODUCT = 5
BOOST_COUNTRY_ONLY = 2
BOOST_PRODUCT_ONLY = 3


def normalize(text):
    """Normalise le texte pour la recherche (minuscules, pas d'accents critiques)."""
    if not text:
        return ""
    return text.lower()


def extract_countries(text):
    """Extrait les pays mentionnes dans un texte d'article."""
    text_lower = normalize(text)
    found = set()
    for code, keywords in COUNTRY_MAP.items():
        for kw in keywords:
            if kw in text_lower:
                found.add(code)
                break
    return found


def extract_products(text):
    """Extrait les produits mentionnes dans un texte d'article."""
    text_lower = normalize(text)
    found = set()
    for product_key, keywords in PRODUCT_MAP.items():
        for kw in keywords:
            if kw in text_lower:
                found.add(product_key)
                break
    return found


def detect_sentiment(text):
    """Detecte le sentiment d'un article (positive/negative/neutral)."""
    text_lower = normalize(text)
    pos_count = sum(1 for kw in POSITIVE_KEYWORDS if kw in text_lower)
    neg_count = sum(1 for kw in NEGATIVE_KEYWORDS if kw in text_lower)
    if pos_count > neg_count:
        return "positive"
    elif neg_count > pos_count:
        return "negative"
    return "neutral"


def normalize_prospect_products(produits_probables):
    """Normalise les produits d'un prospect pour matcher avec PRODUCT_MAP."""
    if not produits_probables:
        return set()
    normalized = set()
    for p in produits_probables:
        p_lower = normalize(p)
        for product_key, keywords in PRODUCT_MAP.items():
            for kw in keywords:
                if kw in p_lower or p_lower in kw:
                    normalized.add(product_key)
                    break
    return normalized


def parse_article_date(timestamp_str):
    """Parse la date d'un article veille (supporte plusieurs formats)."""
    if not timestamp_str:
        return None
    # Formats courants dans veille_live.json
    for fmt in [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
    ]:
        try:
            dt = datetime.strptime(timestamp_str.strip(), fmt)
            # Si pas de timezone, on assume UTC
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def is_recent(article_date, hours=72):
    """Verifie si un article date de moins de N heures."""
    if article_date is None:
        # Si pas de date, on l'inclut quand meme (mieux vaut faux positif que rater un signal)
        return True
    now = datetime.now(timezone.utc)
    return (now - article_date) <= timedelta(hours=hours)


def get_article_text(article):
    """Concatene tous les champs texte d'un article pour l'analyse."""
    parts = []
    for field in ["title", "content_fr", "content", "summary", "description", "category"]:
        val = article.get(field, "")
        if val:
            parts.append(str(val))
    return " ".join(parts)


def process(veille_path, prospects_path, output_path, hours=72):
    """Traitement principal : croise articles veille x prospects."""

    # --- Chargement veille ---
    if not os.path.isfile(veille_path):
        print(f"ERREUR: Fichier veille introuvable: {veille_path}", file=sys.stderr)
        sys.exit(1)
    with open(veille_path, "r", encoding="utf-8") as f:
        veille_data = json.load(f)

    # veille_live.json peut etre une liste ou un dict avec cle "articles"
    if isinstance(veille_data, dict):
        articles = veille_data.get("articles", veille_data.get("items", []))
        if not isinstance(articles, list):
            articles = list(veille_data.values()) if veille_data else []
    elif isinstance(veille_data, list):
        articles = veille_data
    else:
        print("ERREUR: Format veille_live.json inattendu", file=sys.stderr)
        sys.exit(1)

    # --- Chargement prospects ---
    if not os.path.isfile(prospects_path):
        print(f"ERREUR: Fichier prospects introuvable: {prospects_path}", file=sys.stderr)
        sys.exit(1)
    with open(prospects_path, "r", encoding="utf-8") as f:
        prospects = json.load(f)

    if not isinstance(prospects, dict):
        print("ERREUR: prospects_enriched.json doit etre un dict {code: prospect}", file=sys.stderr)
        sys.exit(1)

    # --- Filtre articles recents et analyse ---
    analyzed_articles = []
    for art in articles:
        art_date = parse_article_date(art.get("timestamp", art.get("date", "")))
        if not is_recent(art_date, hours):
            continue

        text = get_article_text(art)
        countries = extract_countries(text)
        products = extract_products(text)
        sentiment = detect_sentiment(text)

        if countries or products:
            analyzed_articles.append({
                "article": art,
                "countries": countries,
                "products": products,
                "sentiment": sentiment,
                "date": art_date,
            })

    print(f"Articles veille charges: {len(articles)}")
    print(f"Articles recents (<{hours}h) avec signal: {len(analyzed_articles)}")
    print(f"Prospects charges: {len(prospects)}")
    print()

    # --- Cross-reference prospects x articles ---
    boosted = {}

    for code, prospect in prospects.items():
        prospect_country = (prospect.get("pays") or "").upper().strip()
        prospect_products = normalize_prospect_products(prospect.get("produits_probables", []))
        current_score = prospect.get("score") or 0

        if not isinstance(current_score, (int, float)):
            try:
                current_score = int(current_score)
            except (ValueError, TypeError):
                current_score = 0

        signals = []
        total_boost = 0

        for aa in analyzed_articles:
            country_match = prospect_country in aa["countries"]
            product_match = bool(prospect_products & aa["products"])

            if not country_match and not product_match:
                continue

            # Calcul du boost selon le type de match
            if country_match and product_match:
                match_type = "pays+produit"
                boost = BOOST_COUNTRY_AND_PRODUCT
            elif country_match:
                match_type = "pays"
                boost = BOOST_COUNTRY_ONLY
            else:
                match_type = "produit"
                boost = BOOST_PRODUCT_ONLY

            # Date formatee pour l'output
            if aa["date"]:
                date_str = aa["date"].strftime("%d/%m/%Y")
            else:
                date_str = "N/A"

            signals.append({
                "article_title": aa["article"].get("title", "Sans titre"),
                "match_type": match_type,
                "sentiment": aa["sentiment"],
                "matched_countries": sorted(aa["countries"] & {prospect_country}) if country_match else [],
                "matched_products": sorted(prospect_products & aa["products"]) if product_match else [],
                "date": date_str,
                "boost": boost,
            })

            total_boost += boost

        if not signals:
            continue

        # Cap le boost a MAX_BOOST
        total_boost = min(total_boost, MAX_BOOST)
        new_score = min(current_score + total_boost, MAX_SCORE)

        # Ne garder que les prospects dont le score change effectivement
        if new_score == current_score:
            continue

        boosted[code] = {
            "prospect_code": code,
            "prospect_name": prospect.get("nom", "N/A"),
            "pays": prospect_country,
            "commercial": prospect.get("commercial", "N/A"),
            "current_score": current_score,
            "boost": total_boost,
            "signals": signals,
            "new_score": new_score,
        }

    # --- Ecriture du fichier output ---
    output_data = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "articles_analyzed": len(analyzed_articles),
        "prospects_total": len(prospects),
        "prospects_boosted": len(boosted),
        "results": boosted,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print(f"Fichier genere: {output_path}")
    print()

    # --- Affichage resume ---
    print_summary(boosted)

    return boosted


def print_summary(boosted):
    """Affiche un tableau resume des prospects boostes."""
    if not boosted:
        print("Aucun prospect booste (pas de signal matching dans les articles recents).")
        return

    # Tri par boost decroissant
    sorted_results = sorted(boosted.values(), key=lambda x: x["boost"], reverse=True)

    # Largeurs colonnes
    col_code = 8
    col_nom = 30
    col_pays = 5
    col_comm = 12
    col_score = 7
    col_boost = 7
    col_new = 7
    col_signals = 10

    sep = "+" + "-" * col_code + "+" + "-" * col_nom + "+" + "-" * col_pays + "+" + "-" * col_comm + "+" + "-" * col_score + "+" + "-" * col_boost + "+" + "-" * col_new + "+" + "-" * col_signals + "+"

    print(f"=== PROSPECTS BOOSTES PAR LA VEILLE ({len(sorted_results)} prospects) ===")
    print()
    print(sep)
    print(
        f"|{'Code':^{col_code}}"
        f"|{'Nom':^{col_nom}}"
        f"|{'Pays':^{col_pays}}"
        f"|{'Commercial':^{col_comm}}"
        f"|{'Score':^{col_score}}"
        f"|{'Boost':^{col_boost}}"
        f"|{'Nouv.':^{col_new}}"
        f"|{'Signaux':^{col_signals}}|"
    )
    print(sep)

    for r in sorted_results:
        nom = r["prospect_name"][:col_nom - 2]
        comm = (r["commercial"] or "N/A")[:col_comm - 2]
        print(
            f"| {r['prospect_code']:<{col_code - 1}}"
            f"| {nom:<{col_nom - 1}}"
            f"| {r['pays']:^{col_pays - 1}}"
            f"| {comm:<{col_comm - 1}}"
            f"| {r['current_score']:>{col_score - 2}} "
            f"| +{r['boost']:<{col_boost - 2}}"
            f"| {r['new_score']:>{col_new - 2}} "
            f"| {len(r['signals']):>{col_signals - 2}} |"
        )

    print(sep)
    print()

    # Stats par commercial
    by_commercial = {}
    for r in sorted_results:
        comm = r.get("commercial", "N/A") or "N/A"
        by_commercial.setdefault(comm, []).append(r)

    print("--- Repartition par commercial ---")
    for comm, items in sorted(by_commercial.items()):
        avg_boost = sum(i["boost"] for i in items) / len(items)
        print(f"  {comm}: {len(items)} prospects boostes (boost moyen: +{avg_boost:.1f})")

    # Stats par pays
    by_country = {}
    for r in sorted_results:
        pays = r.get("pays", "N/A") or "N/A"
        by_country.setdefault(pays, []).append(r)

    print()
    print("--- Repartition par pays ---")
    for pays, items in sorted(by_country.items()):
        print(f"  {pays}: {len(items)} prospects boostes")

    print()


def main():
    parser = argparse.ArgumentParser(
        description="Cross-reference veille articles avec prospects Mehadrin pour auto-boost des scores"
    )
    parser.add_argument(
        "--veille",
        default="veille_live.json",
        help="Chemin vers le fichier veille JSON (default: veille_live.json)",
    )
    parser.add_argument(
        "--prospects",
        default="prospects_enriched.json",
        help="Chemin vers le fichier prospects JSON (default: prospects_enriched.json)",
    )
    parser.add_argument(
        "--output",
        default="veille_prospect_signals.json",
        help="Chemin vers le fichier output (default: veille_prospect_signals.json)",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=72,
        help="Fenetre temporelle en heures pour les articles recents (default: 72)",
    )

    args = parser.parse_args()
    process(args.veille, args.prospects, args.output, args.hours)


if __name__ == "__main__":
    main()
