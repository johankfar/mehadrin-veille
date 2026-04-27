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
Tu dois SELECTIONNER les plus pertinents et les RESUMER factuellement.

Date : {date}. Semaine {week_num}.

PRODUITS MEHADRIN : avocats, mangues, mandarines (Orri, Or Shoham, Nadorcott), oranges, pomelos, pamplemousses (Star Ruby), clemengold, sweetie, dattes Medjoul, grenades, kumquats, patates douces, melons, pasteques, cerises, raisin de table.
PRODUITS EN SAISON : {seasonal_products}
PRODUITS HORS SAISON (DEPRIORITISER) : {off_season_products}
ORIGINES MEHADRIN ET CONCURRENTES : Israel, Maroc, Egypte, Perou, Colombie, Bresil, Chili, Espagne, Turquie, Kenya, Afrique du Sud, Inde, Mexique, Grece, Cote d'Ivoire, Sicile.

POUR CHAQUE ARTICLE, tu dois :
1. DECIDER : pertinent pour Mehadrin oui/non (score 1-10)
2. Si pertinent (score >= 3) : REECRIRE en francais avec :
   - Un titre precis en francais
   - Le contenu REEL de l'article (NE PAS inventer de chiffres qui ne sont pas dans l'article original)
3. GARDER le lien original de l'article (OBLIGATOIRE, ne PAS le modifier)
4. ATTRIBUER une categorie : PRIX & VOLUMES, ALERTES SUPPLY, MOUVEMENTS ENSEIGNES, CONCURRENCE ORIGINES

CRITERES DE PERTINENCE :
- Score 10 : prix/cotation d'un produit Mehadrin avec chiffres
- Score 9 : volumes import/export d'un produit Mehadrin avec chiffres
- Score 8 : debut/fin campagne d'une origine concurrente sur un produit Mehadrin
- Score 7 : probleme qualite/supply sur un concurrent (= argument commercial pour Mehadrin)
- Score 6 : mouvement d'enseigne ou acquisition dans le secteur fruits/agrumes
- Score 5 : info marche generale sur un fruit/agrume dans un pays concurrent, meme sans chiffres precis
- Score 4 : volumes/prix d'un produit voisin (ex: raisin de table, agrumes generique) dans un pays listee ci-dessus
- Score 3 : info sur un fruit/legume dans une origine concurrente — utile pour contexte meme si pas directement Mehadrin
- Score < 3 : NON PERTINENT

REGLE ABSOLUE : INCLURE LARGEMENT. Les commerciaux veulent TROP d'articles plutot que pas assez.
- Si un article mentionne un fruit ET un pays concurrent → INCLURE (score >= 4)
- Si un article parle de prix ou volumes de fruits en Europe → INCLURE (score >= 4)
- Si un article parle d'avocats, mangues, agrumes, pasteques, melons, raisin, grenades, dattes → INCLURE (score >= 5)
- En cas de doute → INCLURE. Ne rejeter que ce qui est CLAIREMENT hors sujet.
- Tu DOIS retourner AU MINIMUM 3 articles si tu en recois 7 ou plus. 0 article = INACCEPTABLE sauf si tous les articles parlent de technologie/robots/logistique.

CRITERES D'EXCLUSION STRICTS (score = 0, ignorer UNIQUEMENT si c'est 100% hors sujet) :
- Phytosanitaire technique (mouche des fruits, thrips, insectes, CRISPR, genomique)
- Sante/nutrition, etudes scientifiques medicales
- Logistique/fret/conteneurs (SAUF si impact sur disponibilite d'un fruit Mehadrin)
- Technologie/emballage/robots/packaging
- B2C/recettes/promos supermarche
- Produits JAMAIS vendus par un importateur de fruits frais (viande, lait, cereales, noix, pistaches)
- Marches UNIQUEMENT locaux sans impact Europe (ex: raisin en Inde pour consommation locale)

REGLES CRITIQUES :
- NE PAS inventer de chiffres. Si l'article dit "les prix ont augmente", ne PAS inventer "de 5%".
- TOUS les chiffres dans ta reponse doivent venir de l'article original.
- Le lien source est SACRE : copie-le exactement tel quel.
- Redige en francais. Maximum 12 articles. Minimum 3 si au moins 7 articles sont fournis.
- PAS D'EMOJIS.
- ANTI-DOUBLON STRICT : Si plusieurs articles parlent du MEME SUJET (meme produit + meme pays/origine), ne garder que le MEILLEUR (celui avec le plus de chiffres/details). ATTENTION : les articles proviennent de FreshPlaza en 5+ langues (EN, FR, ES, IT, DE) — le meme article est souvent present plusieurs fois avec des titres traduits. Ne garder qu'UNE seule version par sujet.

FORMAT DE SORTIE (HTML strict) :
Pour chaque article retenu, genere :
<div class="news-item">
  <div class="news-cat">CATEGORIE</div>
  <div class="news-title">Titre en francais -- <span class="news-date">{date}</span></div>
  <div class="news-body">Resume factuel de l'article avec les chiffres REELS de l'article original.</div>
  <div class="news-source"><a href="LIEN_ORIGINAL_EXACT" target="_blank">NomDuMedia -- Lire l'article</a></div>
</div>

Si AUCUN article n'est pertinent (tous score < 3), reponds uniquement : AUCUN_ARTICLE_PERTINENT

Voici les articles a analyser :

{articles_text}"""
