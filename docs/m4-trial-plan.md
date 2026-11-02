# M4 drafting trial plan (declared before any paid call)

**Status:** declared 2026-11-02, before the first live model call. The results go into `docs/m4-results.md`; this file is not edited after the first paid call except to append a dated "deviations" section.

## Question

Can a language model turn a finished deterministic evidence packet into a short explanation for an accounts payable analyst, with every checkable claim bound to the packet, at an acceptable cost? The model never changes the diagnosis (decisions D148, D149). Under the frozen protocol (`evaluation/protocol.toml` `[model_assistance]`), a drafting-only path is kept only if a recorded practitioner review finds a usefulness benefit. This trial measures validity and cost; it cannot establish usefulness.

## Inputs

- Packets: the validation packets of the M3 run of record `m3-dev-8` (`artifacts/dev-runs/m3-dev-8/inference/validation/packets.jsonl` in the M3 worktree; digest recorded in that run's manifest). Drafted: answered and abstained packets (139 + 76 = 215). Skipped without a call: out-of-scope and failed packets.
- The drafting stage reads only the packets file. It never reads labels, links, keys or the snapshot.
- Train and validation only. Nothing from `test` or `challenge_test`.

## Stages

1. **Smoke test (1 call).** Haiku 5.5 on the single drafted validation packet with the smallest sha256(request_id). Purpose: check credentials, the structured-output schema and cost accounting. Not scored.
2. **Model choice (20 packets per model).** The 20 drafted validation packets with the smallest sha256(request_id), with `claude-haiku-5-5` and with `claude-sonnet-5-5`, effort `low`, at most 2,000 output tokens, prompt `draft_v1`. Choice rule, fixed now: the model with the higher acceptance rate (drafts passing `validate()`); if the two differ by at most 2 of 20, the cheaper model.
3. **Full validation run.** The chosen model on all 215 drafted validation packets, same settings.

At most two predeclared design iterations are allowed (PLAN.md M4): each is a new prompt version (`draft_v2`, `draft_v3`), declared in a dated appendix to this file with its reason **before** it runs, and rerun on the stage 2 sample only, then on the full set if it is adopted. An iteration may change the prompt or effort, never the validator's rules.

## Measures

- Acceptance rate: accepted / attempted, and rejection reasons by kind (fabricated amount, unknown id, category, action, coverage, schema, length).
- Failures: refusals, max-token stops, transport errors.
- Claims bound to findings per accepted draft. All remaining prose is labelled unvalidated (`drafts.jsonl`).
- Cost: total USD from the spend ledger, per attempted packet and per accepted draft; tokens in and out.
- Latency p50 and p95 per call.
- Invariance: the packets file digest is unchanged after drafting (drafts never alter packets), so the primary metric is unchanged by construction; recorded, not assumed.

## Budget

- The protocol cap is $25 in total across M4 and M7. M4 may spend at most **$8**, leaving at least $17 for M7.
- One persistent ledger for all M4 stages: `artifacts/m4/spend-ledger.json`. `runs/draft.py` refuses any call that would cross the cap.
- Estimated cost: stage 2 under $0.50; stage 3 with Sonnet 5.5 about $2 to $5, with Haiku 5.5 well under $0.50 (about 2,800 input tokens per packet, output dominated by adaptive thinking). Prices verified on the official pricing page on 2026-11-02.

## Credentials and data handling

The key is read at run time from `~/.config/reconcile/anthropic_key` (outside the repository, mode 600) into the environment of the drafting command only; it is never printed or written. Packets contain opaque tokens and synthetic amounts only. Responses are recorded to a replay file under `artifacts/m4/` so results can be re-scored without new calls.

## Decision at the M4 gate

- If the acceptance rate is low or failures are frequent, drafting is removed.
- Otherwise drafting stays an optional, off-by-default feature, outside the headline claim, until a practitioner review records a usefulness benefit (protocol `[model_assistance]`). No practitioner review exists yet.

## Deviations (appended after the first paid call)

- 2026-11-02, after stage 1: the smoke test ran with the code default of 1,500 output tokens and used 1,451 of them (accepted, $0.0013, 7.3 s), mostly adaptive thinking. Stages 2 and 3 use 2,000 output tokens, the plan's stated maximum, set with the new `--max-output-tokens` flag (commit 68165a6). The stage 1 result is a smoke test only and is not scored.
- 2026-11-02, after stage 3: two validator false positives were found in the rejected drafts and fixed after the trial, which this plan did not allow (it allowed prompt and effort changes only). (1) Action words that appear in the packet's own limitations or assumptions, such as the F12 limitation "do not pay again unchecked", were rejected because only recommendation text was allowed. (2) A payment method after a number ("USD 7590.62 ACH payment") was read as a currency. The as-declared result (179 of 215 accepted) stays the primary result; a re-score of the same recorded responses with the fixed validator (192 of 215) is reported beside it as post-hoc. Known remaining gap: the action-word check matches exact words, so inflections such as "repaid" or "paid" are not caught; recorded, not tuned.
- 2026-11-02, after review round 1: the section "Decision at the M4 gate" said drafting would otherwise stay "an optional, off-by-default feature". That contradicted the frozen protocol (`evaluation/protocol.toml` `[model_assistance]`), which requires removal unless a recorded practitioner review finds a usefulness benefit; off-by-default is not an exception. Corrected: the proposed outcome is removal from the product path, keeping the trial artifacts, `docs/m4-results.md` and a git tag of the code as research evidence. See `docs/m4-results.md` section 7. The earlier text is left unchanged.
