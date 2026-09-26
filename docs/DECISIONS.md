# Decisions

Each entry: context, decision, consequence.

## D1. One identity field for SSN and national ID last four
- **Context**: Policyholders have `id_type` `ssn_last4` or `national_id_last4` (SPEC §6.2.1).
- **Decision**: The field is `id_last4` everywhere. Only the digits are compared; the ID type the caller names is
  recorded for audit but is not a matching condition. The agent asks for "the last four digits of your SSN or
  national ID".
- **Consequence**: A caller who says "SSN" but holds a national ID still verifies if the digits match. Simpler
  prompts; no extra probing surface.

## D2. Aliases match exactly; no fuzzy matching
- **Context**: The data contains ASR-style aliases (e.g. a mistranscribed name) and near-identical phone numbers
  (§6.2.2–3).
- **Decision**: A value matches if it equals the primary value or a listed alias after normalization. Nothing else
  counts. The loader deduplicates alias lists and drops aliases equal to the primary value.
- **Consequence**: Some real speech errors will fail verification; that is the conservative choice (§19).

## D3. Demo date pinned to 2026-03-10
- **Context**: Appeal deadlines in the sample data are in the past relative to the real date (§6.2.7).
- **Decision**: `DEMO_TODAY=2026-03-10` by default; the container builds a `FixedClock` from it. An empty value
  selects `SystemClock`. All code reads time through the injected `Clock`.
- **Consequence**: The sample data is internally consistent in the demo; `/api/health` reports the effective date.

## D4. `AuthorizedRepresentative` vs `LiveAgent`
- **Context**: "Representative" means a third party calling for the policyholder in the data, and a human
  support agent in the brief (§6.2.10).
- **Decision**: Code uses `AuthorizedRepresentative` for the former and `LiveAgent` for the latter. Customer-facing
  copy says "a member of our claims team" or "a live representative".
- **Consequence**: No ambiguity in code or events.

## D5. Spec location moved to `docs/`
- **Context**: The starter shipped the spec as `doc/SPEC.md`, while CLAUDE.md and SPEC §5 reference `docs/`.
- **Decision**: Moved it to `docs/SPEC.md` unchanged.
- **Consequence**: Paths in CLAUDE.md and the spec are now correct.

## D6. Loader tolerance details
- **Context**: Reviewers may swap in a different dataset with the same schema (§6.2.11, §6.5).
- **Decision**: Unknown record keys are ignored; `case_type` is lowercased; guideline keys (document names, case
  types, settings) are casefolded; consent statuses and topic `intent_hints`/`match_any` are casefolded and
  `match_any` is deduplicated. Representatives referencing an unknown `buyer_party_id` fail fast, like claims.
- **Consequence**: Later lookups can compare normalized keys directly. A dataset with dangling references will not
  start.

## D7. Repository exposes a few extras beyond the §6.4 protocol
- **Context**: The health summary needs total counts and scenario names.
- **Decision**: `InMemoryRepository` adds `claims()`, `representatives()` and `consent_scenario_names()`. The
  `ClaimsRepository` protocol is unchanged, so business logic cannot rely on bulk access.
- **Consequence**: Only the loader summary and tests use the extras.

## D8. `make` is not available on the development machine
- **Context**: The Windows dev environment has no `make` on PATH.
- **Decision**: The Makefile is written per §14.3; locally, `make check` was verified by running its recipes
  directly (`uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`, `uv run pytest -m "not live"`).
- **Consequence**: None for Docker or Unix environments.

## D9. `.env.example` comments on their own lines
- **Context**: §14.1 puts comments after values on the same line (e.g. `DEMO_TODAY=2026-03-10  # ...`).
- **Decision**: Moved those comments to the line above.
- **Consequence**: No risk of an env loader reading the comment as part of the value.

## D10. LF line endings enforced by `.gitattributes`
- **Context**: The Windows dev machine converts line endings on checkout, and the Docker image runs on Linux.
- **Decision**: `.gitattributes` sets `* text=auto eol=lf`; binary image types are marked binary.
- **Consequence**: Line endings are the same on every platform; no CRLF churn in diffs.

