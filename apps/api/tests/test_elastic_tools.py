from app.services.elastic_tools import elastic_agent_builder_tools


def test_parameterized_passage_lookup_tool_definition():
    tools = elastic_agent_builder_tools()
    passage_tool = next(tool for tool in tools if tool["tool_id"] == "hermeneut.passage_lookup")

    assert passage_tool["type"] == "ES|QL"
    assert "?query" in passage_tool["query"]
    assert "MATCH(text_raw, ?query)" in passage_tool["query"]
    assert "CONCAT" not in passage_tool["query"]
    assert "p-ghazali-001" not in passage_tool["query"]
    assert "hermeneut_passages" in passage_tool["query"]


def test_evidence_memory_tool_definition_targets_index():
    tools = elastic_agent_builder_tools()
    memory_tool = next(tool for tool in tools if tool["tool_id"] == "hermeneut.evidence_memory_write")

    assert memory_tool["target_index"] == "hermeneut_evidence"


def test_elastic_tool_catalog_covers_context_graph_memory_and_sources():
    tools = elastic_agent_builder_tools()
    tool_ids = {tool["tool_id"] for tool in tools}

    assert {
        "hermeneut.passage_lookup",
        "hermeneut.semantic_passage_lookup",
        "hermeneut.source_lookup",
        "hermeneut.author_work_graph",
        "hermeneut.evidence_memory_lookup",
        "hermeneut.library_scope_filter",
        "hermeneut.evidence_memory_write",
    } <= tool_ids
