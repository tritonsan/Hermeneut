import re

ARABIC_DIACRITICS = re.compile(r"[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed]")
TATWEEL = "\u0640"

CHAR_MAP = str.maketrans(
    {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        "ى": "ي",
        "ئ": "ي",
        "ؤ": "و",
        "ة": "ه",
    }
)


def normalize_arabic(text: str) -> str:
    text = text.replace(TATWEEL, "")
    text = ARABIC_DIACRITICS.sub("", text)
    text = text.translate(CHAR_MAP)
    text = re.sub(r"[^\w\s\u0600-\u06ff]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def tokenize(text: str) -> list[str]:
    normalized = normalize_arabic(text)
    return [token for token in normalized.split(" ") if token]
