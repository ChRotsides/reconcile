# Data sources

Provenance, rights and the dataset manifest for every external dataset Reconcile uses. Facts below were verified by commands run on 2026-11-01 unless marked unverified. Everything under `data/` is ignored by git (`.gitignore`); this file is the tracked record.

## 1. Sources

| Source | Role | Status |
|---|---|---|
| FinRCA-Bench v1 (`PratikGhawate/FinRCA-AI-Bench`) | Primary independent synthetic benchmark | Retrieved, pinned, audited (see `docs/data-audit.md`) |
| RecBench (Hugging Face `end-close/recbench`) | Later matching/exception transfer task | Not retrieved |
| FinBalance (`Devansh1105/finbalance`) | Later document-grounding task | Not retrieved |
| BenchRec (operartis.com) | Possible later production-derived comparison | Not retrieved; access requires a request; reuse rights unconfirmed |

Only FinRCA has been retrieved. The rest of this file concerns FinRCA.

## 2. FinRCA-Bench: pinned revision

| Field | Value |
|---|---|
| URL | https://github.com/PratikGhawate/FinRCA-AI-Bench |
| Pinned revision | `76c93ec32536e29826e2ab9e68b58e0351410bc7` |
| Pinned revision exists | Yes. `git cat-file -t 76c93ec3...` printed `commit`; `git checkout` succeeded (detached HEAD). |
| Default branch | `main` |
| Default-branch HEAD at retrieval | `76c93ec32536e29826e2ab9e68b58e0351410bc7`. `git ls-remote origin HEAD` returned the same hash, so the pin is the current HEAD and no revision change is recorded. |
| Commit subject / date | "Initial public release of FinRCA-Bench" / Mon 2026-08-17 19:23:01 -0700 (2 commits in history: `d5a2349`, `76c93ec`) |
| Retrieval date | 2026-11-01 |
| Local path | `data/upstream/FinRCA-AI-Bench` (ignored) |
| Checkout integrity | `git status --short` empty (no local changes); git tree hash of the pinned commit `10d0368e49c5b4f0f8f5d017c109f0466834e6c9`; 818 tracked files |
| Dataset hash reported by upstream | `c73ad3e98575cb4093b1b3898f759d69c57a97840b4d39661b983e91692761d8` (`data/benchmark/dataset_statistics.md` line 3). Unverified: I did not recompute upstream's canonical-table hash. |
| Paper | https://arxiv.org/abs/2608.18534 (cited by `PLAN.md`; not read or checked in M0 so far; unverified) |

Commands run: `git clone https://github.com/PratikGhawate/FinRCA-AI-Bench`, `git rev-parse HEAD`, `git cat-file -t <pin>`, `git checkout <pin>`, `git ls-remote origin HEAD`, `git rev-parse HEAD^{tree}`. No upstream script, notebook or setup file was run and no package was installed.

## 3. Licence and rights

Upstream `LICENSE` (MIT, 21 lines): "Copyright (c) 2026 Pratik11". Permits use, copy, modify, merge, publish, distribute, sublicense and sell copies "of this software and associated documentation files", subject to keeping the copyright and permission notice in all copies or substantial portions. Warranty and liability are disclaimed.

