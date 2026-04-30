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
PRODUITS HORS SAISON (EXCLURE — score = 0, ne PAS inclure) : {off_season_products}
ORIGINES MEHADRIN ET CONCURRENTES : Israel, Maroc, Egypte, Perou, Colombie, Bresil, Chili, Espagne, Turquie, Kenya, Afrique du Sud, Inde, Mexique, Grece, Cote d'Ivoire, Sicile.

POUR CHAQUE ARTICLE, tu dois :
1. DECIDER : pertinent pour Mehadrin oui/non (score 1-10)
2. Si pertinent (score >= 4) : REECRIRE en francais avec :
   - Un titre precis en francais
   - Le contenu REEL de l'article (NE PAS inventer de chiffres qui ne sont pas dans l'article original)
3. GARDER le lien original de l'article (OBLIGATOIRE, ne PAS le modifier)
4. ATTRIBUER une categorie : PRIX & VOLUMES, ALERTES SUPPLY, MOUVEMENTS ENSEIGNES, CONCURRENCE ORIGINES

REGLE FONDAMENTALE : un article est pertinent UNIQUEMENT s'il parle d'un PRODUIT MEHADRIN (liste ci-dessus) dans un MARCHE pertinent (Europe, import/export, origine concurrente). Si l'article parle d'un autre produit (myrtilles, fraises, fruits a noyau, bananes, kiwis, pommes, poires...) → EXCLURE.

CRITERES DE PERTINENCE :
- Score 10 : prix/cotation d'un produit Mehadrin EN SAISON avec chiffres
- Score 9 : volumes import/export d'un produit Mehadrin EN SAISON avec chiffres
- Score 8 : debut/fin campagne d'une origine concurrente sur un produit Mehadrin
- Score 7 : probleme qualite/supply chez un concurrent sur un produit Mehadrin (= argument commercial)
- Score 6 : mouvement d'enseigne ou acquisition dans le secteur AGRUMES/AVOCATS/MANGUES/EXOTIQUES
- Score 5 : info marche sur un produit Mehadrin dans un pays concurrent, meme sans chiffres precis
- Score 4 : info generale sur les agrumes, avocats ou mangues dans une origine concurrente
- Score < 4 : NON PERTINENT — ne pas inclure

IMPORTANT : en cas de doute, VERIFIER que l'article parle bien d'un PRODUIT MEHADRIN. Si le produit n'est pas dans la liste ci-dessus → EXCLURE, meme si c'est un fruit.

CRITERES D'EXCLUSION STRICTS (score = 0, NE PAS INCLURE) :
- PRODUITS NON-MEHADRIN : myrtilles, fraises, framboises, bananes, kiwis, pommes, poires, peches, nectarines, abricots, prunes, figues, litchis, fruits a noyau, baies. Si le produit n'est PAS dans la liste PRODUITS MEHADRIN → EXCLURE.
- PRODUITS HORS SAISON : tout article dont le sujet principal est un produit liste dans "PRODUITS HORS SAISON" ci-dessus.
- TOMATES et LEGUMES : tomates, concombres, poivrons, courgettes, haricots, oignons, pommes de terre (sauf patates douces), salades. Mehadrin ne vend QUE des FRUITS.
- Main d'oeuvre / social / absenteisme / greves / conditions de travail / salaires ouvriers.
- Phytosanitaire / recherche : mouche des fruits, thrips, insectes, ravageurs, HLB, verdissement, CRISPR, genomique, innovation varietale, recherche agronomique.
- Sante/nutrition, etudes scientifiques medicales.
- Logistique/fret/conteneurs/films plastiques/serres (SAUF si impact DIRECT sur disponibilite d'un fruit Mehadrin).
- Technologie/emballage/robots/packaging.
- B2C/recettes/promos supermarche.
- Marches sans impact sur l'Europe : Australie, Nouvelle-Zelande, USA/Canada (marche interieur), Asie du Sud-Est, Bangladesh, Inde/Chine (consommation locale). Seuls les marches des ORIGINES CONCURRENTES listees ci-dessus sont pertinents.
- Evenements / salons / promotions generiques sans info de marche concrete.

REGLES CRITIQUES :
- NE PAS inventer de chiffres. Si l'article dit "les prix ont augmente", ne PAS inventer "de 5%".
- TOUS les chiffres dans ta reponse doivent venir de l'article original.
- Le lien source est SACRE : copie-le exactement tel quel.
- Redige en francais. Maximum 10 articles. Mieux vaut 3 articles pertinents que 10 articles mediocres.
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
