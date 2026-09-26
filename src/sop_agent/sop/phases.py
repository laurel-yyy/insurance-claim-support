"""Phase specs: freedom level, allowed tools and context scopes per phase (SPEC §7.3, §9.1.3).

INV-2 is structural: VERIFY_ID's scopes never include a record scope, so no record data can reach its prompts.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from sop_agent.domain.enums import Phase


class Freedom(StrEnum):
    """STRICT: code decides, LLM words it. BOUNDED: LLM picks among candidates. GUIDED: free reasoning."""

    STRICT = "strict"
    BOUNDED = "bounded"
    GUIDED = "guided"


class ContextScope(StrEnum):
    """Slices of grounding the ContextBuilder may assemble."""

    SOP_STATUS = "sop_status"
    USER_STATED_HINTS = "user_stated_hints"
    GENERAL_KB = "general_kb"
    PARTY_PROFILE = "party_profile"
    CLAIM_SUMMARIES = "claim_summaries"
    SELECTED_CLAIM = "selected_claim"
    CLAIM_GUIDANCE = "claim_guidance"
    CASE_LOG = "case_log"
    SUMMARY_DRAFT = "summary_draft"


RECORD_SCOPES: frozenset[ContextScope] = frozenset(
    {
        ContextScope.PARTY_PROFILE,
        ContextScope.CLAIM_SUMMARIES,
        ContextScope.SELECTED_CLAIM,
        ContextScope.CLAIM_GUIDANCE,
        ContextScope.CASE_LOG,
        ContextScope.SUMMARY_DRAFT,
    }
)


class ToolName(StrEnum):
    """Read-only tools the responder may call (§11.1). There are no write tools (INV-5)."""

    LIST_CLAIMS = "list_claims"
    GET_CLAIM = "get_claim"
    GET_DOCUMENT_GUIDANCE = "get_document_guidance"
    GET_FOLLOWUP_GUIDANCE = "get_followup_guidance"


TERMINAL_PHASES: frozenset[Phase] = frozenset({Phase.ESCALATED, Phase.ENDED})


@dataclass(frozen=True)
class PhaseSpec:
    """Static description of one phase."""

    phase: Phase
    freedom: Freedom
    goal: str
    allowed_tools: frozenset[ToolName]
    context_scopes: frozenset[ContextScope]
    instructions_file: str


_ALWAYS = frozenset({ContextScope.SOP_STATUS, ContextScope.USER_STATED_HINTS})

PHASES: Mapping[Phase, PhaseSpec] = MappingProxyType(
    {
        Phase.VERIFY_ID: PhaseSpec(
            phase=Phase.VERIFY_ID,
            freedom=Freedom.STRICT,
            goal="Verify the caller's identity (and, for a representative, authorization and consent).",
            allowed_tools=frozenset(),
            context_scopes=_ALWAYS | {ContextScope.GENERAL_KB},
            instructions_file="phases/verify_id.md",
        ),
        Phase.RESOLVE_INTENT: PhaseSpec(
            phase=Phase.RESOLVE_INTENT,
            freedom=Freedom.BOUNDED,
            goal="Determine which claim and which workflow path to handle.",
            allowed_tools=frozenset({ToolName.LIST_CLAIMS}),
            context_scopes=_ALWAYS
            | {ContextScope.GENERAL_KB, ContextScope.PARTY_PROFILE, ContextScope.CLAIM_SUMMARIES},
            instructions_file="phases/resolve_intent.md",
        ),
        Phase.PROCESS_CASE: PhaseSpec(
            phase=Phase.PROCESS_CASE,
            freedom=Freedom.GUIDED,
            goal="Carry out the selected path and answer grounded follow-up questions.",
            allowed_tools=frozenset(ToolName),
            context_scopes=_ALWAYS
            | {
                ContextScope.GENERAL_KB,
                ContextScope.PARTY_PROFILE,
                ContextScope.CLAIM_SUMMARIES,
                ContextScope.SELECTED_CLAIM,
                ContextScope.CLAIM_GUIDANCE,
                ContextScope.CASE_LOG,
            },
            instructions_file="phases/process_case.md",
        ),
        Phase.POST_PROCESS: PhaseSpec(
            phase=Phase.POST_PROCESS,
            freedom=Freedom.STRICT,
            goal="Offer the summary email and respect the caller's choice.",
            allowed_tools=frozenset(),
            context_scopes=_ALWAYS
            | {ContextScope.PARTY_PROFILE, ContextScope.CASE_LOG, ContextScope.SUMMARY_DRAFT},
            instructions_file="phases/post_process.md",
        ),
        Phase.ESCALATED: PhaseSpec(
            phase=Phase.ESCALATED,
            freedom=Freedom.STRICT,
            goal="The caller is being transferred to a live agent.",
            allowed_tools=frozenset(),
            context_scopes=frozenset({ContextScope.SOP_STATUS}),
            instructions_file="phases/escalated.md",
        ),
        Phase.ENDED: PhaseSpec(
            phase=Phase.ENDED,
            freedom=Freedom.STRICT,
            goal="The session is over.",
            allowed_tools=frozenset(),
            context_scopes=frozenset({ContextScope.SOP_STATUS}),
            instructions_file="phases/ended.md",
        ),
    }
)