| Material | What the licence permits | Unclear or unverified |
|---|---|---|
| Upstream code (`src/`, `generate_finrca_bench.py`, `run_phase6b_graph_llm_experiment.py`, `tests/`) | Use, modification and redistribution under MIT with the notice kept. | Copyright holder is written "Pratik11" while the repository owner is `PratikGhawate`; the identity link is assumed, not confirmed. |
| Dataset files (`data/benchmark/**`, `data/raw_clean/**`) | `LICENSE` names "software and associated documentation files" and does not mention data. The files are committed in the same repository, so MIT very likely applies, but no separate data licence or data card exists. `README.md` line 3 states the benchmark is synthetic and line 5 that all records are generated. | Whether MIT covers generated data is not stated. Treat as MIT-covered for internal evaluation; confirm with the author before publishing any copied rows. |
| Documents, frozen specs, prompts, result artefacts (`docs/`, `prompts/`, `results/`) | Same MIT notice. | `results/` contains predictions and analyses derived from the held-out test labels. Do not copy them into the repository. |
| Binary model files (`results/classical_ml/**/*.joblib`, `*.npz`) | Covered by MIT. | `joblib` files are pickles: loading them executes code. Never load them (AGENTS.md section 7). |
| Reconcile fixtures | Our own synthetic fixtures are ours. Copying FinRCA rows into fixtures would require carrying the MIT notice. | Decision: build fixtures independently; do not copy upstream rows (PLAN.md section 2: "our own tiny synthetic fixtures"). |
| Reconcile outputs (predictions, reports, metrics) | MIT imposes no restriction on results computed from the data. Reports must not reproduce sealed label content for holdout cases. | No citation requirement is stated in `README.md`. Cite the repository and revision anyway. |
| Third-party dependencies of upstream code (`requirements.txt`: numpy, pandas, scikit-learn, joblib, PyYAML, pytest, openai, pydantic, tiktoken) | Not examined. | Licences unverified. Not installed. |

Notice rule for Reconcile: if any upstream file is redistributed or any substantial portion is copied, include the MIT notice with `Copyright (c) 2026 Pratik11`.

## 4. Handling rules for this dataset

- Treat the checkout as data only. Upstream entry points (`python -m src.rules_sql.cli run`, `python -m src.classical_ml.cli run`, `python -m src.direct_llm.cli ...`, `generate_finrca_bench.py`) are not to be run outside an isolated environment. Read before running: `src/classical_ml/cli.py` lines 103-111 call `git` and `subprocess`, and line 214 writes into upstream `docs/`. `src/rules_sql/cli.py run` opens test labels itself after writing predictions (`README.md` line 166).
- Operational inputs are `data/benchmark/full/*.csv` only. Label, truth, provenance and oracle artefacts are listed in `docs/data-audit.md` section 3.
- `data/benchmark/rca_ground_truth.jsonl`, `benchmark_questions.jsonl`, `failure_manifest.csv`, `causal_edges.csv` at the top level of `data/benchmark/` contain all four splits including test and challenge. Do not open them; use the per-split files.
- The test and challenge label files were counted (lines) and hashed only. See `docs/data-audit.md` section 11 for exactly what was read.

## 5. Manifest

Row counts: CSV tables count data rows by the `csv` module (header excluded). The label CSVs `failure_manifest.csv` and `causal_edges.csv` and all `.jsonl`/`.txt` files were counted by lines only (header excluded where noted). Digests are SHA-256 of the file bytes, computed on 2026-11-01 with a throwaway script outside the repository. `dataset_quality_report.json`, `dataset_statistics.md` and `resolved_config.yaml` have no row count.

Table row counts in `data/benchmark/full/` match `data/benchmark/dataset_statistics.md` lines 22-35 (for example invoices 8147, payments 6200, bank_transactions 6263, audit_log 28034).

