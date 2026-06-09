from app.models import DetectedContext, RunCreate
from app.services.bibliographic_reasoning import BibliographicReasoningService
from app.services.scholarly_protocol import ScholarlyProtocol
from app.services.web_research import WebResearchService
from app.settings import Settings


def test_web_source_candidate_normalization_marks_pdf_download_candidate():
    service = WebResearchService(Settings())
    hit = service._normalized_web_hit(
        "Archive PDF",
        "https://ia601304.us.archive.org/16/items/demo/source.pdf",
        "demo",
        "العالم قديم",
    )

    candidates = service._web_source_candidates([hit], RunCreate(passage="العالم قديم"))

    assert candidates[0]["source_page_url"] == hit["source_page_url"]
    assert candidates[0]["download_url"].endswith(".pdf")
    assert candidates[0]["file_type"] == "pdf"
    assert candidates[0]["lifecycle_status"] == "download_candidate"
    assert candidates[0]["license_status"] == "needs_review"


def test_research_questions_treat_containing_author_as_context_not_target():
    service = WebResearchService(Settings())
    questions = service._research_questions(
        RunCreate(passage="قيل إن زيدا ممكن", containing_author="Later commentator"),
        DetectedContext(
            language="ar",
            domain="logic/philosophy",
            period_hint="unknown",
            citation_type="ambiguous",
            key_terms=["الإمكان الخاص"],
        ),
    )

    assert any("In works by Later commentator" in question for question in questions)


def test_open_discovery_research_outputs_academic_candidate_layers():
    service = WebResearchService(Settings(google_cloud_project=None))
    result = service.research(
        RunCreate(
            mode="open_discovery",
            passage="قيل ان زيدا ممكن وصدق زيد موجود بالامكان الخاص",
            domain_hint="logic/philosophy",
            containing_author="Later commentator",
            containing_work="Hashiya on a logic text",
        ),
        DetectedContext(
            language="ar",
            domain="logic/philosophy",
            period_hint="post-Avicennan period",
            citation_type="ambiguous attribution marker",
            key_terms=["ممكن", "الإمكان الخاص"],
        ),
        [],
    )

    assert result["context_profile"]["interpretation_policy"].startswith("containing_author/work")
    assert result["context_profile"]["research_protocol"]
    assert "containing text" in result["context_profile"]["research_protocol"][1].lower()
    assert result["model_routing"]["context_domain_analysis"].startswith("google/gemini-3.1-flash-lite")
    assert result["model_routing"]["final_scholarly_report"] == "google/gemini-3.1-pro-preview"
    assert result["academic_intelligence"]["prompt_profile"] == "professor_grade_multi_agent_discovery_chain_v2"
    assert result["academic_intelligence"]["decision_tier"] in {"confirmed", "probable", "strong_lead", "weak_lead", "no_result"}
    assert result["decision_tier"] in {"confirmed", "probable", "strong_lead", "weak_lead", "no_result"}
    assert "professor-level bibliographic caution" in result["academic_intelligence"]["prompt_excerpt"]
    assert [agent["agent_id"] for agent in result["academic_intelligence"]["subagents"]] == [
        "context_scholar",
        "relationship_scholar",
        "candidate_scholar",
        "search_strategist",
        "web_evidence_critic",
        "source_selection_judge",
        "decision_calibrator",
    ]
    assert any(item["kind"] == "semantic_arabic" for item in result["phrase_variants"])
    assert any(candidate["author_id"] == "najm-al-din-al-katibi" for candidate in result["candidate_authors"])
    assert result["candidate_web_searches"]
    assert all("rejection_reason" in item for item in result["rejected_candidates"])


