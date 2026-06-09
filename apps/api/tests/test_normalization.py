from app.services.normalization import normalize_arabic, tokenize


def test_normalize_arabic_removes_diacritics_and_unifies_letters():
    assert normalize_arabic("وَإِنَّ واجبَ الوُجودِ") == "وان واجب الوجود"


def test_tokenize_returns_normalized_tokens():
    assert tokenize("آثارُ الفلاسفة") == ["اثار", "الفلاسفه"]
