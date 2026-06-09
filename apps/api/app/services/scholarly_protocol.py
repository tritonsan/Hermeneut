from __future__ import annotations

from app.models import RunCreate
from app.services.normalization import normalize_arabic

RESEARCH_PROTOCOL_STEPS = [
    "Identify language, discipline, technical terms, and attribution form.",
    "Classify the containing text: base text, commentary, hashiya, abridgement, refutation, doxography, or anthology.",
    "If a containing author/work is supplied, treat it as citation context rather than as the target source.",
    "Infer the relevant textual tradition and intermediary commentary chain before ranking authors.",
    "Generate broad candidate authors by role: base-text author, primary commentator, later glossator, polemical target, school authority, and adjacent tradition.",
    "Generate phrase variants: exact wording, normalized Arabic, technical-term query, semantic paraphrase, and metadata/transliteration query.",
    "Verify candidate authors/works through metadata and web/source resolvers before download selection.",
    "Select OCR targets only from direct, reviewable PDF/text sources.",
    "Make final attribution only after OCR/indexing produces Elastic passage evidence.",
]


COMMENTARY_MARKERS = {
    "حاشيه": "hashiya",
    "حاشية": "hashiya",
    "hashiya": "hashiya",
    "hashiyah": "hashiya",
    "شرح": "commentary",
    "sharh": "commentary",
    "commentary": "commentary",
    "تعليق": "annotation",
    "تعليقات": "annotation",
    "gloss": "annotation",
    "تحرير": "revision_commentary",
    "تهذيب": "abridgement_or_reworking",
    "مختصر": "abridgement",
    "رد": "refutation",
    "نقض": "refutation",
    "علي": "on_text",
    "على": "on_text",
    "ala": "on_text",
    "on": "on_text",
}