def test_shamsiyya_hashiya_context_prioritizes_qutb_al_din_razi():
    service = WebResearchService(Settings(google_cloud_project=None))
    result = service.research(
        RunCreate(
            mode="open_discovery",
            passage="قال حتى انهم قسموا الادوات الى غير زمانية",
            domain_hint="logic/commentary",
            containing_author="أبو الإسعاد عصام الدين إبراهيم",
            containing_work="حاشية عصام الدين على شمسية",
            max_source_candidates=10,
        ),
        DetectedContext(
            language="ar",
            domain="logic/commentary",
            period_hint="post-classical",
            citation_type="ambiguous attribution marker",
            key_terms=["الأدوات", "غير زمانية"],
        ),
        [],
    )

    assert result["context_profile"]["commentary_chain_detected"] == "shamsiyya"
    classification = result["context_profile"]["containing_text_classification"]
    assert classification["text_relation"] == "hashiya"
    assert "primary_intermediary_commentator" in classification["candidate_roles"]
    assert result["candidate_authors"][0]["author_id"] == "qutb-al-din-al-razi"
    assert result["candidate_authors"][0]["source_role"] == "primary_intermediary_commentator"
    assert any(work["work_id"] == "qutb-razi-tahrir-shamsiyya" for work in result["candidate_works"][:3])
    assert any("تحرير القواعد المنطقية" in source["title"] for source in result["source_candidates"])


def test_shamsiyya_context_detects_transliterated_title_and_library_scope():
    protocol = ScholarlyProtocol()
    payload = RunCreate(
        mode="open_discovery",
        passage="qawluhu ambiguous gloss",
        containing_work="Hashiya ala al-Shamsiyya",
        library_id="shamsiyya_hashiya_demo",
    )

    classification = protocol.classify_containing_text(payload)

    assert classification["tradition_id"] == "shamsiyya"
    assert classification["text_relation"] == "hashiya"


def test_run_create_defaults_to_three_pdf_download_targets():
    assert RunCreate(passage="العالم قديم").max_pdf_downloads == 3


def test_internet_archive_download_file_falls_back_to_best_downloadable_file():
    service = BibliographicReasoningService(Settings())

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "files": [
                    {"name": "unrelated_scan.pdf", "size": "5000"},
                    {"name": "unrelated_meta.xml", "size": "500"},
                ]
            }

    class Client:
        def get(self, _url):
            return Response()

    download_url, file_type, file_name, file_size = service._internet_archive_download_file(
        Client(),
        "demo-identifier",
        "expected title tokens that do not appear",
    )

    assert download_url == "https://archive.org/download/demo-identifier/unrelated_scan.pdf"
    assert file_type == "pdf"
    assert file_name == "unrelated_scan.pdf"
    assert file_size == 5000


def test_internet_archive_download_file_prefers_text_layer_for_retrieval():
    service = BibliographicReasoningService(Settings())

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "files": [
                    {"name": "tahrir.pdf", "size": "5000"},
                    {"name": "tahrir_text.txt", "size": "2000"},
                ]
            }

    class Client:
        def get(self, _url):
            return Response()

    download_url, file_type, file_name, _file_size = service._internet_archive_download_file(
        Client(),
        "demo-identifier",
        "tahrir",
    )

    assert download_url == "https://archive.org/download/demo-identifier/tahrir_text.txt"
    assert file_type == "text"
    assert file_name == "tahrir_text.txt"


