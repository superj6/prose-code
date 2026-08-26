"""JSON schemas for structured output (strict mode: every property required, no extras)."""

EDITS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "op": {"type": "string", "enum": ["replace", "delete"]},
                    "block": {"type": "string", "description": "Block id such as b3"},
                    "text": {
                        "type": ["string", "null"],
                        "description": "Full new text of the block on the target side; null for delete",
                    },
                    "reason": {"type": "string", "description": "One short sentence for the user"},
                },
                "required": ["op", "block", "text", "reason"],
            },
        }
    },
    "required": ["edits"],
}

PARAGRAPHS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "paragraphs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "block": {"type": "string"},
                    "prose": {"type": "string", "description": "One paragraph, no blank lines"},
                },
                "required": ["block", "prose"],
            },
        }
    },
    "required": ["paragraphs"],
}