TEXTUAL_TRADITIONS = {
    "shamsiyya": {
        "label": "Shamsiyya logic commentary tradition",
        "markers": ["شمسي", "الشمسية", "شمسية", "shamsiyya", "shamsiya", "risala shamsiyya"],
        "domain": "logic/commentary",
        "authors": [
            {
                "author_id": "qutb-al-din-al-razi",
                "name": "Qutb al-Din al-Razi",
                "name_ar": "قطب الدين الرازي",
                "period": "8th/14th century",
                "tradition": "logic commentary / Shamsiyya commentary",
                "death_year": 1365,
                "source_role": "primary_intermediary_commentator",
                "relationship_reason": (
                    "In Shamsiyya hashiyas, Qutb al-Din al-Razi's Tahrir/Sharh is a central intermediary "
                    "commentary that later glossators commonly cite, summarize, or dispute."
                ),
                "relation_fit": 0.98,
            },
            {
                "author_id": "najm-al-din-al-katibi",
                "name": "Najm al-Din al-Katibi",
                "name_ar": "نجم الدين الكاتبي",
                "period": "7th/13th century",
                "tradition": "logic / author of al-Risala al-Shamsiyya",
                "death_year": 1277,
                "source_role": "base_text_author",
                "relationship_reason": "The containing work belongs to the Shamsiyya tradition; al-Katibi is the base-text author.",
                "relation_fit": 0.92,
            },
            {
                "author_id": "al-taftazani",
                "name": "al-Taftazani",
                "name_ar": "التفتازاني",
                "period": "8th/14th century",
                "tradition": "logic commentary / Shamsiyya commentary",
                "death_year": 1390,
                "source_role": "parallel_commentator",
                "relationship_reason": "Later Shamsiyya commentary tradition candidate; useful for parallel formulations.",
                "relation_fit": 0.84,
            },
            {
                "author_id": "al-jurjani",
                "name": "al-Jurjani",
                "name_ar": "الجرجاني",
                "period": "8th/14th century",
                "tradition": "logic commentary / hashiya tradition",
                "death_year": 1413,
                "source_role": "later_glossator",
                "relationship_reason": "Later Shamsiyya gloss tradition candidate; useful for reception-chain comparison.",
                "relation_fit": 0.82,
            },
        ],
        "works": [
            {
                "work_id": "qutb-razi-tahrir-shamsiyya",
                "title": "Tahrir al-qawaid al-mantiqiyya fi sharh al-Risala al-Shamsiyya",
                "title_ar": "تحرير القواعد المنطقية في شرح الرسالة الشمسية",
                "author_id": "qutb-al-din-al-razi",
                "score": 0.96,
                "source_role": "primary_intermediary_commentary",
                "relationship_reason": "Primary intermediary commentary for later Shamsiyya hashiyas.",
            },
            {
                "work_id": "katibi-shamsiyya",
                "title": "al-Risala al-Shamsiyya",
                "title_ar": "الرسالة الشمسية في القواعد المنطقية",
                "author_id": "najm-al-din-al-katibi",
                "score": 0.9,
                "source_role": "base_text",
                "relationship_reason": "Base text of the containing Shamsiyya commentary tradition.",
            },
            {
                "work_id": "taftazani-sharh-shamsiyya",
                "title": "Sharh al-Shamsiyya",
                "title_ar": "شرح الشمسية",
                "author_id": "al-taftazani",
                "score": 0.78,
                "source_role": "parallel_commentary",
                "relationship_reason": "Parallel commentary in the same tradition; useful if the wording circulated across commentaries.",
            },
            {
                "work_id": "jurjani-hashiya-shamsiyya",
                "title": "Hashiya ala al-Shamsiyya",
                "title_ar": "حاشية على الشمسية",
                "author_id": "al-jurjani",
                "score": 0.72,
                "source_role": "later_hashiya",
                "relationship_reason": "Later gloss tradition candidate; useful for reception-chain variants.",
            },
        ],
        "title_variants": {
            "katibi-shamsiyya": [
                "katibi shamsiyya",
                "shamsiyya logic arabic",
                "الرسالة الشمسية الكاتبي",
                "Risala Shamsiyya Katibi",
            ],
            "qutb-razi-tahrir-shamsiyya": [
                "تحرير القواعد المنطقية في شرح الرسالة الشمسية",
                "قطب الدين الرازي تحرير القواعد المنطقية",
                "Qutb al-Din al-Razi Tahrir al-qawaid al-mantiqiyya",
                "شرح الرسالة الشمسية للرازي",
            ],
            "taftazani-sharh-shamsiyya": [
                "شرح الشمسية التفتازاني",
                "التفتازاني على الشمسية",
                "Taftazani Sharh Shamsiyya",
            ],
            "jurjani-hashiya-shamsiyya": [
                "حاشية الجرجاني على الشمسية",
                "Hashiya Jurjani Shamsiyya",
            ],
        },
    },
    "isharat": {
        "label": "Isharat Avicennan commentary tradition",
        "markers": ["اشارات", "الإشارات", "الاشارات"],
        "domain": "philosophy/commentary",
        "authors": [
            {
                "author_id": "ibn-sina",
                "name": "Ibn Sina",
                "name_ar": "ابن سينا",
                "period": "5th/11th century",
                "tradition": "falsafa / Avicennan philosophy",
                "death_year": 1037,
                "source_role": "base_text_author",
                "relationship_reason": "Base author of al-Isharat wa-al-tanbihat.",
                "relation_fit": 0.9,
            },
            {
                "author_id": "nasir-al-din-al-tusi",
                "name": "Nasir al-Din al-Tusi",
                "name_ar": "نصير الدين الطوسي",
                "period": "7th/13th century",
                "tradition": "Avicennan commentary / philosophy",
                "death_year": 1274,
                "source_role": "primary_commentator",
                "relationship_reason": "Central commentator on al-Isharat; likely intermediary in later philosophical discussions.",
                "relation_fit": 0.86,
            },
        ],
        "works": [
            {
                "work_id": "ibn-sina-isharat",
                "title": "al-Isharat wa-al-tanbihat",
                "title_ar": "الإشارات والتنبيهات",
                "author_id": "ibn-sina",
                "score": 0.84,
                "source_role": "base_text",
                "relationship_reason": "Base Avicennan source in the Isharat commentary tradition.",
            },
            {
                "work_id": "tusi-sharh-isharat",
                "title": "Sharh al-Isharat",
                "title_ar": "شرح الإشارات",
                "author_id": "nasir-al-din-al-tusi",
                "score": 0.78,
                "source_role": "primary_commentary",
                "relationship_reason": "Major intermediary commentary on al-Isharat.",
            },
        ],
        "title_variants": {
            "tusi-sharh-isharat": ["شرح الاشارات الطوسي", "Tusi Sharh al Isharat"],
        },
    },
    "tahafut": {
        "label": "Tahafut polemical tradition",
        "markers": ["تهافت"],
        "domain": "falsafa-kalam polemic",
        "authors": [
            {
                "author_id": "al-ghazali",
                "name": "al-Ghazali",
                "name_ar": "الغزالي",
                "period": "5th/11th century",
                "tradition": "kalam/falsafa critique",
                "death_year": 1111,
                "source_role": "polemical_author",
                "relationship_reason": "Author of Tahafut al-falasifa; often summarizes philosophers before refuting them.",
                "relation_fit": 0.9,
            },
            {
                "author_id": "ibn-rushd",
                "name": "Ibn Rushd",
                "name_ar": "ابن رشد",
                "period": "6th/12th century",
                "tradition": "falsafa / Tahafut response",
                "death_year": 1198,
                "source_role": "polemical_respondent",
                "relationship_reason": "Respondent in the Tahafut tradition; useful for counter-citation chains.",
                "relation_fit": 0.78,
            },
        ],
        "works": [
            {
                "work_id": "ghazali-tahafut",
                "title": "Tahafut al-falasifa",
                "title_ar": "تهافت الفلاسفة",
                "author_id": "al-ghazali",
                "score": 0.9,
                "source_role": "polemical_source",
                "relationship_reason": "Canonical kalam critique that often reports philosopher positions.",
            },
            {
                "work_id": "ibn-rushd-tahafut",
                "title": "Tahafut al-tahafut",
                "title_ar": "تهافت التهافت",
                "author_id": "ibn-rushd",
                "score": 0.74,
                "source_role": "polemical_response",
                "relationship_reason": "Response text in the same polemical tradition.",
            },
        ],
        "title_variants": {
            "ibn-rushd-tahafut": ["تهافت التهافت ابن رشد", "Ibn Rushd Tahafut al Tahafut"],
        },
    },
}


