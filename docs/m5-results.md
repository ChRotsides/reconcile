# M5 results: usable tool on real export formats

**Status:** evidence for the M5 gate. Everything here was measured on invented fixtures, public samples and our own machine. No real export has been read and no practitioner has used the tool.

## 1. What now exists

- A `reconcile` command with `data fetch`, `data prepare`, `investigate`, `batch`, `report` and `demo` (`docs/cli-guide.md`). Scoring stays `python -m evaluation.dev_run`.
- Self-contained HTML reports and JSON reports with plain-language explanations from fixed templates. No model and no generated text.
- Imports for a generic CSV bundle (with explicit date formats) and for saved Xero JSON with CAMT.053 statements.
- A run record that hashes every output; `report` refuses an unfinished or edited run.
- A six-case offline demo on invented data, report pages for the observable baseline, and a practitioner walkthrough kit (`docs/practitioner-walkthrough.md`).

## 2. Benchmark results did not move

Rerunning inference with the M5 code on the inputs of run `m3-dev-8` gives packets byte-identical to that run (checked with `cmp`) for train (1192) and validation (416), at commit fb23c83. So the M3 development results in `docs/results-card-m3.md` still describe this code, and M5 adds no new benchmark claim. Byte identity was checked at these inputs and limits only; a case near the time limit could behave differently, because the code now runs one extra query on the no-bank-row path.

## 3. What the real-format path does

Measured on invented Xero and CAMT.053 fixtures (`docs/input-mapping.md` section 10.3), as prepared, with references equal on both sides by construction:

| Outcome | Checks |
|---|---|
| Answered | F06 only |
| No conclusion, because approvals are not in either format | split, due date, weekend clearing, check clearing, batch (and then a missing payment journal) |
| No conclusion, because a counterparty is unknown | F12, pending |
| No conclusion, because fee journals cannot be recognised | F14, net fee |
| No conclusion, for other stated reasons | FX, fee row, reversal |

On these two formats alone, then, the tool can state one kind of exception. F12 needs counterparty accounts on both sides. All-clears need approval history, which neither format has. This is the intended behaviour under the project rule that missing evidence never becomes a conclusion, but it limits how useful the tool is on this pair of formats. A CSV bundle that supplies those fields reaches the other outcomes, as the demo shows.

**The largest unknown is unchanged:** whether payment references really match between an accounting export and a bank file (`docs/input-mapping.md` G1, assumption A029). Nothing measured here tests it, because the fixtures match by construction.

Public samples: the ING CAMT.053 sample is refused as a whole, because it states 9 entries with 1 present and its balances do not add up, and its entries parse separately. The Xero examples prepare, but they are receivables, so the supplier-payment route was not exercised by any public data (`docs/input-mapping.md` section 10.4).

## 4. Install and container

Our own measurement on the build machine, not an external user's experience. uv, Python 3.12 and caches were already present.

From a fresh `git clone`, following the README: `uv build --wheel`, `uv venv`, `uv pip install`, `reconcile demo`.

- Elapsed: under one second (0.44 s at commit fee9de9; rerun before the gate). The only package installed is `reconcile`.
- Demo outcomes: F06, F12, F14, net-fee all-clear, pending all-clear, and one no-conclusion case, as asserted in `tests/unit/test_demo.py`.
- Container: `deploy/inference/run_inference.sh` on the demo snapshot gives packets identical to the host run, with no network, read-only, and no CLI, adapters or demo in the image.
- Not tested: Windows (the batch runner uses `resource`), CI on GitHub (no remote), and any machine other than this one.

## 5. Review history

Three Codex `gpt-6-astra` rounds at high effort, the maximum `AGENTS.md` section 9 allows. Fixers reproduced each code finding with a failing test before changing code, except where noted in the fixers' reports, and the parent checked the fixes.

| Round | Result |
|---|---|
| 1 | 19 numbered findings plus a table of report sentences that overstated their checks. Paths to false F12 and false F06 on real-export input. All went to fixers; four fixes deliberately differ from the reviewer's suggestion (D171). |
| 2 | Most of round 1 fixed. 8 new or remaining findings (R2-A to R2-H), including a false F06 and a statement balance check that one currency could satisfy for another. All fixed (D172). |
| 3 | Round 2 mostly fixed. 2 new high findings (a fractional-second gap between statements; a reversed debit counted as settled) and one guide sentence. All fixed, and the fixes have not had a further independent review. |

Parent review also caught a later-cutoff bug in `batch --requests`, a misleading shared colour for exceptions and all-clears, and a false pass in the parent's own verification script (the script is outside the repository; it could print success after a failed type check, and every earlier commit's output shows the checks had in fact passed).

## 6. Open items

**Not met**

- Practitioner walkthroughs, and with them the second half of the gate: "Practitioners can follow a report to the underlying records without help." No session has been held.

**Known limits, accepted and documented**

- A snapshot manifest with no version at all is still read as benchmark-shaped. Normal preparation always writes one, so this needs a hand-edited file.
- `all_paying_accounts_covered`, `fee_journals_typed` and `reference_matching` are declarations by whoever prepares the data. Nothing can check them.
- A statement with no entry count and no balances is accepted: only supplied controls can be checked.
- F14 is not stopped by a reversal or credit on the same payment; it stays an exception to review.
- A credit note applied to an invoice is invisible to the contract, so an all-clear can ignore it.
- Xero batch payments end in no conclusion, because Xero journals are sourced to member payments.
- Assumptions A069 to A074 are unverified, and the walkthrough kit has not been piloted.

**Tests suggested by review and not written**

- A cutoff regression through the container (covered at library level).
- An exact truncated-reference reproduction for the earlier-cutoff case (the equality rule removes that case).
- A multiple-reversal-journal reproduction for `required_records_missing`.

**Carried to M6:** hop-depth enforcement, image digest pinning, resume, and the failure matrix.

**Simplicity:** M5 adds about 4,400 lines under `src/reconcile/`, mostly real-format handling (`camt053.py` alone is 777). All of it answers a recorded failure, but the evaluator and adapters are the largest maintenance risk and the first place to cut if the shortlisted formats change after practitioner input.

## 7. Purpose review (`AGENTS.md` section 9)

1. **Does this help an analyst investigate a real exception?** Unproven. The report shows a conclusion, its records and its limits in analyst words, but no analyst has seen it.
2. **Would it work on a real export format?** Partly and untested. The imports read the published shapes, and they refuse or abstain on what is missing, but no real file has been read, and on Xero plus CAMT.053 alone only F06 is answered.
3. **Is it the simplest thing that achieves that?** Mostly. No runtime dependencies. The format code is large because each check answers a measured failure.
4. **Is every claim backed by recorded evidence?** Yes for what is claimed here: the commands were run and their outputs recorded. Usefulness is not claimed.
5. **Did anything creep in that `SCOPE.md` defers?** No live integrations, hosted interface, or model calls. The baseline pages are small and were carried from the M2 gate.
