#!/usr/bin/env python3
"""
veille_sources.py -- Registre des sources de la veille Mehadrin + filtre + equilibre
====================================================================================
- SOURCES : chaque flux est teste (HTTP 200, XML parsable, date du dernier article)
  avant d'etre ajoute. `filtre` = "specialise" (presse fruits & legumes : un mot-cle
  Mehadrin suffit, comme avant), "specialise_produit" (idem MAIS un mot PRODUIT est
  obligatoire) ou "generaliste" (presse eco/agricole/pays : exige produit ET
  (origine OU marche), sinon l'article est ecarte).
- `groupe` sert a l'equilibre : tout le groupe "freshplaza" (editions FreshPlaza +
  AGF.nl, memes identifiants d'article) est plafonne a une part des candidats.
- Aucune dependance reseau ici : fonctions pures, testees dans test_veille_sources.py.
"""

import html as html_mod
import re
import unicodedata

GROUPE_FRESHPLAZA = "freshplaza"

# Nombre de candidats envoyes a Gemini par cycle (decision du 24/10/2026 : 60, aucune coupe).
NB_CANDIDATS = 60


def _s(id, name, country, kind, lang, url, filtre="specialise", groupe=None, format="rss", **extra):
    """`extra` : options propres au format (html={...} pour format="html", ignorer_sans_extrait=True pour wpjson)."""
    d = {"id": id, "name": name, "country": country, "kind": kind, "lang": lang,
         "url": url, "filtre": filtre, "groupe": groupe or id, "format": format}
    d.update(extra)
    return d


_GOBPE_HTML = {
    # une carte = <h3><a href="/institucion/<org>/noticias/<id>-<slug>">titre</a></h3> ... <div class="z-10 relative">resume</div> ... <time datetime="YYYY-MM-DD HH:MM:SS.000">
    "item": r'<h3[^>]*>\s*<a[^>]*href="(?P<href>/institucion/[a-z]+/noticias/\d+[^"]*)"[^>]*>(?P<title>.*?)</a>\s*</h3>'
            r'.*?<div class="z-10 relative">(?P<resume>.*?)</div>.*?<time[^>]*datetime="(?P<date>[^"]+)"',
    "date_format": "%Y-%m-%d %H:%M:%S.%f",
    "tz_offset_hours": -5,  # heure de Lima (UTC-5), verifiee : datetime 15:54 = « 3:54 p. m. » affiche
}

# --- Sources deja en production (conservees telles quelles) -------------------------
SOURCES_EXISTANTES = [
    _s("fp_com", "FreshPlaza", "INT", "presse pro", "en", "https://www.freshplaza.com/rss.xml", groupe=GROUPE_FRESHPLAZA),
    _s("fp_fr", "FreshPlaza FR", "INT", "presse pro", "fr", "https://www.freshplaza.fr/rss.xml", groupe=GROUPE_FRESHPLAZA),
    _s("fp_it", "FreshPlaza IT", "INT", "presse pro", "it", "https://www.freshplaza.it/rss.xml", groupe=GROUPE_FRESHPLAZA),
    _s("fp_es", "FreshPlaza ES", "INT", "presse pro", "es", "https://www.freshplaza.es/rss.xml", groupe=GROUPE_FRESHPLAZA),
    _s("fp_de", "FreshPlaza DE", "INT", "presse pro", "de", "https://www.freshplaza.de/rss.xml", groupe=GROUPE_FRESHPLAZA),
    _s("agf_nl", "AGF.nl", "NL", "presse pro", "nl", "https://www.agf.nl/rss.xml", groupe=GROUPE_FRESHPLAZA),
    _s("eastfruit", "EastFruit", "Europe de l'Est", "presse pro", "en", "https://east-fruit.com/en/feed/"),
    _s("hortidaily", "HortiDaily", "NL", "presse pro", "en", "https://www.hortidaily.com/rss.xml"),
    _s("reussir_fl", "Reussir F&L", "FR", "presse pro", "fr", "https://www.reussir.fr/fruits-legumes/rss.xml"),
    _s("hortoinfo", "Hortoinfo", "ES", "presse pro", "es", "https://hortoinfo.es/feed/"),
    _s("citrus_industry", "Citrus Industry", "US", "presse pro", "en", "https://citrusindustry.net/feed/"),
    _s("fp_asia", "FreshPlaza Asia", "INT", "presse pro", "en", "https://www.freshplaza.com/asia/rss.xml/", groupe=GROUPE_FRESHPLAZA),
    _s("fp_na", "FreshPlaza NA", "INT", "presse pro", "en", "https://www.freshplaza.com/north-america/rss.xml/", groupe=GROUPE_FRESHPLAZA),
    _s("fp_latam", "FreshPlaza LatAm", "INT", "presse pro", "en", "https://www.freshplaza.com/latin-america/rss.xml/", groupe=GROUPE_FRESHPLAZA),
    _s("fp_africa", "FreshPlaza Africa", "INT", "presse pro", "en", "https://www.freshplaza.com/africa/rss.xml/", groupe=GROUPE_FRESHPLAZA),
    _s("fp_europe", "FreshPlaza Europe", "INT", "presse pro", "en", "https://www.freshplaza.com/europe/rss.xml/", groupe=GROUPE_FRESHPLAZA),
    _s("producereport", "Produce Report", "US", "presse pro", "en", "https://www.producereport.com/rss.xml"),
    _s("freshfel", "Freshfel Europe", "EU", "interprofession", "en", "https://freshfel.org/feed/"),
    _s("valenciafruits", "ValenciaFruits", "ES", "presse pro", "es", "https://valenciafruits.com/feed/"),
    _s("fp_cn", "FreshPlaza CN", "INT", "presse pro", "zh", "https://www.freshplaza.cn/rss.xml", groupe=GROUPE_FRESHPLAZA),
]

