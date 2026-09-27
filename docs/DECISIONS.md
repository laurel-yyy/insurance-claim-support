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

## D15. The anti-probing signature covers every input to `evaluate_identity`
- **Context**: V2 compares "a hash of the sorted field=value pairs"; the spec didn't say whether the policy number
  is part of it. Reviewed and confirmed.
- **Decision**: The rule is "re-evaluate when the inputs to `evaluate_identity` change". The signature covers the
  valid PII values plus the policy number, because V5 uses it to choose the candidate set. The policy number is
  normalized to letters and digits only (`pol 9921` = `POL-9921` = `POL9921`), with the same function on both
  the caller's value and the record, for both the signature and the V5 lookup. A failure caused by changing only
  the policy number costs an attempt like any other. SPEC §8.1.1 and V2 are updated to say this.
- **Consequence**: A caller who mistyped only the policy number can correct it and pass. Cycling policy numbers
  only burns attempts, and the number never counts toward the 3 matches, so there is no probing gain. Restating
  the same number in another format neither re-evaluates nor misses the lookup.

## D16. When a question is deferred — superseded by D21
- **Context**: The first M1 version deferred every question in a turn that carried a confident claim-related
  intent.
- **Decision**: Replaced by per-question labels (D21). It broke on mixed turns ("What's an EOB, and why was my
  claim denied?"): it either deferred the general question for no reason or let the account question reach the
  responder during VERIFY_ID.
- **Consequence**: See D21.

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

## D21. Questions carry a kind from the Extractor
- **Context**: Whether a question needs account data is a language judgment, and a turn-level rule fails on
  mixed turns (D16).
- **Decision**: `NLUResult.questions` is a list of `Question(text, kind)`, with `QuestionKind` = account, general,
  process or out_of_scope. The Extractor fills it in M3 through `QuestionWire` (both fields required, so it stays
  within the §10.2 schema limits). Memory merge defers `account` questions (and ones with a missing kind) until
  verification completes, and in RESOLVE_INTENT until a claim is selected. `general`, `process` and
  `out_of_scope` are never deferred; the M2 policy answers or declines them in any phase (process answers from
  the reasons library, out-of-scope per §9.2). The routing `path` of a deferred question comes from the best
  claim-related intent in the same message with confidence ≥ 0.5, else none. SPEC §9.1.2, §10.3.1, extractor rule
  10 and the few-shot examples (plus a new mixed-turn example) are updated.
- **Consequence**: A wrong label can't leak data (INV-2 and G1 still hold); it only affects helpfulness.

## D22. The ambiguous P91 claims need different documents
- **Context**: CL-9101 and CL-9102 both needed documents with no dedicated guidance, so both would fall back to
  the general guidance and produce identical bundles. Building guidance for the wrong candidate after
  CHOOSE_CLAIM would then pass every test.
- **Decision**: CL-9101 now needs `["x-ray images", "treating provider office note"]`; CL-9102 keeps
  `["referral letter"]`. SPEC §6.6 is updated.
- **Consequence**: The two bundles differ, "the X-ray one" and "the referral one" still work for ClaimSelector,
  and CL-9101 reaches K1's exact-match branch, which the starter data never does. A repository test guards this
  now. M2 adds the guidance tests: each selected P91 claim yields its own bundle, plus inline key lists for K1's
  remaining branches (an ambiguous superset returns no match; singularization).

## D23. `NLUResult.text` and `TurnDirective.grounding`
- **Context**: K4 phrase triggering needs the caller's words, and the ContextBuilder (M4) doesn't see this turn's
  NLU, so it can't know which topics fired.
- **Decision**: `NLUResult.text` carries the raw message. `TurnDirective.grounding` is a `GroundingRequest` with
  names only: triggered topics, background topics, whether to use the follow-up fallback, documents needing
  alternatives, and topics skipped by K5 (an unknown placeholder), which the caller logs. M4 renders the text into
  grounding; the directive never carries record text. SPEC §7.5 is updated.
- **Consequence**: `sop/guidance.py` stays pure (no logging); retrieval is decided once, in the policy.

## D24. ClaimSelector output arrives as an observation
- **Context**: The selector is an LLM call (M3), but `decide()` is pure.
- **Decision**: The orchestrator calls it only while a CHOOSE_CLAIM question is open and passes the result as
  `Observations.claim_selection`. The policy accepts it only if the `case_id` is one of the listed candidates (not
  merely one of the caller's claims) and the path is a claim path; otherwise it's ignored. A case ID the caller
  states resolves deterministically without the selector. SPEC §11.3 is updated.
- **Consequence**: The LLM can only pick among options the code offered (BOUNDED).

## D25. Planned actions assume success; failures swap in a template
- **Context**: The executor runs after `decide()`, so the policy can't know whether an action succeeded.
- **Decision**: Sending the email moves the session to ENDED and a transfer to ESCALATED in the same turn. The
  directive tells the responder to report success only if the matching event (`EMAIL_SENT`, `ESCALATED`,
  `CONSENT_REQUESTED`) is present. On `ACTION_FAILED`, M4 uses `templates.ACTION_FAILURE_TEMPLATES`. The consent
  request is different: the state moves to *pending* only when the executor confirms the request was sent.
- **Consequence**: A reply never claims a side effect that didn't happen.
- **Amended by D38**: the summary email no longer ends the session before the result is known.

## D26. General and process questions are answered in place
- **Context**: `general_insurance_question` needs no claim.
- **Decision**: `general` and `process` questions go to `answer_now` in any phase. They don't make RESOLVE_INTENT
  enter PROCESS_CASE; `infer_path` considers only claim paths.
- **Consequence**: PROCESS_CASE always has a selected claim.

## D27. Persuasion is counted only at VERIFY_ID gates
- **Context**: §9.3.4 defines the budget for resisting "the current gate".
- **Decision**: Resistance is a refusal, a complaint ("just tell me") or frustration/anger of intensity ≥ 2 while
  in VERIFY_ID. Progress (a new field captured or a phase change) resets it. At `PERSUASION_MAX` the agent offers a
  transfer once; resisting again after that offer transfers (`PERSUASION_EXHAUSTED`). A "no" to the offer means
  "continue here" and isn't counted.
- **Consequence**: Complaints after verification never trigger an automatic transfer; the negative-emotion rule
  (offer after 3 strong turns) still applies there.

## D28. Detecting "no, not that one"
- **Context**: §8.2.5 has no pending question for an automatically resolved claim.
- **Decision**: A `deny` act with no open question, on the caller's first turn after a claim was resolved
  automatically, excludes the claim and returns to RESOLVE_INTENT. Hints stated this turn that contradict the
  selected claim (case ID, type or status) also return to RESOLVE_INTENT. The auto-resolved entry turn doesn't set
  ANYTHING_ELSE, so a "no" there isn't read as "I'm done".
- **Consequence**: Rejections are caught right after auto-resolution; later corrections work through hints.

## D29. Turn-level signals belong to the phase the turn started in
- **Context**: §7.4 lets one message pass several phases.
- **Decision**: "done", "deny" and yes/no answers are used only by the handler of the phase where the turn began,
  and a yes/no only if that phase asked the question and the kind matches (`TurnContext.answer()`, consumed once).
  The open question is cleared at the start of each turn; a handler re-asks when it still needs an answer.
- **Consequence**: Only the latest offer can be answered (INV-6); a hop can never reuse a confirmation.

## D30. Deferred questions are marked answered by the orchestrator
- **Context**: §8.2.7 marks them answered "after the directive is delivered successfully".
- **Decision**: The policy lists unanswered ones in `answer_now` whenever PROCESS_CASE runs.
  `memory.merge.mark_deferred_answered()` is called by M4 after a non-fallback reply.
- **Consequence**: A fallback reply doesn't lose them.

## D31. When the policy asks "anything else?"
- **Context**: §8.3.8.
- **Decision**: A PROCESS_CASE turn that stays in the phase ends with it (ANYTHING_ELSE), except on the
  auto-resolved entry turn (D28) and when a live-agent offer is open. A "no" moves to POST_PROCESS; a `done` act
  moves directly.
- **Consequence**: The caller can always close the case in one step.

## D32. Module layout additions
- **Context**: §5 lists the handler files, but they need shared types, and VERIFY_ID has two sub-flows.
- **Decision**: Added `sop/handlers/base.py` (turn context, directive parts, step result, `PolicyConfig`),
  `sop/handlers/verify_rep.py` (authorization and consent, split from `verify_id.py` for size) and
  `sop/handlers/terminal.py` (ESCALATED/ENDED). `EmotionStrategy` lives in `sop/directive.py` so `EmotionPlan` is
  typed without an import cycle. `PolicyEngine` is wired in `container.py`.
- **Consequence**: Every module stays under ~220 lines.

## D33. Failed authorization and ended consent are final for the session
- **Context**: A4 says never reveal whether other representatives exist.
- **Decision**: Once a representative is `not_authorized`, later name or relationship changes aren't re-checked.
  Consent `timeout` or `declined` is also final; later turns repeat the explanation and the live-agent offer.
- **Consequence**: A caller can't probe the representative registry by trying names.

## D34. Resolver details the spec leaves open
- **Context**: §8.2.2.
- **Decision**: A claim with an unknown status (`OTHER`) gets neither a status match nor a penalty. Candidates at
  ≥ 2 that don't meet the unique rule (including a single weak candidate) are offered through CHOOSE_CLAIM for the
  caller to confirm. Keyword overlap is token-based, with naive singularization and a small stopword list.
- **Consequence**: A lone type-only hint ("my auto claim") asks for confirmation instead of assuming.

## D35. DOB format example changed
- **Context**: §9.3.2 suggested the example "March 15, 1985", which is a starter policyholder's real DOB.
- **Decision**: Fallbacks ask for "your full date of birth: month, day and year" with no example date. SPEC §9.3.2
  is updated.
- **Consequence**: No fixture value appears in business logic (INV-10) or in pre-verification replies.

## D36. The formatter owns line length
- **Context**: `ruff format` wraps code at 110 but never splits string literals, so E501 flagged long strings.
- **Decision**: E501 is ignored in the ruff config; the formatter still enforces the limit for code.
- **Consequence**: Long directive strings stay readable as single literals.
- **Note**: ruff also formats Python code blocks inside Markdown. `docs/` is now excluded from ruff, because
  earlier `ruff format .` runs (starting in M0, before the first commit) re-aligned comments in SPEC.md code blocks.
  Those changes are whitespace-only and change no content.

## D37. A global live-agent offer wins the open question
- **Context**: Off-topic, persuasion and emotion rules can offer a transfer while a handler also asks something.
- **Decision**: The global offer replaces the handler's pending question for that turn; the handler re-asks next
  turn.
- **Consequence**: The escalation path is never hidden behind another question.

## D38. A failed summary email stays in POST_PROCESS and offers a retry
- **Context**: Reviewed after M2: ending the session when the send fails left the caller with no way to get the
  summary. The policy is pure and runs before the executor, so it can't know the result in `decide()`.
- **Decision**: A yes to the offer plans `SEND_SUMMARY_EMAIL` and stays in POST_PROCESS. After the executor runs,
  the orchestrator (M4) calls `PolicyEngine.settle(state, [ActionResult(...)])`, which is also pure:
  - success → ENDED with the closing directive (`SESSION_ENDED`);
  - failure → stays in POST_PROCESS, says the email couldn't be sent and re-offers it to the same address (the
    file address or the confirmed alternate), with quick replies `Try again` / `No thanks`. A retry needs a new
    explicit yes (INV-6);
  - a second failure in a row → `EMAIL_SKIPPED(reason=send_failed)` → ENDED, pointing to the member portal, so the
    session can't loop.
  `settle()` returns None when no email was sent that turn, and the original directive stands. The retry limit
  (2 attempts) isn't in the spec; it's the conservative choice.
- **Consequence**: M4's orchestrator must call `settle()` after `executor.run()` whenever the decision planned a
  send, and use the settled directive. SPEC.md is not edited; this entry is the record.

## D39. Extractor few-shot examples use fictitious values
- **Context**: The §10.3.3 prompt's examples use starter records verbatim (Margaret Chen's name, policy number,
  DOB, SSN last four and email; David Chen as her son). The system prompt goes into every extractor request,
  including pre-verification requests from other callers, so sending it verbatim would put policyholder record data
  into LLM requests before verification (INV-2) and fixture text into the codebase (INV-10).