def test_candidate_specific_search_plan_executes_backend_resolvers(monkeypatch):
    service = BibliographicReasoningService(Settings())

    def fake_archive_candidates(_client, query, work):
        if "Qutb" not in query:
            return []
        return [
            {
                "source_id": "ia-qutb-test",
                "work_id": work["work_id"],
                "provider": "Internet Archive",
                "title": "Qutb al-Din al-Razi Tahrir test scan",
                "url": "https://archive.org/details/qutb-test",
                "source_page_url": "https://archive.org/details/qutb-test",
                "download_url": "https://archive.org/download/qutb-test/qutb-test_text.txt",
                "file_type": "text",
                "download_policy": "admin_approval_required",
                "ingestion_status": "web_discovered",
                "lifecycle_status": "download_candidate",
                "relationship_reason": "fake",
                "provenance": "internet_archive_resolver",
                "verification_status": "metadata_only",
                "license_status": "needs_review",
                "grounding_metadata": {"query": query},
                "candidate_score": 0.8,
                "relevance_score": 0.8,
                "relevance_breakdown": {"direct_download": 0.22},
            }
        ]

    monkeypatch.setattr(service, "_internet_archive_candidates", fake_archive_candidates)
    result = service.reason(
        RunCreate(
            mode="open_discovery",
            passage="قال حتى انهم قسموا الادوات الى غير زمانية",
            domain_hint="logic/commentary",
            containing_work="حاشية عصام الدين على شمسية",
            max_source_candidates=5,
            max_pdf_downloads=1,
        ),
        DetectedContext(
            language="ar",
            domain="logic/commentary",
            period_hint="post-classical",
            citation_type="ambiguous attribution marker",
            key_terms=["الأدوات"],
        ),
        [],
        [],
        "fallback",
        {
            "candidate_search_plan": [
                {
                    "candidate": "Qutb al-Din al-Razi Tahrir al-qawaid",
                    "query": "Qutb al-Din al-Razi Tahrir al-qawaid archive.org PDF",
                    "target": "Internet Archive",
                    "expected_signal": "downloadable scan or OCR text",
                }
            ]
        },
    )

    first_search = result["candidate_web_searches"][0]
    assert first_search["execution_status"] == "executed"
    assert "internet_archive" in first_search["executed_targets"]
    assert first_search["resolver_result_count"] == 1
    assert first_search["resolver_results"][0]["source_id"] == "ia-qutb-test"
    assert any(source["source_id"] == "ia-qutb-test" for source in result["source_candidates"])
    assert result["top_pdf_targets"][0]["source_id"] == "ia-qutb-test"


def test_open_discovery_creates_containing_layer_source_targets(monkeypatch):
    service = BibliographicReasoningService(Settings())

    def fake_archive_candidates(_client, query, work):
        if work.get("source_role") != "containing_layer":
            return []
        return [
            {
                "source_id": "ia-containing-layer",
                "work_id": work["work_id"],
                "provider": "Internet Archive",
                "title": "Containing hashiya scan",
                "url": "https://archive.org/details/containing-layer",
                "source_page_url": "https://archive.org/details/containing-layer",
                "download_url": "https://archive.org/download/containing-layer/containing-layer_text.txt",
                "file_type": "text",
                "download_policy": "admin_approval_required",
                "ingestion_status": "web_discovered",
                "lifecycle_status": "download_candidate",
                "relationship_reason": "fake",
                "provenance": "internet_archive_resolver",
                "verification_status": "metadata_only",
                "license_status": "needs_review",
                "grounding_metadata": {"query": query},
                "candidate_score": 0.9,
                "relevance_score": 0.9,
                "relevance_breakdown": {"direct_download": 0.22},
            }
        ]

    monkeypatch.setattr(service, "_internet_archive_candidates", fake_archive_candidates)
    result = service.reason(
        RunCreate(
            mode="open_discovery",
            passage="قوله كذا",
            containing_author="Isam al-Din al-Isfarayini",
            containing_work="Hashiya on al-Shamsiyya",
            max_source_candidates=12,
            max_pdf_downloads=6,
        ),
        DetectedContext(language="ar", domain="logic", period_hint="post-classical", citation_type="ambiguous", key_terms=[]),
        [],
        [],
        "fallback",
        {},
    )

    assert any(work["source_role"] == "containing_layer" for work in result["work_candidates"])
    assert any(target["source_role"] == "containing_layer" for target in result["top_pdf_targets"])


def test_top_pdf_targets_apply_three_plus_three_role_quota():
    service = BibliographicReasoningService(Settings())
    sources = []
    for role in ["containing_layer", "citation_chain"]:
        for index in range(5):
            sources.append(
                {
                    "source_id": f"{role}-{index}",
                    "title": f"{role} {index}",
                    "download_url": f"https://archive.org/download/{role}-{index}/{role}-{index}_text.txt",
                    "file_type": "text",
                    "lifecycle_status": "download_candidate",
                    "source_role": role,
                    "candidate_score": 1 - index * 0.01,
                    "relevance_score": 1 - index * 0.01,
                }
            )

    selected = service._top_pdf_targets(
        sources,
        RunCreate(mode="open_discovery", passage="قوله كذا", max_pdf_downloads=6),
    )

    assert len([item for item in selected if item["source_role"] == "containing_layer"]) == 3
    assert len([item for item in selected if item["source_role"] == "citation_chain"]) == 3
