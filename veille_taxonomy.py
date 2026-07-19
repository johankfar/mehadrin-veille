#!/usr/bin/env python3
"""Taxonomie deterministe produit/origine pour la veille Mehadrin.

Les tags sont uniquement des associations de navigation. Ils sont derives de
mentions explicites dans le titre/resume deja publie, puis limites au
referentiel canonique de Cours Marches. Une absence ou une ambiguite donne une
liste vide : aucun produit ni origine n'est deduit du commercial, de la source
ou du calendrier saisonnier.
"""

from __future__ import annotations

import argparse
import copy
import html
import json
import re
import unicodedata
from pathlib import Path


# Valeurs canoniques identiques a CMLOGIC.FAMS (familles de la page Cours).
PRODUCTS = (
    "avocat", "lemon", "date", "mandarine", "mango", "melon", "orange",
    "watermelon", "sweet_potato", "pear", "apple", "grape", "cherry",
    "kiwi", "plum", "apricot", "nectarine", "asparagus", "berry",
    "butternut", "citrus_grapefruit", "coconut", "ginger", "lime",
    "papaya", "passionfruit", "physalis", "pineapple", "pitahaya",
    "plantain", "pomegranate",
)

# Valeurs canoniques identiques aux ISO3 de CMLOGIC.COUNTRY_NAMES. Les zones
# vagues (EU, SAM, "Afrique") sont volontairement absentes : association trop
# incertaine pour un lien article -> origine.
ORIGINS = (
    "FRA", "ITA", "ESP", "DEU", "MAR", "ISR", "EGY", "TUN", "ZAF",
    "PER", "BRA", "COL", "CHL", "MEX", "ARG", "NLD", "USA", "CRI",
    "TUR", "PRT", "KEN", "IRN", "CIV", "GRC", "IND", "SEN", "JOR",
    "DOM", "PRI", "CHN", "NZL",
)


PRODUCT_ALIASES = {
    "avocat": ("avocat", "avocats", "avocado", "avocados", "aguacate", "aguacates", "hass"),
    "lemon": ("citron", "citrons", "lemon", "lemons", "limon", "limones", "limone", "limoni"),
    "date": ("datte", "dattes", "medjoul", "medjool", "datteri", "datil", "datiles", "dattel"),
    "mandarine": ("mandarine", "mandarines", "mandarin", "mandarins", "mandarina", "mandarinas", "orri", "nadorcott", "clemengold", "clementine", "clementines", "clemenvilla", "satsuma", "tango"),
    "mango": ("mangue", "mangues", "mango", "mangos", "mangoes"),
    "melon": ("melon", "melons", "melone", "meloni", "cantaloup", "galia", "honeydew", "piel de sapo"),
    "orange": ("orange", "oranges", "naranja", "naranjas", "arancia", "arance", "valencia", "navel", "tarocco", "salustiana"),
    "watermelon": ("pasteque", "pasteques", "watermelon", "watermelons", "sandia", "sandias", "anguria", "angurie", "wassermelone"),
    "sweet_potato": ("patate douce", "patates douces", "sweet potato", "sweet potatoes", "batata dulce", "batatas dulces", "susskartoffel"),
    "pear": ("poire", "poires", "pear", "pears", "pera", "pere"),
    "apple": ("pomme", "pommes", "apple", "apples", "manzana", "manzanas", "mela", "mele"),
    "grape": ("raisin", "raisins", "table grape", "table grapes", "uva de mesa", "uvas de mesa", "uva da tavola", "uve da tavola"),
    "cherry": ("cerise", "cerises", "cherry", "cherries", "cereza", "cerezas", "ciliegia", "ciliegie"),
    "kiwi": ("kiwi", "kiwis"),
    "plum": ("prune", "prunes", "plum", "plums", "ciruela", "ciruelas", "susina", "susine"),
    "apricot": ("abricot", "abricots", "apricot", "apricots", "albaricoque", "albaricoques", "albicocca", "albicocche"),
    "nectarine": ("nectarine", "nectarines", "nectarina", "nectarinas"),
    "asparagus": ("asperge", "asperges", "asparagus", "esparrago", "esparragos", "asparago", "asparagi"),
    "berry": ("baie", "baies", "berries", "myrtille", "myrtilles", "blueberry", "blueberries", "framboise", "framboises", "raspberry", "raspberries", "mure", "mures", "blackberry", "blackberries", "fraise", "fraises", "strawberry", "strawberries"),
    "butternut": ("butternut", "courge butternut", "butternut squash"),
    "citrus_grapefruit": ("pamplemousse", "pamplemousses", "grapefruit", "grapefruits", "pomelo", "pomelos", "pompelmo", "pompelmi", "star ruby", "sweetie"),
    "coconut": ("noix de coco", "coconut", "coconuts", "coco"),
    "ginger": ("gingembre", "ginger", "jengibre", "zenzero"),
    "lime": ("lime", "limes", "citron vert", "citrons verts", "lima", "limas", "tahiti lime", "persian lime"),
    "papaya": ("papaye", "papayes", "papaya", "papayas"),
    "passionfruit": ("fruit de la passion", "fruits de la passion", "passion fruit", "passion fruits", "maracuja"),
    "physalis": ("physalis",),
    "pineapple": ("ananas", "pineapple", "pineapples", "pina tropical"),
    "pitahaya": ("pitaya", "pitayas", "pitahaya", "pitahayas", "dragon fruit"),
    "plantain": ("banane plantain", "bananes plantains", "plantain", "plantains", "platano macho"),
    # "Grenade" au singulier est aussi la province espagnole : on ne la tague
    # jamais sans marqueur fruit explicite. Le pluriel et les noms non ambigus
    # restent admis.
    "pomegranate": ("grenades", "grenade fruit", "pomegranate", "pomegranates", "melagrana", "melagrane", "granada fruit"),
}

