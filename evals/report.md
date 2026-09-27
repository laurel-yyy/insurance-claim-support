# Scenario eval report

Runs per scenario: 1. Models: extractor and selector on Haiku 4.5, responder and summary on Sonnet 5.

| Scenario | Passed | Pass rate | Avg latency per turn | Calls | Input tokens | Output tokens | Failed assertions |
|---|---|---|---|---|---|---|---|
| alias_name_yaven_li | 1/1 | 100% | 5.1 s | 2 | 8,365 | 307 |  |
| cross_record_mix | 1/1 | 100% | 3.9 s | 2 | 7,775 | 274 |  |
| document_alternatives_exhausted | 1/1 | 100% | 6.4 s | 8 | 40,891 | 1,707 |  |
| failed_verification_lockout | 1/1 | 100% | 4.8 s | 6 | 22,567 | 862 |  |
| frustrated_before_verification | 1/1 | 100% | 6.8 s | 6 | 24,647 | 1,021 |  |
| live_agent_request_midflow | 1/1 | 100% | 5.4 s | 4 | 17,544 | 682 |  |
| margaret_happy_path | 1/1 | 100% | 7.0 s | 9 | 38,782 | 1,823 |  |
| margaret_skip_email | 1/1 | 100% | 6.1 s | 7 | 26,307 | 1,177 |  |
| medical_advice | 1/1 | 100% | 5.8 s | 4 | 21,474 | 742 |  |
| national_id_ma_tian | 1/1 | 100% | 6.1 s | 4 | 20,780 | 797 |  |
| off_topic_retries | 1/1 | 100% | 4.2 s | 6 | 23,776 | 977 |  |
| partial_answers_and_clarifications | 1/1 | 100% | 5.3 s | 10 | 40,405 | 1,545 |  |
| prompt_injection | 1/1 | 100% | 5.3 s | 4 | 16,140 | 691 |  |
| refuse_id_use_phone_email | 1/1 | 100% | 4.4 s | 4 | 16,610 | 668 |  |
| representative_consent_approved | 1/1 | 100% | 5.6 s | 6 | 26,856 | 953 |  |
| representative_consent_timeout | 1/1 | 100% | 4.9 s | 12 | 48,505 | 1,793 |  |
| representative_not_authorized | 1/1 | 100% | 4.9 s | 2 | 7,914 | 340 |  |
| ungrounded_question | 1/1 | 100% | 6.6 s | 4 | 21,407 | 809 |  |

**Total:** 18/18 runs passed. 100 LLM calls, 430,745 input and 17,168 output tokens, estimated cost $0.73.

Calls by component: {"extractor": 49, "responder": 49, "summary": 2}.

## Tuning

| Run | Passed | Estimated cost |
|---|---|---|
| Baseline | 16/18 | $0.73 |
| Targeted rerun of the two failures | 2/2 | $0.11 |
| Final | 18/18 | $0.73 |

**Failures in the baseline and their root cause.** `margaret_happy_path` (turn 3) and `margaret_skip_email` (turn 2)
stayed in PROCESS_CASE after "Thanks, that's all." / "That's everything, thank you." The traces showed the
extractor labelled both as `thanks` only, never `done`, and left `confirmation` empty while "anything else?" was
open. The policy correctly waited. **Layer: extractor prompt wording.** The prompt listed `done` without defining
it. Fix: rule 18 now defines `done` (closing phrases; a closing thanks is both thanks and done; "ok, thanks" is
thanks only; a closing reply to ANYTHING_ELSE is also confirmation "no"), plus one example with ANYTHING_ELSE open.
A related policy guard was added so "No, that's all" right after an auto-resolved claim closes the case instead
of being read as "not that claim" (`deny` together with `done` isn't a rejection). No assertion was changed and no
scenario text appears in code.

Trace scan of the final run: 49 turns, 0 LLM fallbacks, 0 guard blocks, 0 extraction conflicts, 0 failed actions.
The responder answered every question from prefetched grounding, without tool calls.
