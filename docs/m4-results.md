# M4 results: bounded model assistance (drafting only)

Date of runs: 2026-11-02. Development data only (validation split of the M3 run `m3-dev-8`). Every number below was read from the files under `artifacts/m4/` named in each section; counts are written as numerator over denominator.

**Read this first: what "accepted" means.** "Accepted" means the draft passed structural checks: references, amounts, ids, dates, day counts, category spellings and action words. It does NOT mean the prose is true. Review round 1 (blocker 1) gives concrete counterexamples that the validator would accept: on an F06 (duplicate payment) packet, a draft whose summary says "No duplicate payment occurred." passes; on an abstained packet, a draft saying "The investigation confirmed a duplicate payment." passes; and a claim can negate the finding it cites while keeping valid references, because coverage only requires the finding's identifier, not its assertion. "Claims bound to findings" is therefore a structural count of references, not a count of verified truths. Wherever this document says "accepted", "validity" or "measured validity", read it in this sense. These counterexamples come from a static code review; they were not run.

## 1. What was tested and what was not

Tested: whether a language model can turn a finished deterministic evidence packet into a short plain-English explanation (a summary, one claim per supported finding, and a next step), where a rule-based validator checks every checkable item against the packet.

Not tested:

- **Adjudication or follow-up retrieval by a model.** Not tried. The remaining investigator errors are label-ambiguous legitimate-variant swaps (decision log D148), so a model has no reliable signal to improve on and no ground truth that could show an improvement.
- **Jev.** Not tested.
- **The diagnosis.** The model never changes it. The diagnosis, evidence and recommendations come from the deterministic investigator (decision log D148, D149).
- **Usefulness to a human.** No practitioner has rated any draft.

The drafting stage reads only the packets file. It reads no labels, links, keys or snapshot.

## 2. Setup

| Item | Value | Source |
|---|---|---|
| Models | `claude-haiku-5-5` (smoke, stage 2, stage 3), `claude-sonnet-5-5` (stage 2 only) | `artifacts/m4/stage*/draft-stats.json` |
| Effort | `low` | `docs/m4-trial-plan.md` |
| Output cap | 1,500 tokens (smoke); 2,000 tokens (stages 2 and 3, set with `--max-output-tokens`) | trial plan, Deviations section |
| Prompt | `draft_v1`, sha256 `e5113ab8b43afe80d2f38c53d73ab067b7db651e50a54b4852cb82d02503c7ce` (identical in all five `draft-stats.json` files) | `artifacts/m4/stage*/draft-stats.json` |
| Inputs | Validation packets of `m3-dev-8`: 416 rows, of which 215 drafted (139 answered + 76 abstained) and 201 skipped without a call (out of scope or failed) | `artifacts/m4/eval-declared.json` |
| Packets sha256 | `5b301df10c6310893bc4db1c211790c692c67025f4277091a254d7407e0dfe74` | `artifacts/m4/eval-declared.json`, `eval-rescored.json`, and the `m3-dev-8` run manifest |
| Budget | M4 cap $8 of the $25 protocol cap (leaves at least $17 for M7). Total spent: $0.5589550 over 256 ledger entries (computed, see pricing note below) | trial plan; `artifacts/m4/spend-ledger.json` |

Pricing provenance (review finding 13). Rates used: Haiku 5.5 $0.10 per million input tokens and $0.50 per million output tokens (prompts up to 100,000 tokens); Sonnet 5.5 $2 input and $10 output per million tokens. Source: the official pricing page https://platform.claude.com/docs/en/about-claude/pricing, checked 2026-11-02. Every dollar figure in this document is computed from the recorded token counts at those rates; none is read from an invoice or billing statement, so treat them as estimates under those rates. The page content was not archived with the run, so the rates cannot be re-checked from the artifacts alone.

The 256 ledger entries are 1 (smoke) + 20 (Haiku stage 2) + 20 (Sonnet stage 2) + 215 (stage 3). Of them 233 are Haiku `ok`, 20 are Sonnet `ok` and 3 are Haiku `max_tokens`.