| Path | Bytes | SHA-256 | Rows | Row unit |
|---|---:|---|---:|---|
| `data/benchmark/benchmark_questions.jsonl` | 5484747 | `e852152fe2752ed2884fb3ab457e7548fa0cbce6fdbb240a1c03482741f93b69` | 11250 | lines |
| `data/benchmark/causal_edges.csv` | 5272164 | `9919210d2281c8387b3466cee407cf94be388022612fb2359c0df9d57ae9a179` | 97635 | lines minus header (line count only) |
| `data/benchmark/challenge_test/benchmark_questions.jsonl` | 498858 | `21efc4b72beaf021a7ce70fbbcdfd28c8ba56785177228322db9405165b170a8` | 1015 | lines |
| `data/benchmark/challenge_test/case_entity_records.jsonl` | 506376 | `5fb26c557d2e8c587dd586786c4206fe8b4a10cbca301ba667e932fc269196a5` | 1338 | lines |
| `data/benchmark/challenge_test/case_ids.txt` | 2233 | `584b8897fe53e8318f86092c754a3c9e9dc1c082367b89d9d5cd20e5cda4d28a` | 203 | lines |
| `data/benchmark/challenge_test/failure_manifest.csv` | 29264 | `e3f79466251d5b5f2dd77173e8048b25c524f045f105b710ef6f24a147954fd4` | 203 | lines minus header (line count only) |
| `data/benchmark/challenge_test/rca_ground_truth.jsonl` | 215475 | `27d94fff852f806b7b1afad19fd0e1661efe88dd9ffc9c03826e2e273906a72a` | 203 | lines |
| `data/benchmark/dataset_quality_report.json` | 65899 | `3eddc653265f2d918f16167d22d6b363a280462290f3ec486a59dbf63624fb60` | - | n/a |
| `data/benchmark/dataset_statistics.md` | 1732 | `80d3d73b606d3cdc1029ae36ec6d6e87c981965a8a0df275f20b5215ff00f27e` | - | n/a |
| `data/benchmark/failure_manifest.csv` | 311793 | `e6ef55d20485619f8bb1a84baa0c8adf17295dfe7bef7058088f2cec464278fa` | 2250 | lines minus header (line count only) |
| `data/benchmark/full/approval_events.csv` | 2640720 | `1b06ae818dd5db4e994ae33604996949c85bda4f09b1d5212d9b2918165060d6` | 17429 | data rows (excl. header) |
| `data/benchmark/full/audit_log.csv` | 2806719 | `df9672985bb829933c520f8070007351161f155e79c3ad75f310431bcc33fa38` | 28034 | data rows (excl. header) |
| `data/benchmark/full/bank_statements.csv` | 5279 | `03ae8c7053d9f496ac1572e8935a3e7a90a932bb7174b06e8f5a1256ef8c2933` | 57 | data rows (excl. header) |
| `data/benchmark/full/bank_transactions.csv` | 970897 | `3692b353dda734eb89eb4f90c547493112852d90cff291f0b816719544fca513` | 6263 | data rows (excl. header) |
| `data/benchmark/full/employees.csv` | 5986 | `1fa83c16c723e07c1f437869536a23d65bfe732123e18f83e5747e92e904e203` | 120 | data rows (excl. header) |
| `data/benchmark/full/gl_entries.csv` | 6897304 | `fb3bb454d7a1c2278286fd9d71dbca579d893c94c49dc305659e34585fc8852a` | 46765 | data rows (excl. header) |
| `data/benchmark/full/invoice_lines.csv` | 1792301 | `630c357a611a99e7fd8b59d0ce9facfd1b5d8f7940907ba73e493b385b575d47` | 18299 | data rows (excl. header) |
| `data/benchmark/full/invoices.csv` | 1234475 | `49a529ab18e6b0d2373cbf9ab5352a5ab1df74580907ef96900af012e85416e8` | 8147 | data rows (excl. header) |
| `data/benchmark/full/payment_allocations.csv` | 358928 | `0bff28018ce3053352ba3e41d084d931d6cede03bfa12ca05e5e4332b3b441aa` | 7180 | data rows (excl. header) |
| `data/benchmark/full/payments.csv` | 823402 | `0289a1fe560edb8afc5f199d2dc6f485e08f14c805bffa532b4781f9e474c2f2` | 6200 | data rows (excl. header) |
| `data/benchmark/full/po_lines.csv` | 1578112 | `6a8edf1f626d4257000203fc59477b77be17c533b6e420f273e2e233b31f4b36` | 11231 | data rows (excl. header) |
| `data/benchmark/full/purchase_orders.csv` | 703940 | `191886778cd6884a41ca69fc4f47760ad40077990ceb41d940e5642ba19845a0` | 5000 | data rows (excl. header) |
| `data/benchmark/full/vendor_change_log.csv` | 28143 | `98218759f5e365219221a342dc559a2b2827106ead4d7685db9ae6cf7b1d9e6f` | 186 | data rows (excl. header) |
| `data/benchmark/full/vendors.csv` | 94985 | `75bebafe8fdec13bc9c52a8bf344a034d104088d034b137b537039cc732190c5` | 500 | data rows (excl. header) |
| `data/benchmark/internal/mutation_log.jsonl` | 3140108 | `533aaa5495001b0baa0e565c7b925ed065bf12bf310c445f1bd1d9277804dfe9` | 7304 | lines |
| `data/benchmark/rca_ground_truth.jsonl` | 2375704 | `295b0cbaa7a382cbe0d41180584cf1a82e21ec8c5282962bdca73d53f84a8d72` | 2250 | lines |
| `data/benchmark/resolved_config.yaml` | 925 | `f067cd6814f03dbf99f2a96191672b48d890f46e8bfddde59c0014f4a55240b5` | - | n/a |
| `data/benchmark/test/benchmark_questions.jsonl` | 1066519 | `46764e71d537843e3ef2bd04b58fee8d2b4d2b734f0903368093301054bb4818` | 2195 | lines |
| `data/benchmark/test/case_entity_records.jsonl` | 1040163 | `206ce3b34cd2270fb52a01daa99528248818c2ee23ade541d5edb51ef33a3cb8` | 2716 | lines |
| `data/benchmark/test/case_ids.txt` | 4829 | `6bb26ae102ba5d957e6d70a0e58fda6fa94bd82f68a9dbaeca3561b8f3df1584` | 439 | lines |
| `data/benchmark/test/failure_manifest.csv` | 60148 | `285dc740959cedccbf9db5141085a9cbdd92ff138759c35d22dd08b840c85eb5` | 439 | lines minus header (line count only) |
| `data/benchmark/test/rca_ground_truth.jsonl` | 462954 | `60fad1341c8bdb1f41a934f0992aa1444025916c643ee05f0ec9231d0c912033` | 439 | lines |
| `data/benchmark/train/benchmark_questions.jsonl` | 2899556 | `2a0a258c8dcd95446de5e9bff445051f7e65ee66bd13a4361e78064c9fb448cd` | 5960 | lines |
| `data/benchmark/train/case_entity_records.jsonl` | 2845113 | `72161beebeecb7e7e9aa95e8853d43960882b0b04f745737f82b371c3e4519f7` | 7442 | lines |
| `data/benchmark/train/case_ids.txt` | 13112 | `f0e45a78d34b96cb1b66ef329db77a64f61f8cb86229880393510ec44c647aa9` | 1192 | lines |
| `data/benchmark/train/failure_manifest.csv` | 164161 | `4007876f3a365fbf913f5cdf74998b637fa21622d92ccfc16a2f9c12b533ea56` | 1192 | lines minus header (line count only) |
| `data/benchmark/train/rca_ground_truth.jsonl` | 1257887 | `7f1fe32586637450c558633f2b20c090f9e7031257582ef24dbc141d68b279b5` | 1192 | lines |
| `data/benchmark/validation/benchmark_questions.jsonl` | 1019814 | `d1b57b2d320d01183a9e62ac593dabad49c7aa86b35153a34d57f372044c5cca` | 2080 | lines |
| `data/benchmark/validation/case_entity_records.jsonl` | 1024412 | `bc3f76bf4c831095dc796415ab21df1840d206ca4ff49726442ddc5b3d83d4af` | 2680 | lines |
| `data/benchmark/validation/case_ids.txt` | 4576 | `fcfab5e4f0e980194eb27c490f18972df4fb39a85407db00e5af5bb4d1dbd769` | 416 | lines |
| `data/benchmark/validation/failure_manifest.csv` | 58661 | `4450ca6aca386032a3087a9252c22ad5ebf1d47e315b1fa3a6ccd5f906935f14` | 416 | lines minus header (line count only) |
| `data/benchmark/validation/rca_ground_truth.jsonl` | 439388 | `de77604e13c2f00fad98b940c2c6bdd05f089f8be3d4101926a44911b72a401b` | 416 | lines |
| `data/raw_clean/approval_events.csv` | 2654990 | `61085066e8589f6698149d3dd9e28f987a0026b33695b8ada1d6eb11b2720ff9` | 17529 | data rows (excl. header) |
| `data/raw_clean/audit_log.csv` | 2668170 | `61ec1876986030ef1eb1e41a24265ec974f8696748b848c7d2780cceb75d1f75` | 27040 | data rows (excl. header) |
| `data/raw_clean/bank_statements.csv` | 4909 | `f264f1816323d34747d6b3f7e7c7f0f164ffc8120edb030a2fa9f946bdb955fd` | 54 | data rows (excl. header) |
| `data/raw_clean/bank_transactions.csv` | 938434 | `6f12d8e185f2b6d5a08bcf6b656e35cde216b29e266cea546814c32ecca66bd3` | 6063 | data rows (excl. header) |
| `data/raw_clean/employees.csv` | 5986 | `1fa83c16c723e07c1f437869536a23d65bfe732123e18f83e5747e92e904e203` | 120 | data rows (excl. header) |
| `data/raw_clean/gl_entries.csv` | 6750427 | `98f27090ce6b9314f518f0364fa532728fea0fd350d44de03e8d2378c84f0d07` | 45800 | data rows (excl. header) |
| `data/raw_clean/invoice_lines.csv` | 1761074 | `cf1296124b9d527d8ac7c809f3db6399d0620d6d482ebd39765ffdbd151665a2` | 17977 | data rows (excl. header) |
| `data/raw_clean/invoices.csv` | 1211176 | `f8f9557e7907a7d8a99fdfdfdad425f71b7513ca5cc76c0ab8f2224d148a22df` | 8000 | data rows (excl. header) |
| `data/raw_clean/payment_allocations.csv` | 353949 | `42c47f95e7e35fe07d5aadbb4671054e0570a766184847625483b97472d0c156` | 7080 | data rows (excl. header) |
| `data/raw_clean/payments.csv` | 796323 | `e9e7975a804c1566a721febe22db282af2374cc4d8c085183a14da7bc1f08d9c` | 6000 | data rows (excl. header) |
| `data/raw_clean/po_lines.csv` | 1578112 | `6a8edf1f626d4257000203fc59477b77be17c533b6e420f273e2e233b31f4b36` | 11231 | data rows (excl. header) |
| `data/raw_clean/purchase_orders.csv` | 703940 | `191886778cd6884a41ca69fc4f47760ad40077990ceb41d940e5642ba19845a0` | 5000 | data rows (excl. header) |
| `data/raw_clean/vendor_change_log.csv` | 5553 | `8a482118c598c4e3874c0fbee5b6c5c4a1dd6491e68beabcdb57c1358cd1770a` | 40 | data rows (excl. header) |
| `data/raw_clean/vendors.csv` | 94985 | `75bebafe8fdec13bc9c52a8bf344a034d104088d034b137b537039cc732190c5` | 500 | data rows (excl. header) |

Not individually hashed: the remaining 762 tracked files (`src/`, `tests/`, `docs/`, `prompts/`, `results/`, config). They are covered by the git tree hash in section 2.

## 6. Split case counts (line counts of `case_ids.txt` and `rca_ground_truth.jsonl`)

| Split | Cases | Role proposed in `docs/data-audit.md` |
|---|---:|---|
| train | 1192 | Development labels |
| validation | 416 | Development labels |
| test | 439 | Reproduction set only (upstream already published results on it) |
| challenge_test | 203 | Candidate final holdout; labels not read |
| Total | 2250 | Matches `dataset_statistics.md` line 9 |