- **Decision**: `agent/prompts/extractor.md` keeps the spec's rules and example structure exactly, with fictitious
  values (Jane Doe, POL-1234, 1990-04-12, 1234, jane.doe@example.com, Sam Doe, 555-010-0199, CL-5678).
  SPEC.md is not edited.
- **Consequence**: A test fails if any starter record value appears in the prompt, and the INV-2 extractor test
  scans a full pre-verification request for record data.

## D40. NLU wire models and conversion live in their own modules
- **Context**: §10.3.1 puts the wire schema and `to_domain` in `nlu/schema.py`, which would pass ~400 lines.
- **Decision**: `nlu/schema.py` keeps the domain models, `nlu/wire.py` holds the wire models and
  `build_nlu_wire()`, and `nlu/convert.py` holds `to_domain()` and the degraded result.
- **Consequence**: Every NLU module stays under ~200 lines.

## D41. Wire labels degrade per field
- **Context**: Structured output guarantees the shape but not enum casing; one bad label would otherwise fail
  the whole extraction and force degraded mode.
- **Decision**: Before validation, enum strings are lowercased. Unknown items in enum lists (dialog acts, declined
  fields, follow-up topics) are dropped. Unknown scalars take a safe default: `id_kind`/`claim_status`/intent
  `path` → `none`, `caller_role` → `unknown`, `scope` → `in_scope`, `emotion` → `neutral`, `confirmation` →
  `unclear` (never a yes), question `kind` → `account` (D21). A missing required field still fails validation and
  triggers the retry and then degraded mode.