Validator rules (`src/reconcile/models/validate.py`), summarised. A draft is rejected if any of these fails:

- Shape and length: valid structure; summary at most 600 characters; each claim text and the next-step text at most 300 characters; at most 20 claims.
- Finding binding: each claim names a real finding of the packet and cites only evidence ids and calculation ids that belong to that finding.
- Amounts, dates, ids and day counts in the text must appear in the packet, with the packet's currency.
- Category: no diagnosis category other than the packet's own may be named.
- Action: the next-step action must be an allowed one; money-moving verbs ("pay", "repay", "transfer", "approve", "delete", "release", "refund", "reverse", "void", "cancel", "reissue", "recall", "write-off") are allowed only if the packet's own text uses the word.

Accepted drafts are delivered as `packet_with_unvalidated_prose`: the packet plus the draft, with the prose beyond the checked patterns labelled unvalidated.

Weakness of the value and id checks (review finding 3, major). Amounts, dates, ids and day counts are checked for membership anywhere in the packet, not bound to the asserted record, sign or currency. Concrete bypasses: `abs()` lets a positive amount stand for a negative difference; a prefix currency ("USD 1000.00") is read as a bare amount and can pass against an EUR-only packet; a value belonging to one record can be attributed to another; zero is always allowed as a day count, and limits or search counts pass as elapsed days. Free-text packet fields (symptoms, limitations) are part of the whole-packet membership check, so text in them can widen the allowlists.

## 3. Results

### 3.1 Stage 1: smoke test (not scored)

One call, Haiku 5.5, request `req_6de6a37dd02cb300221dba8c4a497311` (`artifacts/m4/select-smoke.txt`). Result: 1/1 accepted, $0.0012912, 5,657 input and 1,451 output tokens, latency 7.26 s (`artifacts/m4/stage1-smoke/draft-stats.json`, `drafts.jsonl`). It used 1,451 of the 1,500-token cap, mostly adaptive thinking, which is why stages 2 and 3 used 2,000.

### 3.2 Stage 2: model choice (20 packets per model)

Same 20 packets for both models (`artifacts/m4/select-stage2.txt`).

| | Haiku 5.5 | Sonnet 5.5 |
|---|---|---|
| Accepted | 18/20 | 18/20 |
| Rejected | 2/20 | 2/20 |
| Cost | $0.0178579 | $0.349958 |
| Input / output tokens | 92,119 / 17,292 | 92,119 / 16,572 |
| Latency p50 / p95 | 3.95 s / 6.32 s | 6.88 s / 10.31 s |
| Claims bound (accepted drafts) | 65 | 66 |

Both models were rejected on the same two packets (`req_31984ca79652da606b56247e6542caca` and `req_6b2346a678e5600eeb4eee8ae4d4d70c`), each time for "action word 'pay' is not a packet recommendation". Choice rule, fixed before any paid call: higher acceptance rate; if the two differ by at most 2 of 20, the cheaper model. They differ by 0 of 20, so the rule picks Haiku 5.5, at about one twentieth of the cost ($0.0178579 against $0.349958).

### 3.3 Stage 3: full validation run, Haiku 5.5, 215 attempted

Two result sets are shown side by side. **They are different kinds of evidence.** The declared column is the pre-registered result: the validator as it stood when the run was made. The re-scored column re-applies a corrected validator, written after seeing the rejected drafts, to the same recorded model responses (no new model calls; `artifacts/m4/rescore-ledger.json` is replay accounting at 20:49, about one second for 212 entries, not new spend). The re-score is post-hoc and was not allowed by the plan; the declared column is the primary result. **The re-scored figure is LESS trustworthy than the declared one, not more** (review blocker 2): the post-hoc fix lets action words that appear in the packet's limitations through, so a prohibition such as "do not pay again unchecked" now puts "pay" on the allowlist, and a draft whose next step says "Pay the vendor again now." would pass. The 12 drafts that moved to accepted for the word "pay" were admitted by this rule, which also allows the opposite of what the limitation says. Prerequisites, negation and unresolved assumptions are never enforced. It also lets instruction-like text in a limitation add words to the validator's allowlist. This is from static review, not a run. See `docs/m4-trial-plan.md`, Deviations.