# --- Sources retirees pour robots.txt (Disallow /feed/) : NON lues par le pipeline ---
SOURCES_RETIREES_ROBOTS = [
    dict(_s("fresh_fruit_portal", "Fresh Fruit Portal", "US/LATAM", "presse pro", "en", "https://www.freshfruitportal.com/feed/"), motif='robots.txt : Disallow /feed/'),
    dict(_s("agrimaroc", "AgriMaroc", "MA", "presse pro", "fr", "https://www.agrimaroc.ma/feed/"), motif='robots.txt : Disallow /feed/'),
    dict(_s('grocer_uk', 'The Grocer (Royaume-Uni)', 'UK', 'presse distribution', 'en', 'https://www.thegrocer.co.uk/34272.rss', filtre="generaliste"), motif='robots.txt : Disallow /*.rss'),
]

# --- Sources ajoutees le 24/10/2026 (chacune testee : HTTP 200, flux parsable, date du dernier article) ---
SOURCES_AJOUTEES = [
    _s('revista_mercados', 'Revista Mercados', 'ES', 'presse pro', 'es', 'https://revistamercados.com/feed/', filtre="specialise_produit"),
    _s('vegetable_fr', 'Vegetable.fr', 'FR', 'presse pro', 'fr', 'https://www.vegetable.fr/feed/', filtre="specialise_produit"),
    _s('fyh', 'Frutas y Hortalizas', 'ES', 'presse pro', 'es', 'https://fyh.es/feed/', filtre="specialise_produit"),
    _s('perishable_news', 'Perishable News', 'US', 'presse pro', 'en', 'https://perishablenews.com/feed/', filtre="specialise_produit"),
    _s('cga_za', 'Citrus Growers Association (Afrique du Sud)', 'ZA', 'interprofession', 'en', 'https://www.cga.co.za/feed/', filtre="specialise_produit"),
    _s('hortgro_za', 'Hortgro (Afrique du Sud)', 'ZA', 'interprofession', 'en', 'https://www.hortgro.co.za/feed/', filtre="generaliste"),
    _s('farmers_weekly_za', 'Farmers Weekly (Afrique du Sud)', 'ZA', 'presse agricole', 'en', 'https://www.farmersweekly.co.za/feed/', filtre="generaliste"),
    _s('agroinformacion', 'Agroinformacion', 'ES', 'presse agricole', 'es', 'https://agroinformacion.com/feed/', filtre="generaliste"),
    _s('agronoticias', 'Agronoticias', 'ES', 'presse agricole', 'es', 'https://agronoticias.es/feed/', filtre="generaliste"),
    _s('leseco_ma', 'Les Eco (Maroc)', 'MA', 'presse eco', 'fr', 'https://leseco.ma/feed', filtre="generaliste"),
    _s('globes_il', 'Globes (Israel)', 'IL', 'presse eco', 'en', 'https://en.globes.co.il/WebService/Rss/RssFeeder.asmx/FeederNode?iID=942', filtre="generaliste"),
    _s('business_daily_ke', 'Business Daily (Kenya)', 'KE', 'presse eco', 'en', 'https://www.businessdailyafrica.com/bd/rss.xml', filtre="generaliste"),
    _s('agronegocios_co', 'Agronegocios (Colombie)', 'CO', 'presse agricole', 'es', 'https://www.agronegocios.co/rss/agricultura', filtre="generaliste"),
    _s('terre_net', 'Terre-net', 'FR', 'presse agricole', 'fr', 'https://www.terre-net.fr/rss', filtre="generaliste"),
    _s('lafranceagricole', 'La France Agricole', 'FR', 'presse agricole', 'fr', 'https://www.lafranceagricole.fr/rss', filtre="generaliste"),
    _s('agrarheute', 'agrarheute', 'DE', 'presse agricole', 'de', 'https://www.agrarheute.com/rss.xml', filtre="generaliste"),
    _s('fepex', 'Fepex - Noticias', 'ES', 'interprofession', 'es', 'https://www.fepex.es/Feed.axd?contenido=noticia&sitio=1&idioma=98', filtre="generaliste"),
    _s('standard_ke', 'The Standard Kenya - Agriculture', 'KE', 'presse agricole', 'en', 'https://www.standardmedia.co.ke/rss/agriculture.php', filtre="generaliste"),
    _s('agroberichten_nl', 'Agroberichten Buitenland (ministere NL)', 'NL', 'institution', 'nl', 'https://www.agroberichtenbuitenland.nl/api/rss?query=%7B%22filters%22%3A%5B%7B%22field%22%3A%22content_type%22%2C%22values%22%3A%5B%22pro%3AnewsDocument%22%5D%2C%22type%22%3A%22all%22%7D%5D%2C%22resultSearchTerm%22%3A%22%22%2C%22pageTitle%22%3A%22Nieuws%22%7D', filtre="generaliste"),
    _s('lid_ch', 'LID.ch (information agricole suisse)', 'CH', 'presse agricole', 'de', 'https://lid.ch/rss.xml', filtre="generaliste"),
    _s('fruchthandel', 'Fruchthandel', 'DE', 'presse pro', 'de', 'https://www.fruchthandel.de/rss.xml', filtre="specialise_produit"),
    _s('red_agricola', 'Red Agricola', 'PE', 'presse pro', 'es', 'https://redagricola.com/feed/', filtre="specialise_produit"),
    _s('agrodata_peru', 'Agrodata Peru (stats d\'export mensuelles)', 'PE', 'donnees', 'es', 'https://agrodataperu.com/feed', filtre="specialise_produit"),
    _s('fruitbook_it', 'Fruitbook Magazine', 'IT', 'presse pro', 'it', 'https://www.fruitbookmagazine.it/feed/', filtre="specialise_produit"),
    _s('foodformzansi', 'Food For Mzansi', 'ZA', 'presse agricole', 'en', 'https://www.foodformzansi.co.za/feed/', filtre="generaliste"),
    _s('agropopular', 'Agropopular', 'ES', 'presse agricole', 'es', 'https://www.agropopular.com/feed/', filtre="generaliste"),
    _s('asaja', 'ASAJA', 'ES', 'syndicat agricole', 'es', 'https://www.asaja.com/feed/', filtre="generaliste"),
    _s('fresh_fruit_portal_wp', 'Fresh Fruit Portal', 'US/LATAM', 'presse pro', 'en', 'https://www.freshfruitportal.com/wp-json/wp/v2/posts?per_page=100&_fields=id,date,link,title,excerpt', format="wpjson"),
    _s('agrimaroc_wp', 'AgriMaroc', 'MA', 'presse pro', 'fr', 'https://www.agrimaroc.ma/wp-json/wp/v2/posts?per_page=100&_fields=id,date,link,title,excerpt', format="wpjson", ignorer_sans_extrait=True),
    _s('freshpoint_it', 'FreshPoint Magazine', 'IT', 'presse pro', 'it', 'https://www.freshpointmagazine.it/feed/', filtre="specialise_produit"),
    _s('fruit_journal_it', 'Fruit Journal', 'IT', 'presse pro', 'it', 'https://www.fruitjournal.com/feed/', filtre="specialise_produit"),
    _s('senasa_pe', 'SENASA (Perou)', 'PE', 'officiel', 'es', 'https://www.gob.pe/institucion/senasa/noticias', filtre="specialise_produit", format="html", html=_GOBPE_HTML),
    _s('midagri_pe', 'MIDAGRI (Perou)', 'PE', 'officiel', 'es', 'https://www.gob.pe/institucion/midagri/noticias', filtre="specialise_produit", format="html", html=_GOBPE_HTML),
    _s('interfel', 'Interfel', 'FR', 'interprofession', 'fr', 'https://www.interfel.com/wp-json/wp/v2/posts?per_page=10', filtre="specialise_produit", format="wpjson"),
    _s('agrodigital', 'Agrodigital', 'ES', 'presse agricole', 'es', 'https://www.agrodigital.com/wp-json/wp/v2/posts?per_page=20', filtre="generaliste", format="wpjson"),
]

