"""Observer: gathers external status before decide() so the policy stays pure.

Consent is polled only while a request is pending; the ClaimSelector runs only while a CHOOSE_CLAIM question is
open (so the caller is verified). Failures produce no observation, which the policy treats as "still waiting".
"""

from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.enums import PATHS_NEEDING_CLAIM, ConsentStatus, IdentityStatus, Phase, VerifyStage
from sop_agent.memory.state import SessionState
from sop_agent.nlu.selector import ClaimSelector, SelectorCandidate
from sop_agent.observability.logging import get_logger
from sop_agent.sop.directive import Observations, PendingQuestionKind
from sop_agent.tools.consent import ConsentError, ScenarioConsentService

_log = get_logger(__name__)


class Observer:
    def __init__(self, repo: InMemoryRepository, consent: ScenarioConsentService) -> None:
        self._repo = repo
        self._consent = consent

    async def observe(self, state: SessionState, text: str, selector: ClaimSelector) -> Observations:
        observations = Observations()
        rep = state.memory.representative
        if (
            state.phase is Phase.VERIFY_ID
            and state.verify_stage is VerifyStage.CONSENT
            and rep.consent_status is ConsentStatus.PENDING
            and rep.consent_request_id
        ):
            try:
                observations.consent = self._consent.poll(rep.consent_request_id)
            except ConsentError:
                _log.warning("consent poll failed")
        pending = state.pending_question
        if (
            pending is not None
            and pending.kind is PendingQuestionKind.CHOOSE_CLAIM
            and state.memory.identity.status is IdentityStatus.VERIFIED
        ):
            candidates = self._candidates(state, [str(c) for c in pending.payload.get("candidates", [])])
            observations.claim_selection = await selector.select(
                text, candidates, sorted(PATHS_NEEDING_CLAIM)
            )
        return observations

    def _candidates(self, state: SessionState, case_ids: list[str]) -> list[SelectorCandidate]:
        """Only the verified caller's own claims, in the order they were shown."""
        party_id = state.memory.identity.party_id
        out: list[SelectorCandidate] = []
        for case_id in case_ids:
            claim = self._repo.claim(case_id)
            if claim is None or claim.party_id != party_id:
                continue
            summary = f"{claim.case_type}, created {claim.created_at.isoformat()}, {claim.raw_status}: {claim.summary}"
            out.append(SelectorCandidate(case_id=claim.case_id, summary=summary))
        return out
