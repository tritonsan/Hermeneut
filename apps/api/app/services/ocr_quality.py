import re


STRONG_OCR = "strong_text_layer_or_ocr"
USABLE_OCR = "usable_but_needs_review"
WEAK_OCR = "weak_ocr_needs_manual_review"


def classify_ocr_quality(pages: list[dict], avg_confidence: float) -> str:
    text = "\n".join(str(page.get("text") or page.get("vision_text") or page.get("text_layer") or "") for page in pages)
    if avg_confidence < 0.55 or not ocr_text_is_readable(text):
        return WEAK_OCR
    if avg_confidence >= 0.78 and ocr_text_is_readable(text, strict=True):
        return STRONG_OCR
    return USABLE_OCR


def ocr_text_is_readable(text: str, *, strict: bool = False) -> bool:
    sample = _compact(text)
    if not sample:
        return False
    letters = [char for char in sample if char.isalpha()]
    if len(letters) < (24 if strict else 12):
        return False
    arabic_letters = [char for char in letters if "\u0600" <= char <= "\u06ff"]
    if arabic_letters:
        arabic_ratio = len(arabic_letters) / max(len(letters), 1)
        if arabic_ratio < (0.45 if strict else 0.35):
            return False
    readable_chars = sum(1 for char in sample if char.isalnum() or char.isspace() or char in "،؛,.:-()[]«»؟")
    if readable_chars / max(len(sample), 1) < (0.82 if strict else 0.72):
        return False
    tokens = [token for token in re.split(r"\s+", sample) if token]
    meaningful_tokens = [token for token in tokens if len([char for char in token if char.isalpha()]) >= 2]
    if len(meaningful_tokens) < (6 if strict else 3):
        return False
    long_noise_tokens = [token for token in tokens if len(token) > 45]
    if len(long_noise_tokens) >= (1 if strict else 2):
        return False
    noisy_tokens = [token for token in tokens if _looks_noisy(token)]
    if noisy_tokens and len(noisy_tokens) / max(len(tokens), 1) > (0.18 if strict else 0.28):
        return False
    return True


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _looks_noisy(token: str) -> bool:
    if len(token) >= 8 and re.search(r"(.)\1{4,}", token):
        return True
    alnum = sum(1 for char in token if char.isalnum())
    symbols = sum(1 for char in token if not char.isalnum())
    if len(token) >= 6 and symbols / max(len(token), 1) > 0.35:
        return True
    if len(token) >= 16 and alnum / max(len(token), 1) < 0.75:
        return True
    return False