| | Declared (primary) | Re-scored (post-hoc) |
|---|---|---|
| Source | `artifacts/m4/eval-declared.json`, `stage3-claude-haiku-5-5/` | `artifacts/m4/eval-rescored.json`, `stage3-rescored/` |
| Accepted / attempted | 179/215 (83.3%) | 192/215 (89.3%) |
| Answered: accepted / attempted | 116/139 (20 rejected, 3 failed) | 129/139 (7 rejected, 3 failed) |
| Abstained: accepted / attempted | 63/76 (13 rejected, 0 failed) | 63/76 (13 rejected, 0 failed) |
| Rejected | 33 | 20 |
| Failed (max_tokens) | 3 | 3 (listed as "other") |
| Claims bound to findings | 572 over 179 accepted drafts (3.20 per draft) | 598 over 192 accepted drafts (3.11 per draft) |

Rejection and failure kinds:

| Kind | Declared | Re-scored |
|---|---|---|
| action_word | 12 | 0 |
| amount | 1 | 0 |
| category | 4 | 4 |
| finding_binding | 13 | 13 |
| length_or_schema | 3 | 3 |
| max_tokens (failed, no draft) | 3 | 3 (as "other") |

Exactly 13 drafts changed status between the two columns, all from rejected to accepted: 12 for the action word "pay" and 1 for the amount "7590.62 ACH". Nothing moved from accepted to rejected.

Cost, tokens and latency:

| | Declared | Re-scored |
|---|---|---|
| Total stage 3 cost | $0.1898479 | $0.1848021 |
| Per attempted packet | $0.00088301 (0.1898479/215) | $0.00085954 |
| Per accepted draft | $0.00106060 (0.1898479/179) | $0.00096251 (0.1848021/192) |
| Input / output tokens | 994,149 / 180,866 | 973,691 / 174,866 |
| Latency p50 / p95 | 3.38 s / 6.82 s over 212 timed calls | same |

The re-scored cost and token figures leave out the 3 `max_tokens` calls (0.1898479 - 0.1848021 = 0.0050458, which equals the 3 `max_tokens` ledger entries; the token gap is 20,458 input and 6,000 output). The money for those calls was still spent. The true stage 3 spend is the declared $0.1898479 in both columns, so the re-scored per-attempted and per-accepted costs are slightly understated. Latency is identical because the responses are the same; the 3 failed calls have no recorded latency. Latency discrepancy (review finding 11): the two files use different percentile definitions. `src/reconcile/runs/draft.py` `_percentile` takes sorted index `int(q * n)`, giving p50 3.44 s (and p95 6.82 s) in the run's own `draft-stats.json`. `evaluation/metrics.py` `nearest_rank` takes the value at 1-based rank `ceil(p/100 * n)`, giving p50 3.38 s (and p95 6.82 s) in the evaluator output. For 212 timed calls these pick different median positions (index 106 against rank 106, i.e. index 105). The evaluator's nearest-rank values are the ones in the tables above. I did not re-run either computation to confirm the exact positions; the cause is read from the code. For two values [1, 3] the runner gives 3 and nearest rank gives 1.

The 3 `max_tokens` failures: `req_70a9f97d75851e7b28a2060e45752f9f`, `req_766eed48d50204b51bd38aa0bc7e5ee1` and `req_b80bdc5cf0bf0ef4f05a489f6ce0685d`. Each used the whole 2,000-token output cap (input 6,025, 7,596 and 6,837 tokens), so no draft exists for them.

Whole-run spend, all stages: $0.5589550 against the $8 M4 cap (7.0%, which is 0.5589550/8) and the $25 protocol cap (2.2%).

