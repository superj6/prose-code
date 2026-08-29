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
        "summary": {
            "type": "string",
            "description": "One paragraph (1-3 sentences, no blank lines) saying what the whole file is for and does",
        },
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
    "required": ["summary", "paragraphs"],
}

PERTURB_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "block": {"type": "string"},
        "text": {"type": "string", "description": "Full new text of the block on the edited side"},
        "label": {"type": "string", "description": "Short description of the change, e.g. 'add None guard'"},
    },
    "required": ["block", "text", "label"],
}

CODE_BLOCKS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "block": {"type": "string"},
                    "code": {"type": "string", "description": "Source code for this block (may be empty for the summary)"},
                },
                "required": ["block", "code"],
            },
        }
    },
    "required": ["blocks"],
}


FREE_PROSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string", "description": "One paragraph for the directory as a whole"},
        "paragraphs": {"type": "array", "items": {"type": "string", "description": "One paragraph, no blank lines"}},
    },
    "required": ["summary", "paragraphs"],
}
