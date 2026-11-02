"""Build the drafting request: explanation prose from a finished evidence packet.

Drafting only (D148/D149): the model receives the deterministic packet and can
never change its diagnosis. Instructions live in the versioned system prompt;
the packet is delimited data in the user message.

Answered and abstained packets are drafted: an abstention explanation is what
an analyst most needs in words. Failed and out_of_scope packets are skipped:
they carry no investigated evidence to explain, only a deterministic reason.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

from reconcile.domain.contracts import Disposition, EvidencePacket
from reconcile.models.client import ModelRequest
from reconcile.models.validate import MAX_CLAIMS, MAX_SUMMARY, MAX_TEXT, NO_ACTION

PROMPT_VERSION = "draft_v1"
PROMPT_PATH = Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.md"
DRAFTED = frozenset({Disposition.ANSWERED, Disposition.ABSTAINED})

DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "claims", "next_step"],
    "properties": {
        "summary": {
            "type": "string",
            "description": f"At most {MAX_SUMMARY} characters.",
        },
        "claims": {
            "type": "array",
            "description": f"At most {MAX_CLAIMS} claims.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["finding", "text", "evidence_ids", "calculation_ids"],
                "properties": {
                    "finding": {"type": "string"},
                    "text": {
                        "type": "string",
                        "description": f"At most {MAX_TEXT} characters.",
                    },
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "calculation_ids": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "next_step": {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "text"],
            "properties": {
                "action": {"type": "string"},
                "text": {
                    "type": "string",
                    "description": f"At most {MAX_TEXT} characters.",
                },
            },
        },
    },
}


def prompt_text() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def prompt_sha256() -> str:
    return hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest()


def should_draft(packet: EvidencePacket) -> bool:
    return packet.diagnosis.disposition in DRAFTED


def packet_data(packet: EvidencePacket) -> str:
    """Compact packet JSON that cannot close the data delimiter: "<" is written
    as its JSON escape, so the text still parses to the same packet."""
    text = json.dumps(
        packet.to_obj(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return text.replace("<", "\\u003c")


def build_request(
    packet: EvidencePacket, model: str, effort: str, max_output_tokens: int
) -> ModelRequest:
    checks = [f.check_id for f in packet.diagnosis.findings]
    actions = [r.action_type for r in packet.recommendations] or [NO_ACTION]
    user = (
        f"Allowed finding check_ids: {json.dumps(checks)}\n"
        f"Allowed next_step actions: {json.dumps(actions)}\n"
        "<packet>\n"
        f"{packet_data(packet)}\n"
        "</packet>"
    )
    return ModelRequest(
        model=model,
        system=prompt_text(),
        user=user,
        schema=DRAFT_SCHEMA,
        max_output_tokens=max_output_tokens,
        effort=effort,
    )