## 4. Ablation: model drafting on versus off

The protocol lists "model assistance on versus off (M4)" and an "oracle-evidence diagnostic". Plainly:

- **On versus off.** Drafting writes a separate artifact (`drafts.jsonl`). It cannot change the diagnosis, evidence or recommendations, because the model never decides them. The packets file digest recorded in both evaluator outputs is `5b301df1...e0407dfe74`, the same as the `m3-dev-8` manifest value, and I re-computed the sha256 of `reconcile-m3/artifacts/dev-runs/m3-dev-8/inference/validation/packets.jsonl` and it matches. The primary metric is therefore unchanged by construction; the difference on versus off is 0 by design, not a measured improvement. The run did not store a before-and-after digest pair of its own; the match is against the manifest.
- **Oracle-evidence diagnostic.** Does not apply. It would test a model that performs diagnosis or retrieval given perfect evidence. No model performs diagnosis or retrieval here, so there is nothing to run, and none was run.

## 5. Representative examples

All data is synthetic with opaque tokens. Quotes come from `artifacts/m4/replay.jsonl` (the recorded model output, looked up by each row's `request_key`) and, for accepted drafts, `artifacts/m4/stage3-claude-haiku-5-5/drafts.jsonl`.

**Accepted, answered** (`req_003195680efb086cff6d54d9dc7d92b6`, 6 claims bound). Summary: "The packet's diagnosis is legitimate_pending_settlement and it is answered. The payment of 1224.52 USD is within the ACH clearing window (1 business day against a limit of 3), its records are present, allocations and invoice totals reconcile, approval preceded payment, and the payment journal matches. No recommended action is given. ..." One claim: "The 1224.52 USD ACH payment dated 2026-06-29 is 1 business day old, within the ACH clearing limit of 3. Searches through 2026-06-30 found no matching bank debit, so settlement is pending." Next step action `none`. Validator: no reasons.

**Accepted, abstained** (`req_02b6137da8abe31bf8c96b05bd4e7b6b`). Summary: "The investigation abstained. The payment of 5832.06 USD matches its bank debit and its allocation to the invoice, but the payment journal shows 6047.85 USD, a difference of -215.79 USD. No explanation for the journal difference was found, so no conclusion was reached and no recommendation was made." Next step `none`. Validator: no reasons.

**Rejected, finding_binding** (`req_237b9eec971713db7cd688e18f20508d`, an abstained packet). The claim for finding `invoice_approval_not_completed` cites `inv:d6c7a35f82b8c0866eb55f7c9a682715`. Validator reason: "claim 0: evidence 'inv:d6c7a35f82b8c0866eb55f7c9a682715' is not in finding invoice_approval_not_completed". The text itself says no approval was found for that invoice, so the claim may be true, but the cited id belongs to the packet, not to that finding. The rule is strict about the binding.

**Rejected, category** (`req_139bb051289979b217244582a0bb1e0b`). The summary says "The packet's diagnosis is legitimate_batch_payment, answered, ... Alternative considered by the packet: legitimate_weekend_clearing." Validator reason: "category 'legitimate_weekend_clearing' is not this packet's diagnosis". The model named the other legitimate variant that the packet itself lists as an alternative. This is the same label ambiguity that drives the investigator's remaining errors (decision log D148); the validator cannot tell naming an alternative from asserting it, so it rejects. All 4 category rejections name another `legitimate_*` variant (`legitimate_weekend_clearing` twice, `legitimate_due_date_payment` twice).

**Rejected, length/schema** (`req_19271d383daf286fa912852403331e8b`). The summary was 601 characters against the 600 limit. Validator reason: "summary must be a non-empty string of at most 600". The content was otherwise a correct F06 description with the packet's own recommendation. Another length case, `req_133e7358c1adb3d6b6e04f579df17eeb`, had a 309-character next-step text against 300. These reflect the length rule, not wrong content.

**Changed status in the re-score** (`req_0274c1eadf8d13c4f3fdee9d3f050477`, one of the 13). Declared: rejected, "action word 'pay' is not a packet recommendation". The offending text was the next step "...Do not pay again unchecked.", copied from the packet's own F12 limitation, so the word comes from the packet. The corrected validator lets action words that appear in the packet's limitations or assumptions through, and accepts the draft (declared rejected, re-scored accepted). Note that the same rule would equally accept "Pay the vendor again now." (section 3.3, blocker 2), so this acceptance shows the rule is unsafe, not that the draft is right. The same draft's summary says "the payment should not be repaid unchecked". "repaid" is not caught by the exact-word check (section 6), so this acceptance also shows the remaining gap.

## 6. Limits

- The validator checks listed patterns only (ids, amounts, dates, day counts, categories, action words, lengths). Prose beyond those patterns is unvalidated, which is why accepted drafts are delivered as `packet_with_unvalidated_prose`. "Claims bound to findings" counts structured claims, not true sentences.
- The action-word check matches exact words. Inflections such as "repaid" or "paid" pass; this was recorded, not tuned (trial plan, Deviations).
- Text ids are checked against the whole packet, not against the claim's own finding, so a text can name an id from another finding.
- No human has rated usefulness, readability or trust. Acceptance by the validator is not value to an analyst, and it is not evidence that the prose is true (see the note at the top).
- Development data, one run of one prompt (`draft_v1`), no repeat to measure variability. The 20-packet model comparison cannot separate the models: both 18/20.
- The validator was corrected after seeing the rejected drafts, which the plan did not allow (it allowed prompt or effort changes only). Two false positives were fixed: action words that appear in the packet's limitations or assumptions, and a payment method after a number ("USD 7590.62 ACH payment") read as a currency. The declared 179/215 stays primary; the re-scored 192/215 is post-hoc. See `docs/m4-trial-plan.md`, section "Deviations".
- Rejected drafts are stored only in `artifacts/m4/replay.jsonl`; the `drafts.jsonl` rows for them have `draft: null`.

## 7. Decision under the frozen protocol (proposed)

`evaluation/protocol.toml` `[model_assistance]` says a drafting-only path that cannot change the primary metric is kept only if a recorded practitioner review finds a usefulness benefit, and is otherwise removed. No such review exists. In addition, review round 1 shows the validator cannot establish that a claim is true (blocker 1), and the post-hoc action-word rule can admit the opposite of a limitation (blocker 2). An earlier version of this section proposed keeping drafting as an optional, off-by-default feature; that contradicted the frozen protocol (review finding 9) and is withdrawn. Off-by-default is not an exception.

PROPOSED outcome: remove model drafting from the product path. Keep the trial artifacts under `artifacts/m4/`, these documents, and a git tag of the code as research evidence. Drafting is not part of the headline claim. The final decision is left to the user at the M4 gate.

### Not fixed because removal is proposed

Other review round 1 findings, listed so a future design starts from them (one line each):

- 4 (major): the pre-call cost estimate (one token per three characters) is not an upper bound, so the spend cap is enforced only after the fact.
- 5 (major): billable failures can escape accounting (usage with no text, timeouts recorded at zero cost, replay-file write failure before ledger record); draft-row costs omit retried attempts.
- 6 (major): the cumulative budget is optional (a fresh ledger per output directory without `--ledger`), M4's $8 share is not enforced by default, and ledger check and record are unlocked across processes.
- 7 (major): replay goes through the same budget path as live calls and is charged as spend; refusals and truncations are not replayable, so rescoring turns them into `replay_miss` failures.
- 8 (major): the evaluator accepts duplicate rows and does not account for selected-but-not-attempted cases, so a stopped run can report 1/1 accepted.
- 10 (minor): no total per-case wall-time budget; each attempt has a 60 s timeout and retries add up.
- 12 (minor): draft rows, stats, replay and ledger files have no `schema_version`, and outputs do not record the validator revision or live-versus-replay mode.
