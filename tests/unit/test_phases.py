from sop_agent.domain.enums import Phase
from sop_agent.sop.phases import PHASES, RECORD_SCOPES, ContextScope, Freedom, ToolName

S = ContextScope


def test_inv2_verify_id_scopes_contain_no_record_data() -> None:
    assert PHASES[Phase.VERIFY_ID].context_scopes.isdisjoint(RECORD_SCOPES)


def test_inv2_verify_id_has_no_tools() -> None:
    assert PHASES[Phase.VERIFY_ID].allowed_tools == frozenset()


def test_inv2_record_scopes_are_exactly_the_record_data_scopes() -> None:
    assert {
        S.PARTY_PROFILE,
        S.CLAIM_SUMMARIES,
        S.SELECTED_CLAIM,
        S.CLAIM_GUIDANCE,
        S.CASE_LOG,
        S.SUMMARY_DRAFT,
    } == RECORD_SCOPES


def test_every_phase_has_a_spec() -> None:
    assert set(PHASES) == set(Phase)
    assert all(spec.phase is phase for phase, spec in PHASES.items())


def test_read_scopes_match_the_scope_table() -> None:
    always = {S.SOP_STATUS, S.USER_STATED_HINTS}
    assert PHASES[Phase.VERIFY_ID].context_scopes == always | {S.GENERAL_KB}
    assert PHASES[Phase.RESOLVE_INTENT].context_scopes == always | {
        S.GENERAL_KB,
        S.PARTY_PROFILE,
        S.CLAIM_SUMMARIES,
    }
    assert PHASES[Phase.PROCESS_CASE].context_scopes == always | {
        S.GENERAL_KB,
        S.PARTY_PROFILE,
        S.CLAIM_SUMMARIES,
        S.SELECTED_CLAIM,
        S.CLAIM_GUIDANCE,
        S.CASE_LOG,
    }
    assert PHASES[Phase.POST_PROCESS].context_scopes == always | {
        S.PARTY_PROFILE,
        S.CASE_LOG,
        S.SUMMARY_DRAFT,
    }


def test_tool_whitelists_match_the_expected_table() -> None:
    assert PHASES[Phase.RESOLVE_INTENT].allowed_tools == {ToolName.LIST_CLAIMS}
    assert PHASES[Phase.PROCESS_CASE].allowed_tools == set(ToolName)
    for phase in (Phase.VERIFY_ID, Phase.POST_PROCESS, Phase.ESCALATED, Phase.ENDED):
        assert PHASES[phase].allowed_tools == frozenset()


def test_freedom_levels() -> None:
    assert PHASES[Phase.VERIFY_ID].freedom is Freedom.STRICT
    assert PHASES[Phase.RESOLVE_INTENT].freedom is Freedom.BOUNDED
    assert PHASES[Phase.PROCESS_CASE].freedom is Freedom.GUIDED
    assert PHASES[Phase.POST_PROCESS].freedom is Freedom.STRICT