- **Consequence**: A mislabeled field can't turn into a confirmation or bypass a gate; the rest of the extraction
  is kept.

## D42. Structured output is validated inside the LLM client
- **Context**: §10.1 has `output_schema: type[BaseModel]` and `parsed`.
- **Decision**: `LLMRequest.output_model` is the Pydantic model. The client sends
  `output_config.format` with the SDK's `anthropic.transform_schema()` (which closes objects and moves
  constraints into descriptions) and validates the reply with the same model, so the lenient label handling (D41)
  runs there. A refusal or `max_tokens` stop with a schema, or output that fails validation, raises
  `LLMOutputError`. `llm/` stays business-agnostic.
- **Consequence**: Callers only ever see a validated wire object or an `LLMError`.

## D43. Extraction problems become policy events
- **Context**: Degraded mode (`LLM_FALLBACK`) and regex/LLM disagreements (`NLU_CONFLICT`) must reach the trace
  and the directive (§10.3.2).
- **Decision**: `NLUResult` carries `degraded` and `conflicts`; the global rules emit `LLM_FALLBACK
  {component: extractor}` plus a "ask the caller to rephrase if unclear" instruction, and `NLU_CONFLICT {fields}`.
  The Extractor itself never raises for LLM problems and never writes events.
- **Consequence**: All events for a turn come from `decide()`, in one timeline.

## D44. Extractor request contents and model settings
- **Context**: §10.3.3, §10.7.
- **Decision**: The system prompt is rendered single-pass (a value containing `{phase}` is never re-expanded)
  with today's date, the phase, the open question kind (e.g. `OFFER_SUMMARY_EMAIL`), the last agent message and
  the follow-up topic names with up to 3 trigger phrases each. Messages are the last 6 turns before the current one
  (starting with a caller turn) plus the current message. The extractor and selector use `EXTRACTOR_MODEL` as
  configured (`claude-haiku-4-5-20251001` per §14.1) with no effort and no thinking setting (Haiku 4.5 rejects
  effort); max tokens are 1500 and 400. `Container.make_llm(api_key)` builds a client for the server key or a key
  entered in the UI (M6).
- **Consequence**: No record data enters extractor requests before verification; agent replies in the history
  have passed the output guard (M4).
