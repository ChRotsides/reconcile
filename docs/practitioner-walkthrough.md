# Practitioner walkthrough kit

**Status:** Not yet used. No session has been held. This kit is for the M5 task "hold walkthrough sessions where practitioners use the HTML report on realistic cases" and for the still-open M2 practitioner packet review (`PLAN.md`). The M5 gate it tests: "Practitioners can follow a report to the underlying records without help."

Findings are self-reported, from a small sample, and are reported separately from benchmark results (`AGENTS.md` section 6). The demo data is invented, so a session says nothing about real exports.

## Rules for the facilitator

- Do not explain the report before they try it. Say only what is in the script.
- Do not defend the report or the tool. Do not correct a wrong reading; write it down.
- Do not ask leading questions ("is this clear?", "would you trust this?"). Use the open questions below.
- Do not help with a task. If they ask, say "do what you would normally do" and note the question.
- Note times with a phone timer. Do not stop them for being slow.

## Setup (before the session, 10 minutes)

1. From a checkout, install and build the demo as in `docs/cli-guide.md`, then run `reconcile demo --output demo-out`.
2. Open `demo-out/report/index.html` in a browser, offline. Check you can open each case page and the source records from it.
3. No real data is needed and none should be brought. Send the permission note (`docs/practitioner-findings.md` section 3) first and get a written "agreed". Say the session is about the report, not about them.
4. Have a copy of the recording table (below) per person.

## Script (30 minutes)

Opening (3 min). Say: "This is a report a tool produced from an invented company's accounting and bank files. I would like you to look at it as you would at a colleague's note. Please think aloud. There are no right answers and I cannot help during the tasks."

Cases, in this order (5 minutes each). Show the index first and let them pick nothing; you choose the case.

| # | Case (title as shown in the report) | Task | Open question |
|---|---|---|---|
| 1 | Possible duplicate payment | "Find the bank row behind this conclusion." | "What would you do next?" |
| 2 | Payment possibly missing from the bank | "Tell me what was searched and what was not." | "Where would you check this?" |
| 3 | Consistent with a bank debit reduced by a booked bank fee | "Show me the numbers that support this, and check one yourself." | "What would make you stop and ask someone?" |
| 4 | No conclusion reached (two payments share a reference) | "Tell me what this report does not know." | "What would you need to see to decide?" |
| 5 | Bank debit smaller than the payment, unexplained | "Find the payment and the bank row, and say what is still unknown." | "Who would you hand this to, and what would you tell them?" |

The sixth demo case (a payment still within its clearing limit) is a spare if time remains.

Closing (7 min). Ask: "What was the hardest part?" "What did you look for that was not there?" "What did you ignore?" Then, if the interview guide has not covered it: "Which accounting export and bank format do you use, and do payment references match between your accounting and bank data?" The last question is the largest open assumption (`docs/input-mapping.md` G1; assumption A029). Write the answer in their words.

## What counts as following without help

For tasks 1, 2, 3 and 5, all of these without prompting from you:

- they reach the source record page or section from the conclusion by themselves;
- they name the bank or accounting row (by its id or its date and amount) that the conclusion rests on;
- their stated conclusion matches what the report says, including "no conclusion" and "unknown";
- they say, unprompted or on the "does not know" task, at least one thing the report did not establish.

A task is **not** followed without help if you had to answer a question about the report, if they gave up, or if their reading contradicts the report (record it as a wrong reading). Count a hesitation only if they stopped for several seconds or re-read the same section; note the section.

## Recording table

One row per case per person. Use a code (P1, P2) not a name. Quote their words; mark your inference as such.

| Person | Case | Followed without help (yes/no) | Time to bank row (min:sec) | Hesitation (where, what they said) | Distrust (what, why) | Information they wanted that is not shown | Wrong reading |
|---|---|---|---|---|---|---|---|
| P1 | 1 | | | | | | |

## How findings change the report

After each session, copy rows into `docs/practitioner-findings.md` section 6. Sort each finding:

- **Wrong reading or missing information that two people share:** change the report (wording in `src/reconcile/reports/explain.py`, layout in `src/reconcile/reports/html.py`), add a test, and note the change next to the finding.
- **One person only:** record it, change nothing yet, and look for it in the next session.
- **A request for a feature outside `SCOPE.md`:** record it as deferred; do not build it.
- **A disagreement between practitioners:** record both views. Do not average them.

Re-run the demo after a change and, where possible, show the changed page to the next person. Do not claim the report is understood until the gate above holds for at least two practitioners; with so small a sample it says only that the report was followable for them.
