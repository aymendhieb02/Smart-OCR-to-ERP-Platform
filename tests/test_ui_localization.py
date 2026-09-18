from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "app" / "static" / "index.html"
APP_JS = ROOT / "app" / "static" / "app.js"
STRINGS_JS = ROOT / "app" / "static" / "strings.js"


def run_node(source: str) -> str:
    result = subprocess.run(
        ["node", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def test_dictionaries_have_matching_complete_key_sets_and_french_default() -> None:
    payload = json.loads(run_node(
        "require('./app/static/strings.js');"
        "const i=global.AppI18n;"
        "console.log(JSON.stringify({en:Object.keys(i.dictionaries.en).sort(),fr:Object.keys(i.dictionaries.fr).sort(),d:i.DEFAULT_LOCALE,f:i.FALLBACK_LOCALE}));"
    ))

    assert payload["en"] == payload["fr"]
    assert len(payload["en"]) >= 250
    assert payload["d"] == "fr"
    assert payload["f"] == "en"


def test_locale_fallback_missing_key_interpolation_and_unicode_preservation() -> None:
    payload = json.loads(run_node(
        "require('./app/static/strings.js');"
        "const i=global.AppI18n;"
        "i.setLocale('fr',{apply:false}); const fr=i.t('review.page',{current:2,total:3});"
        "const mixed='SOCIÉTÉ — مرحبا'; const preserved=i.t('camera.captured_file',{filename:mixed});"
        "i.setLocale('en',{apply:false}); const en=i.t('app.heading');"
        "const unknown=i.setLocale('xx',{apply:false}); const missing=i.t('not.a.real.key');"
        "console.log(JSON.stringify({fr,preserved,en,unknown,missing}));"
    ))

    assert payload["fr"] == "Page 2 / 3"
    assert "SOCIÉTÉ — مرحبا" in payload["preserved"]
    assert payload["en"] == "Invoice review workspace"
    assert payload["unknown"] == "en"
    assert payload["missing"] == "[missing:not.a.real.key]"


def test_apply_translations_updates_text_placeholder_title_aria_and_html_lang() -> None:
    source = r"""
const elements = {
  text: {dataset:{i18n:'app.heading'}, textContent:''},
  placeholder: {dataset:{i18nPlaceholder:'upload.choose'}, placeholder:''},
  title: {dataset:{i18nTitle:'line_items.total_title'}, title:''},
  aria: {dataset:{i18nAriaLabel:'camera.dialog_aria'}, setAttribute(k,v){this[k]=v;}},
};
global.document = {
  title:'x', readyState:'complete', documentElement:{dataset:{uiLocale:'fr'},lang:'en'},
  querySelectorAll(selector){
    return selector==='[data-i18n]'?[elements.text]:selector==='[data-i18n-placeholder]'?[elements.placeholder]:selector==='[data-i18n-title]'?[elements.title]:selector==='[data-i18n-aria-label]'?[elements.aria]:[];
  }
};
global.window=global;
require('./app/static/strings.js');
console.log(JSON.stringify({elements,lang:document.documentElement.lang,title:document.title}));
"""
    payload = json.loads(run_node(source))

    assert payload["lang"] == "fr"
    assert payload["elements"]["text"]["textContent"] == "Espace de vérification des factures"
    assert payload["elements"]["placeholder"]["placeholder"] == "Choisir une facture ou un document"
    assert payload["elements"]["title"]["title"].startswith("Somme des montants TTC")
    assert payload["elements"]["aria"]["aria-label"] == "Fenêtre de capture photo"


def test_static_translation_keys_exist_and_strings_load_before_application() -> None:
    html = HTML.read_text(encoding="utf-8")
    strings = STRINGS_JS.read_text(encoding="utf-8")
    keys = set(re.findall(r'data-i18n(?:-placeholder|-title|-aria-label)?="([^"]+)"', html))

    assert '<html lang="fr" data-ui-locale="fr">' in html
    assert html.index("/static/strings.js") < html.index("/static/app.js")
    assert keys
    for key in keys:
        assert f'"{key}"' in strings, key


def test_business_state_and_source_data_are_independent_of_display_language() -> None:
    script = APP_JS.read_text(encoding="utf-8")

    assert 'status === "ERP Ready"' not in script
    assert 'status === "Rejected"' not in script
    assert 'const className = readiness.ready ? "ready"' in script
    assert 'normalized.extracted_text || ""' in script
    assert 'supplier_name' in script
    assert 'needs_review' in script


def test_pragmatic_guardrail_rejects_new_literal_copy_in_known_ui_sinks() -> None:
    script = APP_JS.read_text(encoding="utf-8")
    violations = []
    patterns = {
        "textContent": r'\.textContent\s*=\s*["\'][A-Za-z][^"\']+["\']',
        "showError": r'showError\(\s*["\'][A-Za-z][^"\']+["\']\s*\)',
        "innerHTML": r'\.innerHTML\s*=\s*["\'][^"\']*[A-Za-z]{3}[^"\']*["\']',
    }
    for name, pattern in patterns.items():
        for match in re.finditer(pattern, script):
            literal = match.group(0)
            if literal.endswith('textContent = ""'):
                continue
            violations.append(f"{name}: {literal}")

    assert not violations, "Likely user-visible hardcoded copy found:\n" + "\n".join(violations)