SOURCES = SOURCES_EXISTANTES + SOURCES_AJOUTEES


# --- Normalisation et recherche de termes -----------------------------------------------

def normaliser(texte):
    """Minuscules, sans accents, apostrophes simples."""
    t = unicodedata.normalize("NFKD", str(texte or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", t.lower().replace("’", "'")).strip()


def _motif(termes):
    """Debut de mot obligatoire (pas de 'hass' dans 'chasse') ; fin de mot exigee pour les termes <= 4 lettres."""
    morceaux = []
    for t in sorted({normaliser(x) for x in termes}, key=len, reverse=True):
        fin = r"(?![a-z0-9])" if len(t) <= 4 else ""
        if t == "mango":
            fin = r"(?!ld)"  # Mangold (bette) n'est pas une mangue
        morceaux.append(re.escape(t) + fin)
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(morceaux) + ")")


# Produits Mehadrin : avocat, agrumes, dattes, patate douce, mangue, exotiques.
PRODUITS = [
    "avocat", "avocado", "aguacate", "palta", "abacate", "hass", "ettinger",
    "orange", "naranja", "arancia", "arance", "navel", "tarocco", "blood orange",
    "mandarine", "mandarin", "mandarina", "mandarino", "clementine", "clementina", "clemenvilla", "nadorcott",
    "clemengold", "orri", "easy peeler", "easy-peeler", "tangerine", "satsuma",
    "pomelo", "pamplemousse", "grapefruit", "pompelmo", "star ruby", "sweetie",
    "agrume", "agrumi", "citrus", "citrico", "zitrus",
    "medjool", "medjoul", "datte", "datteri", "date palm", "date fruit", "date growers", "dattel",
    "patate douce", "sweet potato", "batata", "susskartoffel",
    "mangue", "mango", "mangos", "mangoes",
    "orangen", "sinaasappel", "sinaasappelen", "mandarinen", "mandarijn", "mandarijnen", "zitrusfruchte",
    "dadel", "dadels", "sukartoffel", "zoete aardappel", "granatapfel", "granaatappel", "avocados",
    "papaye", "papaya", "litchi", "lychee", "passion fruit", "fruit de la passion", "maracuja",
    "pitaya", "pitahaya", "dragon fruit", "kumquat", "grenade fruit", "pomegranate", "melagrana",
    "granada fruit", "physalis", "tropical fruit", "fruits exotiques", "exotic fruit",
]

ORIGINES = [
    "israel", "israeli", "israelien", "espagne", "spain", "spanish", "espana", "espagnol", "spagna",
    "maroc", "morocco", "moroccan", "marocain", "marocco", "marruecos",
    "perou", "peru", "peruvian", "peruano", "peruan", "marroqui", "sudafrican", "afrique du sud", "south africa", "south african",
    "sudafrica", "kenya", "kenyan", "colombie", "colombia", "colombian", "egypte", "egypt", "egyptian",
    "chili", "chile", "chilean", "bresil", "brazil", "brasil", "turquie", "turkey", "turkiye",
    "mexique", "mexico", "republique dominicaine", "dominican republic", "cote d'ivoire", "ivory coast",
    "spanien", "marokko", "sudafrika", "kolumbien", "kenia", "agypten", "turkei", "spanje", "zuid-afrika",
    "zuid afrika", "colombia", "marokko", "senegal", "tunisie", "tunisia", "jordanie", "jordan", "inde", "india",
]

MARCHES = [
    "france", "french", "francais", "europe", "europa", "european", "europeen", "royaume-uni", "united kingdom",
    "uk", "britain", "british", "angleterre", "england", "irlande", "ireland", "irish",
    "italie", "italy", "italian", "italia", "suisse", "switzerland", "swiss", "schweiz",
    "allemagne", "germany", "german", "pays-bas", "netherlands", "dutch", "belgique", "belgium",
    "pologne", "poland", "polish", "roumanie", "romania", "eastern europe", "europe de l'est",
    "frankreich", "frankrijk", "deutschland", "duitsland", "niederlande", "nederland", "polen", "italien",
    "schweiz", "osteuropa", "oost-europa", "vereinigtes konigreich", "verenigd koninkrijk", "belgie", "engeland",
    "rungis", "mercabarna", "mercamadrid", "supermarche", "supermarket", "retail", "import",
]

_RE_PRODUITS = _motif(PRODUITS)
_RE_ORIGINES = _motif(ORIGINES)
_RE_MARCHES = _motif(MARCHES)


def mots_trouves(texte):
    """(produits, origines, marches) trouves dans un texte, listes de chaines normalisees."""
    t = normaliser(texte)
    return (sorted(set(_RE_PRODUITS.findall(t))), sorted(set(_RE_ORIGINES.findall(t))),
            sorted(set(_RE_MARCHES.findall(t))))


def pertinent_generaliste(titre, resume=""):
    """Presse generaliste : produit Mehadrin ET (origine OU marche) dans le titre/resume."""
    prod, orig, march = mots_trouves(f"{titre} {resume}")
    return bool(prod) and bool(orig or march)


# --- Equilibre ---------------------------------------------------------------------------

def equilibrer(articles):
    """Reordonne les articles (deja tries par pertinence decroissante) SANS en retirer aucun.

    Tourniquet entre sources pour que les non-FreshPlaza passent en tete de lot : chaque source hors FreshPlaza est une file,
    le groupe FreshPlaza/AGF.nl (toutes editions) ne forme qu'UNE file ; a chaque tour, les files hors FreshPlaza passent
    d'abord. L'ordre de pertinence est conserve a l'interieur d'une file. Tous les articles sont rendus.
    """
    files, ordre = {}, []
    for a in articles:
        cle = GROUPE_FRESHPLAZA if a.get("groupe") == GROUPE_FRESHPLAZA else (a.get("source_id") or a.get("source"))
        if cle not in files:
            files[cle] = []
            ordre.append(cle)
        files[cle].append(a)
    ordre = [c for c in ordre if c != GROUPE_FRESHPLAZA] + [c for c in ordre if c == GROUPE_FRESHPLAZA]
    sortie = []
    while len(sortie) < len(articles):
        for c in ordre:
            if files[c]:
                sortie.append(files[c].pop(0))
    return sortie


def part_groupe(articles, groupe=GROUPE_FRESHPLAZA, n=None):
    """Part (0..1) des `n` premiers articles appartenant au groupe."""
    lot = articles[:n] if n else articles
    if not lot:
        return 0.0
    return sum(1 for a in lot if a.get("groupe") == groupe) / len(lot)


def _href(bloc):
    m = re.search(r'href="([^"]+)"', bloc)
    return html_mod.unescape(m.group(1)) if m else None


def limiter_groupe_html(blocs, groupe_par_href, max_fp):
    """Garde au plus `max_fp` blocs HTML du groupe FreshPlaza (ordre de sortie conserve)."""
    gardes, nb = [], 0
    for bloc in blocs:
        est_fp = groupe_par_href.get(_href(bloc)) == GROUPE_FRESHPLAZA
        if est_fp:
            if nb >= max_fp:
                continue
            nb += 1
        gardes.append(bloc)
    return gardes


_RE_LIEN_SOURCE = re.compile(r'(<div class="news-source">\s*<a href=")([^"]+)("[^>]*>)[^<]*</a>')


def etiqueter_source_html(bloc, source_par_href):
    """Reecrit le libelle du lien source d'un bloc `news-item` avec le vrai nom de la source (deterministe)."""
    def _remplace(m):
        nom = source_par_href.get(html_mod.unescape(m.group(2)))
        if not nom:
            return m.group(0)
        return f"{m.group(1)}{m.group(2)}{m.group(3)}{nom} -- Lire l'article</a>"
    return _RE_LIEN_SOURCE.sub(_remplace, bloc)


def appliquer_diversite_html(html_sortie, articles, max_fp=None):
    """Sortie Gemini (blocs `news-item`) : libelle de source deterministe ; si `max_fp` est donne, au plus `max_fp`
    blocs du groupe FreshPlaza/AGF.nl (None = aucune limite : le generateur n'en pose plus depuis le 24/10). `articles` = candidats envoyes a Gemini (champs link, source, groupe).
    Le texte hors blocs `news-item` (et la sentinelle AUCUN_ARTICLE_PERTINENT) n'est pas touche."""
    morceaux = re.split(r'(?=<div class="news-item">)', html_sortie)
    if len(morceaux) < 2:
        return html_sortie
    avant, blocs = morceaux[0], morceaux[1:]
    source_par_href = {a["link"]: a["source"] for a in articles}
    groupe_par_href = {a["link"]: a.get("groupe") for a in articles}
    blocs = [etiqueter_source_html(b, source_par_href) for b in blocs]
    if max_fp is not None:
        blocs = limiter_groupe_html(blocs, groupe_par_href, max_fp)
    return avant + "".join(blocs)


# --- Cle de sujet multilingue (dedoublonnage) -----------------------------------------------
# Meme article rapporte en NL / EN / ES / FR / DE => meme cle (produits + origines canoniques).
# Les noms canoniques sont ceux de veille_taxonomy (PRODUCTS / ORIGINS). La taxonomie n'est pas modifiee :
# ce dictionnaire d'alias la complete (mots composes neerlandais/allemands, adjectifs espagnols...).

ALIAS_PRODUITS = {
    "avocat": ["avocat", "avocado", "aguacate", "palta", "abacate", "hass"],
    "mango": ["mangue", "mango", "mangos", "mangoes"],
    "mandarine": ["mandarine", "mandarin", "mandarina", "mandarino", "mandarijn", "clementin", "nadorcott", "clemengold", "orri"],
    "orange": ["orange", "naranja", "arancia", "arance", "sinaasappel", "orangen"],
    "date": ["datte", "dattes", "medjool", "medjoul", "datteri", "dattel", "dadel"],
    "sweet_potato": ["patate douce", "sweet potato", "batata", "susskartoffel", "sukartoffel", "zoete aardappel"],
    "citrus_grapefruit": ["pamplemousse", "grapefruit", "pomelo", "pompelmo"],
    "pomegranate": ["grenade", "pomegranate", "granada", "melagrana", "granaatappel", "granatapfel"],
}
ALIAS_ORIGINES = {
    "ZAF": ["afrique du sud", "south africa", "sud-africain", "sudafrica", "sudafrican", "sudafrik", "zuid-afrika",
            "zuid afrika", "suedafrika"],
    "PER": ["perou", "peru", "peruvian", "peruan", "peruaan"],
    "MAR": ["maroc", "morocco", "moroccan", "marocain", "marruecos", "marroqui", "marokko", "marocco"],
    "KEN": ["kenya", "kenia", "kenyan", "keniat"],
    "ISR": ["israel", "israeli", "israelien", "israelisch"],
    "ESP": ["espagne", "spain", "spanish", "espana", "espanol", "spanien", "spanje", "spagna"],
    "EGY": ["egypte", "egypt", "egyptian", "egipto", "egitto", "agypten", "egypten"],
    "COL": ["colombie", "colombia", "colombian", "kolumbien"],
    "CHL": ["chili", "chile", "chilean", "chileno"],
    "TUR": ["turquie", "turkey", "turquia", "turchia", "turkei", "turkije"],
}


def _motif_alias(termes):
    morceaux = []
    for t in sorted({normaliser(x) for x in termes}, key=len, reverse=True):
        fin = r"(?![a-z0-9])" if len(t) <= 4 else ""
        if t == "mango":
            fin = r"(?!ld)"  # Mangold != mangue
        morceaux.append(re.escape(t) + fin)
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(morceaux) + ")")


_RE_ALIAS_PRODUITS = {k: _motif_alias(v) for k, v in ALIAS_PRODUITS.items()}
_RE_ALIAS_ORIGINES = {k: _motif_alias(v) for k, v in ALIAS_ORIGINES.items()}


def cle_sujet_multilingue(titre, resume=""):
    """frozenset(produits U origines) canoniques du TITRE, ou None si produit ou origine manque.

    Union de veille_taxonomy.classify_article (si importable) et des alias ci-dessus. Le titre seul est utilise :
    les resumes de deux editions n'evoquent pas les memes pays, ce qui separerait des articles identiques.
    """
    produits, origines = set(), set()
    try:
        from veille_taxonomy import classify_article
        p, o = classify_article(titre or "", "")
        produits.update(p)
        origines.update(o)
    except Exception:
        pass
    t = normaliser(titre)
    produits.update(k for k, rx in _RE_ALIAS_PRODUITS.items() if rx.search(t))
    origines.update(k for k, rx in _RE_ALIAS_ORIGINES.items() if rx.search(t))
    if produits and origines:
        return frozenset(produits | origines)
    return None