## D11. General FAQ deferred until the context builder exists
- **Context**: `data/kb/faq.md` (§6.7) isn't used until grounding is assembled.
- **Decision**: Create it in M4 together with `agent/context.py`, the first thing that reads it.
- **Consequence**: `FAQ_PATH` is configured now but points at a file that doesn't exist yet; nothing reads it before M4.

## D12. `NLUResult` (domain side) defined before the Extractor
- **Context**: Memory merge (M1) consumes NLU output, but the Extractor is M3.
- **Decision**: `nlu/schema.py` now holds only the domain `NLUResult` and its vocabulary enums. Identity values
  stay raw there (`FieldMention(raw, source)`); memory merge normalizes them with `today` from the session
  settings. The wire schema and `to_domain` come in M3.
- **Consequence**: `sop/` and `memory/` may import `nlu.schema`; it imports nothing from `llm/`.

## D13. Status `Literal`s from §9.1.1 are `StrEnum`s
- **Context**: The spec writes identity, authorization and consent statuses as `Literal[...]`; CLAUDE.md requires
  `StrEnum` for statuses.
- **Decision**: `IdentityStatus`, `VerifiedAs`, `AuthorizationStatus`, `ConsentStatus`, `ValueSource`,
  `ActionKind` and `VerifyStage` are enums with the same string values.
- **Consequence**: Same serialized values; no magic strings.

## D14. Identity check returns events instead of writing them
- **Context**: A policyholder pass completes verification, a representative pass doesn't (§7.5).
- **Decision**: `run_identity_check(identity, caller_role, ...)` returns a new `IdentityState`, the outcome and
  events. An unknown role counts as policyholder (A1). `matched_fields` stays in the outcome for the audit trace
  and never enters events.
- **Consequence**: The M2 handler just appends the events and decides the transition.

## D15. The anti-probing signature includes the policy number
- **Context**: V2 compares "a hash of the sorted field=value pairs"; the spec doesn't say whether the policy number
  is part of it.
- **Decision**: It is. A new policy number changes the candidate set (V5), so it counts as new information.
- **Consequence**: A caller can trigger one re-evaluation by changing only the policy number; each one still
  counts toward the lockout if it fails.

## D16. When a question is deferred (spec is silent)
- **Context**: §9.1.2 defers questions "the current phase can't answer" but NLU questions carry no category.
- **Decision**: Questions are deferred when the phase is VERIFY_ID, or RESOLVE_INTENT with no selected claim, and
  the same message has an intent for a path that needs a claim (confidence ≥ 0.5). General questions are never
  deferred. Duplicates (case-insensitive) are dropped.
- **Consequence**: "Why was it denied?" before verification is answered right after; "what is an EOB?" is answered
  immediately from general knowledge.

## D17. Identity memory is frozen after it leaves UNVERIFIED
- **Context**: "Write memory always" (§9.1.2) vs. a verified or locked session never re-verifying (V4, INV-7).
- **Decision**: Merge stops writing identity fields, declined fields and the policy number once the status is
  IDENTITY_VERIFIED, VERIFIED or LOCKED. Everything else keeps merging.
- **Consequence**: Later corrections can't change who the session belongs to.

## D18. `raw_mentions` are built by code
- **Context**: `CaseHints.raw_mentions` are "summaries of the caller's words".
- **Decision**: Built deterministically from this turn's stated hints (e.g. "denied healthcare claim, January"),
  never from free LLM text or record data.
- **Consequence**: Safe to show before verification (INV-3); wording is plain.

## D19. `PolicyDecision` is a frozen dataclass
- **Context**: It references `SessionState`, while `memory/state.py` imports event types from `sop/directive.py`.
- **Decision**: A frozen dataclass with a type-only import, instead of a Pydantic model.
- **Consequence**: No import cycle; it is an internal return value and never serialized as a whole.

## D20. Name normalization details
- **Context**: §8.1.1 says "strip punctuation" without details.
- **Decision**: Apostrophes are removed (O'Brien → obrien); other punctuation and symbols become spaces
  (Ya-Wen → ya wen). A single comma swaps "Last, First". A numeric date whose first part is over 12 is read as
  day-first (15/03/1985); otherwise mm/dd.
- **Consequence**: Caller input and record values normalize the same way, so matching stays exact (no fuzzy match).
