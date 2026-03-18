#!/usr/bin/env python3
"""
veille_translate.py -- Traductions FR -> EN / HE via Gemini Flash
=================================================================
Meme methode que translate_html_content() du pipeline principal.
"""

import os
import re
import sys
import time

# Ajouter le dossier parent pour importer config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from config import GEMINI_API_KEY, GEMINI_API_KEY_DEFAULT, GEMINI_MODEL_FLASH
except ImportError:
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
    GEMINI_API_KEY_DEFAULT = os.environ.get("GEMINI_API_KEY_DEFAULT", "")
    GEMINI_MODEL_FLASH = "gemini-3-flash-preview"


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


def translate_html(html_content, target_lang, api_key=None):
    """Translate HTML content to target language using Gemini Flash.

    target_lang: 'en' or 'he'
    Returns translated HTML or original on failure.
    """
    key = api_key or GEMINI_API_KEY or GEMINI_API_KEY_DEFAULT or os.environ.get("GEMINI_API_KEY", "")
    if not key or not html_content or not html_content.strip():
        return html_content

    try:
        from google import genai
        client = genai.Client(api_key=key)
        lang_name = "English" if target_lang == "en" else "Hebrew"
        dir_hint = " All text direction should be RTL." if target_lang == "he" else ""

        prompt = (
            f"Translate the following HTML content to {lang_name}. "
            f"Keep ALL HTML tags, CSS classes, attributes, URLs, and structure exactly intact. "
            f"Only translate the visible text content.{dir_hint} "
            f"Do NOT add any emojis. "
            f"Return ONLY the translated HTML, nothing else, no markdown fences, no explanation.\n\n"
            f"{html_content}"
        )

        response = _gemini_call_with_retry(
            client, model=GEMINI_MODEL_FLASH, contents=[prompt],
        )
        result = response.text
        result = re.sub(r'```\w*\n?', '', result).strip()
        if len(result) > 50:
            print(f"  Traduction {target_lang} OK ({len(result)} chars)")
            return result
        else:
            print(f"  Traduction {target_lang} trop courte ({len(result)} chars) -- fallback original")
            return html_content
    except Exception as e:
        print(f"  Traduction {target_lang} echouee : {e}")
        return html_content


def translate_all(html_fr):
    """Traduit le HTML francais en anglais et hebreu.

    Returns: (html_en, html_he)
    """
    print("  Traduction EN...")
    html_en = translate_html(html_fr, "en")
    print("  Traduction HE...")
    html_he = translate_html(html_fr, "he")
    return html_en, html_he
