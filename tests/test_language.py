"""Offline script and language-hint detection contracts."""
from iralens.search.language import detect_language


def test_latin_language_hints_cover_romanized_search_queries():
    examples = {
        "fr": "où trouver la meilleure musique près de Paris",
        "es": "¿qué receta es mejor para una cena rápida?",
        "pt": "qual é o melhor caminho para a cidade",
        "de": "wie funktioniert eine erneuerbare heizung",
        "it": "quanto costa una nuova bicicletta",
        "nl": "wat is een goede manier voor beginners",
        "sv": "var ligger det största berget i landet",
        "tr": "şehirde en iyi restoranı nasıl bulurum",
        "id": "berapa jumlah pengguna yang ada",
        "vi": "cách tìm đường đến thành phố gần đây",
    }
    for expected, query in examples.items():
        assert detect_language(query).wikipedia_lang == expected, query


def test_language_detector_recognizes_unaccented_spanish_procedural_queries():
    assert detect_language("como instalar un paquete en Windows").wikipedia_lang == "es"


def test_language_detector_prefers_japanese_in_mixed_kanji_and_kana():
    info = detect_language("大阪市の人口")
    assert info.wikipedia_lang == "ja"
    assert info.script == "Japanese"
    assert info.is_latin is False


def test_language_detector_distinguishes_ukrainian_and_persian_from_script_peers():
    assert detect_language("де знайти найближчу станцію").wikipedia_lang == "uk"
    assert detect_language("چگونه به نزدیکترین ایستگاه بروم").wikipedia_lang == "fa"


def test_language_detector_keeps_english_and_existing_indic_yoruba_hints():
    assert detect_language("what is the weather today").wikipedia_lang == "en"
    assert detect_language("भारत की राजधानी क्या है").wikipedia_lang == "hi"
    assert detect_language("ẹ káàbọ̀ sí ilé wa").wikipedia_lang in {"yo", "en"}