class ScholarlyProtocol:
    """General academic candidate protocol for classical-text source discovery."""

    def classify_containing_text(self, payload: RunCreate) -> dict:
        text = self.containing_text(payload)
        detected_markers = [
            {"marker": marker, "relation": relation}
            for marker, relation in COMMENTARY_MARKERS.items()
            if marker in text
        ]
        tradition_id = self.detect_tradition_id(payload)
        tradition = TEXTUAL_TRADITIONS.get(tradition_id or "", {})
        return {
            "text_relation": detected_markers[0]["relation"] if detected_markers else "unspecified",
            "detected_markers": detected_markers,
            "tradition_id": tradition_id,
            "tradition_label": tradition.get("label"),
            "tradition_domain": tradition.get("domain"),
            "candidate_roles": [
                "base_text_author",
                "primary_intermediary_commentator",
                "later_glossator",
                "polemical_target",
                "school_authority",
                "adjacent_tradition_author",
            ],
            "research_protocol": RESEARCH_PROTOCOL_STEPS,
        }

    def detect_tradition_id(self, payload: RunCreate) -> str | None:
        text = self.containing_text(payload)
        passage_context = normalize_arabic(" ".join([payload.passage, payload.context or "", payload.domain_hint or ""]))
        library_hint = normalize_arabic(payload.library_id or "")
        if "shamsiyya" in library_hint or "shamsiya" in library_hint:
            return "shamsiyya"
        for tradition_id, tradition in TEXTUAL_TRADITIONS.items():
            markers = [normalize_arabic(marker) for marker in tradition["markers"]]
            if any(marker and (marker in text or marker in passage_context) for marker in markers):
                return tradition_id
        if "العالم" in passage_context and "قديم" in passage_context:
            return "tahafut"
        return None

    def author_templates(self, payload: RunCreate) -> list[dict]:
        tradition = TEXTUAL_TRADITIONS.get(self.detect_tradition_id(payload) or "")
        if not tradition:
            return []
        return [self._author_template(item) for item in tradition["authors"]]

    def work_templates(self, payload: RunCreate) -> list[dict]:
        tradition = TEXTUAL_TRADITIONS.get(self.detect_tradition_id(payload) or "")
        if not tradition:
            return []
        return [dict(item) for item in tradition["works"]]

    def title_variants(self, work_id: str) -> list[str]:
        variants: list[str] = []
        for tradition in TEXTUAL_TRADITIONS.values():
            variants.extend(tradition.get("title_variants", {}).get(work_id, []))
        return variants

    def relationship_fit(self, author_id: str, payload: RunCreate) -> tuple[float, str] | None:
        tradition = TEXTUAL_TRADITIONS.get(self.detect_tradition_id(payload) or "")
        if not tradition:
            return None
        for author in tradition["authors"]:
            if author["author_id"] == author_id:
                return float(author.get("relation_fit", 0.75)), str(author["relationship_reason"])
        classification = self.classify_containing_text(payload)
        if classification["text_relation"] in {"hashiya", "commentary", "annotation", "on_text"}:
            return 0.62, "The containing text is commentary-like; candidate retained as an adjacent commentary-chain possibility."
        return None

    def containing_text(self, payload: RunCreate) -> str:
        return normalize_arabic(" ".join([payload.containing_author or "", payload.containing_work or ""]))

    def _author_template(self, item: dict) -> dict:
        return {
            "author_id": item["author_id"],
            "name": item["name"],
            "name_ar": item["name_ar"],
            "aliases": [],
            "death_year": item["death_year"],
            "period": item["period"],
            "tradition": item["tradition"],
            "source_role": item["source_role"],
            "hypothesis_fit": 0.58,
            "relationship_reason": item["relationship_reason"],
            "metadata_status": "requires_backend_verification",
        }
