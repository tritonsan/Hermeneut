from app.services.openai_proxy import sanitize_openai_payload
from app.settings import Settings


def test_sanitize_payload_removes_vertex_incompatible_schema_keys():
    payload = {
        "model": "google/gemini-2.5-pro",
        "tool_choice": "required",
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "search",
                    "parameters": {
                        "type": "object",
                        "propertyNames": {"pattern": "^[a-z]+$"},
                        "properties": {
                            "query": {
                                "type": "object",
                                "patternProperties": {"^x": {"type": "string"}},
                                "properties": {"value": {"type": "string"}},
                            }
                        },
                    },
                },
            }
        ],
    }

    cleaned = sanitize_openai_payload(payload, Settings())

    assert cleaned["tool_choice"] == "auto"
    parameters = cleaned["tools"][0]["function"]["parameters"]
    assert "propertyNames" not in parameters
    assert "patternProperties" not in parameters["properties"]["query"]
