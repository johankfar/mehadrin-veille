#!/usr/bin/env python3
"""
veille_prompt.py -- Prompts pour la veille marche HYBRID (RSS + Gemini)
========================================================================
V4 HYBRID : Gemini ne GENERE plus d'articles. Il FILTRE et ENRICHIT
les articles reels trouves via RSS.
"""

# ─── Calendrier saisonnier Mehadrin ───

MEHADRIN_SEASON_CALENDAR = [
    ((1, 15),  ["ORRI / Or Mehadrin / Or Shoham (mandarines Israel)", "Avocats Hass Israel", "Star Ruby (pamplemousses Israel)", "Sweetie Israel"]),
    ((5, 20),  ["Avocats Hass Maroc", "Nadorcott Maroc + Israel", "Clemengold"]),
    ((10, 25), ["Mangues Israel (bateau)", "Patates douces Egypte"]),
    ((15, 30), ["Mangues avion Israel (Sheli, Maya, Aya)"]),
    ((20, 35), ["Melon Israel + Maroc", "Cerises Israel", "Raisin Israel", "Pasteque Israel + Maroc"]),
    ((1, 52),  ["Dattes Medjoul Israel (toute l'annee)"]),
    ((35, 52), ["Grenade Israel", "Kumquat Israel"]),
]

MEHADRIN_OFF_SEASON = [
    ((1, 14),  ["Melon", "Pasteque", "Cerises", "Raisin"]),
    ((15, 19), ["Cerises", "Raisin", "Pasteque"]),
    ((25, 52), ["Mangues Israel bateau"]),
    ((30, 52), ["Mangues avion"]),
    ((15, 34), ["Grenade", "Kumquat"]),
    ((6, 52),  ["Sweetie"]),
]


def get_seasonal_products(week_num):
    products = []
    for (start, end), items in MEHADRIN_SEASON_CALENDAR:
        if start <= week_num <= end:
            products.extend(items)
    return products


def get_off_season_products(week_num):
    products = []
    for (start, end), items in MEHADRIN_OFF_SEASON:
        if start <= week_num <= end:
            products.extend(items)
    return products


# ─── Prompt HYBRID : filtrer + enrichir des articles RSS reels ───

HYBRID_FILTER_PROMPT = """Tu es un ANALYSTE DE RENSEIGNEMENT COMMERCIAL pour Mehadrin France, exportateur israelien de fruits frais vers l'Europe.

Je te donne {article_count} articles REELS trouves dans la presse B2B fruits et legumes.
Tu dois SELECTIONNER les plus pertinents et les ENRICHIR avec un impact tactique.

Date : {date}. Semaine {week_num}.

PRODUITS EN SAISON : {seasonal_products}
PRODUITS HORS SAISON (DEPRIORITISER) : {off_season_products}

POUR CHAQUE ARTICLE, tu dois :
1. DECIDER : pertinent pour Mehadrin oui/non (score 1-10)
2. Si pertinent (score >= 5) : REECRIRE en francais avec :
   - Un titre precis en francais
   - Le contenu REEL de l'article (NE PAS inventer de chiffres qui ne sont pas dans l'article original)
   - Un "Impact tactique" : que fait le commercial Mehadrin avec cette info en rendez-vous
3. GARDER le lien original de l'article (OBLIGATOIRE, ne PAS le modifier)
4. ATTRIBUER une categorie : PRIX & VOLUMES, ALERTES SUPPLY, MOUVEMENTS ENSEIGNES, CONCURRENCE ORIGINES

CRITERES DE PERTINENCE :
- Score 10 : prix/cotation d'un produit Mehadrin avec chiffres
- Score 9 : volumes import/export d'un produit Mehadrin
- Score 8 : debut/fin campagne d'une origine concurrente sur un produit Mehadrin
- Score 7 : probleme qualite/supply sur un concurrent (= argument pour Mehadrin)
- Score 6 : mouvement d'enseigne sur un produit Mehadrin
- Score 5 : info marche generale mais utile pour contexte commercial
- Score < 5 : NON PERTINENT, ne pas inclure

CRITERES D'EXCLUSION (score = 0, ignorer completement) :
- Produit hors catalogue Mehadrin
- Phytosanitaire technique (mouche des fruits, thrips, insectes)
- Sante/nutrition, etudes scientifiques
- Logistique/fret/conteneurs
- Technologie/emballage/robots
- B2C/recettes/promos
- Produits hors saison SAUF evenement majeur

REGLES CRITIQUES :
- NE PAS inventer de chiffres. Si l'article dit "les prix ont augmente", ne PAS inventer "de 5%".
- TOUS les chiffres dans ta reponse doivent venir de l'article original.
- Le lien source est SACRE : copie-le exactement tel quel.
- Redige en francais. Maximum 8 articles. Minimum 3 si au moins 3 sont pertinents.
- PAS D'EMOJIS.

FORMAT DE SORTIE (HTML strict) :
Pour chaque article retenu, genere :
<div class="news-item">
  <div class="news-cat">CATEGORIE</div>
  <div class="news-title">Titre en francais -- <span class="news-date">{date}</span></div>
  <div class="news-body">Resume enrichi de l'article avec les chiffres REELS de l'article original. <strong>Impact tactique :</strong> Ce que le commercial fait avec cette info.</div>
  <div class="news-source"><a href="LIEN_ORIGINAL_EXACT" target="_blank">NomDuMedia -- Lire l'article</a></div>
</div>

Si AUCUN article n'est pertinent (tous score < 5), reponds uniquement : AUCUN_ARTICLE_PERTINENT

Voici les articles a analyser :

{articles_text}"""
