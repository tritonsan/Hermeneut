PASSAGE_LOOKUP_ESQL = """FROM hermeneut_passages
| WHERE MATCH(text_raw, ?query)
   OR MATCH(text_normalized, ?query)
   OR MATCH(work_title, ?query)
   OR MATCH(author_name, ?query)
   OR MATCH(concepts, ?query)
| KEEP passage_id, text_raw, work_id, work_title, author_name, source_id, page_ref,
      domain, concepts, library_id, semantic_model
| LIMIT 10"""

SEMANTIC_PASSAGE_LOOKUP_ESQL = """FROM hermeneut_passages METADATA _score
| WHERE MATCH(translation_hint, ?query)
   OR MATCH(concepts, ?query)
   OR MATCH(text_raw, ?query)
   OR MATCH(text_normalized, ?query)
| SORT _score DESC
| KEEP passage_id, text_raw, translation_hint, work_id, work_title, author_name,
      source_id, page_ref, domain, concepts, library_id, semantic_model, _score
| LIMIT 10"""

SOURCE_LOOKUP_ESQL = """FROM hermeneut_sources
| WHERE MATCH(source_id, ?query)
   OR MATCH(work_id, ?query)
   OR MATCH(provider, ?query)
   OR MATCH(license_note, ?query)
| KEEP source_id, work_id, provider, url, file_type, license_status, ingestion_status,
      library_id, visibility, quality, gcs_raw_path, gcs_normalized_path
| LIMIT 10"""

AUTHOR_WORK_GRAPH_ESQL = """FROM hermeneut_edges
| WHERE MATCH(from, ?query)
   OR MATCH(to, ?query)
   OR MATCH(type, ?query)
   OR MATCH(from_id, ?query)
   OR MATCH(to_id, ?query)
   OR MATCH(relation, ?query)
| KEEP from, to, type, from_type, from_id, relation, to_type, to_id,
      provenance, confidence, verification_status
| LIMIT 20"""

EVIDENCE_MEMORY_LOOKUP_ESQL = """FROM hermeneut_evidence
| WHERE MATCH(query, ?query)
   OR MATCH(passage_id, ?query)
   OR MATCH(candidate_work, ?query)
   OR MATCH(verification_note, ?query)
| KEEP run_id, query, tool_used, passage_id, candidate_work, confidence,
      verification_note, retrieval_mode, relationship_fit_score
| SORT confidence DESC
| LIMIT 10"""

LIBRARY_SCOPE_FILTER_ESQL = """FROM hermeneut_passages
| WHERE library_id == ?library_id
| STATS passage_count = COUNT(), work_count = COUNT_DISTINCT(work_id),
        source_count = COUNT_DISTINCT(source_id) BY library_id"""

EVIDENCE_MEMORY_WRITE_DESCRIPTION = (
    "Writes the final selected evidence record for a Hermeneut run back into Elasticsearch, "
    "including run_id, query, passage_id, candidate_work, confidence, and verification_note."
)


def elastic_agent_builder_tools() -> list[dict]:
    return [
        {
            "tool_id": "hermeneut.passage_lookup",
            "type": "ES|QL",
            "description": (
                "Retrieves candidate evidence passages from the Hermeneut classical text corpus. "
                "Use this before making source claims."
            ),
            "parameters": [{"name": "query", "type": "string", "required": True}],
            "query": PASSAGE_LOOKUP_ESQL,
        },
        {
            "tool_id": "hermeneut.semantic_passage_lookup",
            "type": "ES|QL",
            "description": (
                "Retrieves meaning-level candidate passages using semantic fields such as translation hints, "
                "concept tags, normalized Arabic, and semantic model metadata. Use this for paraphrases or "
                "near-meaning matches when exact wording may differ."
            ),
            "parameters": [{"name": "query", "type": "string", "required": True}],
            "query": SEMANTIC_PASSAGE_LOOKUP_ESQL,
        },
        {
            "tool_id": "hermeneut.source_lookup",
            "type": "ES|QL",
            "description": (
                "Retrieves source metadata, license state, ingest state, and GCS vault paths for candidate "
                "works. Use it to distinguish discovered PDFs from searchable text layers."
            ),
            "parameters": [{"name": "query", "type": "string", "required": True}],
            "query": SOURCE_LOOKUP_ESQL,
        },
        {
            "tool_id": "hermeneut.author_work_graph",
            "type": "ES|QL",
            "description": (
                "Queries the Hermeneut bibliographic graph for author, work, concept, refutation, citation, "
                "and provenance edges that can narrow source hypotheses."
            ),
            "parameters": [{"name": "query", "type": "string", "required": True}],
            "query": AUTHOR_WORK_GRAPH_ESQL,
        },
        {
            "tool_id": "hermeneut.evidence_memory_lookup",
            "type": "ES|QL",
            "description": (
                "Looks up prior Hermeneut evidence decisions written to Elasticsearch so the agent can "
                "reuse previous source-grounded findings as context."
            ),
            "parameters": [{"name": "query", "type": "string", "required": True}],
            "query": EVIDENCE_MEMORY_LOOKUP_ESQL,
        },
        {
            "tool_id": "hermeneut.library_scope_filter",
            "type": "ES|QL",
            "description": (
                "Summarizes the active institutional library scope before retrieval, ensuring the agent "
                "only searches the approved collection."
            ),
            "parameters": [{"name": "library_id", "type": "string", "required": True}],
            "query": LIBRARY_SCOPE_FILTER_ESQL,
        },
        {
            "tool_id": "hermeneut.evidence_memory_write",
            "type": "index write / workflow",
            "description": EVIDENCE_MEMORY_WRITE_DESCRIPTION,
            "target_index": "hermeneut_evidence",
        },
    ]
