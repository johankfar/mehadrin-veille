import copy
import json
import re
import unittest
from pathlib import Path

from veille_taxonomy import ORIGINS, PRODUCTS, classify_article, enrich_articles


class TaxonomyTests(unittest.TestCase):
    def test_aliases_are_canonical(self):
        products, origins = classify_article(
            "Avocats Hass sud-africains et patates douces d'Egypte",
            "Les avocats et les patates douces sont explicitement cites.",
        )
        self.assertEqual(products, ["avocat", "sweet_potato"])
        self.assertEqual(origins, ["EGY", "ZAF"])

    def test_multilingual_aliases(self):
        products, origins = classify_article(
            "Spanish watermelons and Peruvian mandarinas",
            "Origines Spain et Peru publiees dans l'article.",
        )
        self.assertEqual(products, ["mandarine", "watermelon"])
        self.assertEqual(origins, ["ESP", "PER"])

    def test_uncertain_or_absent_is_empty(self):
        products, origins = classify_article(
            "Le marche europeen reste attentiste",
            "Aucun produit ni pays precis n'est cite.",
        )
        self.assertEqual(products, [])
        self.assertEqual(origins, [])

    def test_grenade_city_is_not_pomegranate(self):
        products, origins = classify_article(
            "Recolte de mangues a Grenade",
            "La province espagnole de Grenade est citee comme lieu.",
        )
        self.assertEqual(products, ["mango"])
        self.assertEqual(origins, ["ESP"])

    def test_tactical_impact_and_source_do_not_create_tags(self):
        html = '''
        <div class="news-item">
          <div class="news-title">Marche des mangues</div>
          <div class="news-body">La mangue est citee sans origine.
          <strong>Impact tactique :</strong> Contacter les clients en Israel.</div>
          <div class="news-source"><a href="#">Kenya Daily</a></div>
        </div>'''
        products, origins = classify_article("Marche des mangues", html)
        self.assertEqual(products, ["mango"])
        self.assertEqual(origins, [])

    def test_schema_and_idempotence_preserve_article(self):
        article = {
            "id": "x", "timestamp": "2026-07-19T00:00:00+00:00",
            "title": "Mangues d'Espagne", "category": "ALERTES SUPPLY",
            "commercials": ["Ophélie"], "langue": "fr",
            "source": "Source test", "content_fr": "Mangues d'Espagne",
            "content_en": "Spanish mangoes", "content_he": "",
        }
        original = copy.deepcopy(article)
        data, changed = enrich_articles({"articles": [article]})
        self.assertEqual(changed, 1)
        tagged = data["articles"][0]
        self.assertEqual(tagged["products"], ["mango"])
        self.assertEqual(tagged["origins"], ["ESP"])
        for key, value in original.items():
            self.assertEqual(tagged[key], value)
        again, changed_again = enrich_articles(copy.deepcopy(data))
        self.assertEqual(changed_again, 0)
        self.assertEqual(again, data)

    def test_live_output_has_arrays_on_every_article(self):
        path = Path(__file__).with_name("veille_live_f3.json")
        if not path.exists():
            self.skipTest("artefact F3 non encore construit")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(data["articles"])
        for article in data["articles"]:
            self.assertIsInstance(article.get("products"), list)
            self.assertIsInstance(article.get("origins"), list)
            self.assertTrue(set(article["products"]).issubset(PRODUCTS))
            self.assertTrue(set(article["origins"]).issubset(ORIGINS))

    def test_taxonomy_matches_cours_reference(self):
        cours = Path(r"C:\Users\johan\Desktop\MehadrinPipeline\cours_marches_builder\fable_src\coursmarches-logic.js")
        source = cours.read_text(encoding="utf-8")
        fams = re.search(r"var FAMS=\[(.*?)\];", source, re.S)
        self.assertIsNotNone(fams)
        actual_products = tuple(re.findall(r"'([^']+)'", fams.group(1)))
        self.assertEqual(actual_products, PRODUCTS)
        country_names = re.search(r"var COUNTRY_NAMES=\{(.*?)\};", source, re.S)
        self.assertIsNotNone(country_names)
        actual_origins = set(re.findall(r"(?:^|,)([A-Z]{3}):", country_names.group(1)))
        self.assertTrue(set(ORIGINS).issubset(actual_origins))


if __name__ == "__main__":
    unittest.main()
