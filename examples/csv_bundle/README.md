# CSV bundle example (invented data)

A folder of exports plus `mapping.toml`, which says where each contract column
comes from. Source column names, date formats and number styles differ per file
on purpose. `approval_events` is not supplied, so no case can be cleared as
"legitimate" (the manifest lists it under `tables_not_supplied`).

```python
from datetime import date
from pathlib import Path
from reconcile.adapters.csv_bundle import prepare_csv_bundle

manifest = prepare_csv_bundle(
    Path("examples/csv_bundle/mapping.toml"),
    Path("out/snapshot"),
    cutoff=date(2026, 6, 30),
)
```

Then investigate invoice `INV-2001` and `INV-2002` against
`out/snapshot/snapshot.sqlite` (record IDs are your own IDs, unchanged):

- `INV-2001`: two payments, both debited before the cutoff: answered `F06`
  (duplicate payment, 1000.00 EUR committed twice).
- `INV-2002`: the second debit posts 2026-07-01, after the cutoff: abstained
  (`commitment_not_proven_at_cutoff`).

Preparation stops, naming file, line and value, on a date that matches no
declared format or matches two with different results, a status not in its
`values` map, a bank export that ends before the cutoff, or a mapping that does
not state what the bank export covers (`bank_data_from`, `bank_data_through`,
`all_paying_accounts_covered`), how references are matched (`reference_matching`)
and whether fee journals are typed (`fee_journals_typed`).