ORIGIN_ALIASES = {
    "FRA": ("france", "francais", "francaise", "french"),
    "ITA": ("italie", "italien", "italienne", "italy", "italian", "italia", "sicile", "sicily", "sicilia"),
    "ESP": ("espagne", "espagnol", "espagnole", "spain", "spanish", "espana"),
    "DEU": ("allemagne", "allemand", "allemande", "germany", "german", "deutschland"),
    "MAR": ("maroc", "marocain", "marocaine", "morocco", "moroccan", "marocco"),
    "ISR": ("israel", "israelien", "israelienne", "israeli"),
    "EGY": ("egypte", "egypt", "egyptien", "egyptienne", "egyptian", "egitto"),
    "TUN": ("tunisie", "tunisien", "tunisienne", "tunisia", "tunisian"),
    "ZAF": ("afrique du sud", "sud africain", "sud africains", "sud africaine", "sud africaines", "south africa", "south african", "south africans", "sudafrica"),
    "PER": ("perou", "peruvien", "peruvienne", "peru", "peruvian"),
    "BRA": ("bresil", "bresilien", "bresilienne", "brazil", "brazilian", "brasil"),
    "COL": ("colombie", "colombien", "colombienne", "colombia", "colombian"),
    "CHL": ("chili", "chilien", "chilienne", "chile", "chilean"),
    "MEX": ("mexique", "mexicain", "mexicaine", "mexico", "mexican"),
    "ARG": ("argentine", "argentin", "argentinian", "argentina"),
    "NLD": ("pays bas", "neerlandais", "neerlandaise", "netherlands", "dutch", "holland"),
    "USA": ("etats unis", "united states", "u s a", "usa", "americain", "americaine", "american"),
    "CRI": ("costa rica", "costaricain", "costaricaine", "costa rican"),
    "TUR": ("turquie", "turc", "turque", "turkey", "turkish", "turchia"),
    "PRT": ("portugal", "portugais", "portugaise", "portuguese"),
    "KEN": ("kenya", "kenyan"),
    "IRN": ("iran", "iranien", "iranienne", "iranian"),
    "CIV": ("cote d ivoire", "ivoirien", "ivoirienne", "ivory coast", "cote ivoire"),
    "GRC": ("grece", "grec", "grecque", "greece", "greek"),
    "IND": ("inde", "indien", "indienne", "india", "indian"),
    "SEN": ("senegal", "senegalais", "senegalaise", "senegalese"),
    "JOR": ("jordanie", "jordanien", "jordanienne", "jordan", "jordanian"),
    "DOM": ("republique dominicaine", "dominican republic", "dominicaine", "dominican"),
    "PRI": ("porto rico", "puerto rico", "puerto rican"),
    "CHN": ("chine", "chinois", "chinoise", "china", "chinese"),
    "NZL": ("nouvelle zelande", "new zealand", "neo zelandais", "neo zelandaise"),
}


