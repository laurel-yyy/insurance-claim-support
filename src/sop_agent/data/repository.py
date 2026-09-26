"""Read-only access to policyholders, claims and guidance (SPEC §6.4).

There are no write methods: every side effect goes through the mock services in tools/.
"""

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Protocol

from sop_agent.domain.models import (
    AuthorizedRepresentative,
    Claim,
    ConsentScenario,
    DocumentGuideline,
    FieldInfo,
    Policyholder,
)
from sop_agent.domain.normalize import normalize_policy_number


class ClaimsRepository(Protocol):
    """Query interface used by the SOP rules; implementations must be read-only."""

    def policyholders(self) -> Sequence[Policyholder]: ...
    def policyholder(self, party_id: str) -> Policyholder | None: ...
    def policyholders_by_policy(self, policy_number: str) -> Sequence[Policyholder]: ...
    def claims_for(self, party_id: str) -> Sequence[Claim]: ...
    def claim(self, case_id: str) -> Claim | None: ...
    def representatives_for(self, party_id: str) -> Sequence[AuthorizedRepresentative]: ...
    def consent_scenario(self, name: str) -> ConsentScenario | None: ...
    def field_glossary(self) -> Mapping[str, FieldInfo]: ...
    def document_guideline(self) -> DocumentGuideline: ...


class InMemoryRepository:
    """Indexes validated fixture data once at startup; callers get immutable tuples and mapping proxies."""

    def __init__(
        self,
        policyholders: Sequence[Policyholder],
        claims: Sequence[Claim],
        representatives: Sequence[AuthorizedRepresentative],
        consent_scenarios: Sequence[ConsentScenario],
        glossary: Mapping[str, FieldInfo],
        guideline: DocumentGuideline,
    ) -> None:
        self._policyholders = tuple(policyholders)
        self._by_party = {p.party_id: p for p in self._policyholders}
        self._claims = tuple(claims)
        self._by_case = {c.case_id: c for c in self._claims}
        self._representatives = tuple(representatives)
        self._consent = {s.name: s for s in consent_scenarios}
        self._glossary = MappingProxyType(dict(glossary))
        self._guideline = guideline

    def policyholders(self) -> Sequence[Policyholder]:
        return self._policyholders

    def policyholder(self, party_id: str) -> Policyholder | None:
        return self._by_party.get(party_id)

    def policyholders_by_policy(self, policy_number: str) -> Sequence[Policyholder]:
        """Compare normalized forms on both sides so formatting never changes the V5 candidate set."""
        wanted = normalize_policy_number(policy_number)
        if wanted is None:
            return ()
        return tuple(p for p in self._policyholders if normalize_policy_number(p.policy_number) == wanted)

    def claims_for(self, party_id: str) -> Sequence[Claim]:
        return tuple(c for c in self._claims if c.party_id == party_id)

    def claim(self, case_id: str) -> Claim | None:
        return self._by_case.get(case_id)

    def representatives_for(self, party_id: str) -> Sequence[AuthorizedRepresentative]:
        return tuple(r for r in self._representatives if r.buyer_party_id == party_id)

    def representatives(self) -> Sequence[AuthorizedRepresentative]:
        return self._representatives

    def consent_scenario(self, name: str) -> ConsentScenario | None:
        return self._consent.get(name)

    def consent_scenario_names(self) -> Sequence[str]:
        return tuple(self._consent)

    def claims(self) -> Sequence[Claim]:
        return self._claims

    def field_glossary(self) -> Mapping[str, FieldInfo]:
        return self._glossary

    def document_guideline(self) -> DocumentGuideline:
        return self._guideline