def _fold(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().replace("’", "'")
    return " " + re.sub(r"[^a-z0-9]+", " ", value).strip() + " "


def _mentions(text: str, alias: str) -> bool:
    folded_alias = _fold(alias).strip()
    return bool(folded_alias and (" " + folded_alias + " ") in text)


def _factual_text(content_fr: str) -> str:
    """Isole titre + resume, sans impact tactique ni libelle de source."""
    source = content_fr or ""
    title_match = re.search(r'<div class="news-title">(.*?)</div>', source, re.I | re.S)
    body_match = re.search(r'<div class="news-body">(.*?)</div>', source, re.I | re.S)
    parts = []
    if title_match:
        parts.append(title_match.group(1))
    if body_match:
        body = re.split(r'<strong[^>]*>\s*Impact tactique\s*:?</strong>', body_match.group(1), maxsplit=1, flags=re.I)[0]
        parts.append(body)
    if parts:
        return " ".join(parts)
    # Compatibilite defensible avec un ancien HTML non structure : on coupe
    # avant les deux surfaces qui ne sont pas des faits de l'article.
    source = re.split(r'<strong[^>]*>\s*Impact tactique\s*:?</strong>', source, maxsplit=1, flags=re.I)[0]
    source = re.split(r'<div class="news-source">', source, maxsplit=1, flags=re.I)[0]
    return source


def classify_article(title: str, content_fr: str) -> tuple[list[str], list[str]]:
    """Retourne uniquement les mentions explicites produit/origine."""
    text = _fold((title or "") + " " + _factual_text(content_fr))
    products = [key for key in PRODUCTS if any(_mentions(text, a) for a in PRODUCT_ALIASES[key])]
    origins = [key for key in ORIGINS if any(_mentions(text, a) for a in ORIGIN_ALIASES[key])]
    return products, origins


def enrich_articles(data: dict) -> tuple[dict, int]:
    """Ajoute/remplace les deux tableaux, sans toucher aux autres champs."""
    changed = 0
    for article in data.get("articles", []):
        products, origins = classify_article(article.get("title", ""), article.get("content_fr", ""))
        if article.get("products") != products or article.get("origins") != origins:
            changed += 1
        article["products"] = products
        article["origins"] = origins
    return data, changed


def _main() -> int:
    parser = argparse.ArgumentParser(description="Ajoute products[]/origins[] a une sortie veille existante")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true", help="dry-run : aucune ecriture")
    args = parser.parse_args()
    original = json.loads(args.input.read_text(encoding="utf-8"))
    tagged, changed = enrich_articles(copy.deepcopy(original))
    summary = {
        "articles": len(tagged.get("articles", [])),
        "changed": changed,
        "tagged_products": sum(bool(a.get("products")) for a in tagged.get("articles", [])),
        "tagged_origins": sum(bool(a.get("origins")) for a in tagged.get("articles", [])),
        "write": False,
    }
    if not args.check:
        if not args.output:
            parser.error("--output est requis sans --check")
        args.output.write_text(json.dumps(tagged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary["write"] = True
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
