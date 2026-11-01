# Practitioner findings

**Status:** Pending. No practitioners have been interviewed yet. Nothing in this file is a finding until a section below is filled in from a real interview.

## 1. Purpose and use

- Test whether the three initial families (suspected duplicate payment, missing settlement, bank/accounting amount difference) are frequent and costly for people who handle accounts-payable-to-bank exceptions, and whether the next steps and evidence the project assumes match their practice (`PLAN.md` sections 2 and 6/M0).
- Collect which accounting and bank export formats they use, to choose the first formats to support (`docs/export-formats.md`).
- Collect their estimates of time per exception and cost of a missed duplicate or unresolved settlement.
- All statements are **self-reported** by a small, non-random sample. They are estimates, not measurements. They are reported separately from benchmark results and never combined with them into one claim (`AGENTS.md` section 6, `PLAN.md` "Usefulness evidence").
- If practitioners do not confirm the three families as worth investigating, the families are revised before M1 (`PLAN.md` M0 gate).

## 2. Recruitment

- **Target:** at least two practitioners from different organisations or different roles (for example AP clerk, finance operations lead, controller). More is better; two is the minimum for the M0 gate.
- **Record per practitioner:** role, years of experience in AP or finance operations, organisation size band (for example under 50 / 50-500 / over 500 staff), industry, region, accounting system(s) and bank(s) used. Use a code (P1, P2, ...) in place of a name.
- **Names:** not recorded here. Record a name or an organisation only if the person agrees in writing to be credited, and note that agreement.
- **Contact details and consent records:** keep outside the repository.

Recruitment log (fill as it happens):

| Code | Role | Experience | Org size / industry / region | Date contacted | Outcome |
|---|---|---|---|---|---|
| P1 | pending | pending | pending | pending | pending |
| P2 | pending | pending | pending | pending | pending |

## 3. Permission note template

Send before the interview. Fill the bracketed items; do not interview or accept files without a written reply.

> Subject: Permission to use your input on a payment-exception tool
>
> Thank you for agreeing to talk to me about accounts-payable-to-bank payment exceptions.
>
> **Purpose.** I am designing a read-only tool that helps an analyst investigate such exceptions. I want to learn how you handle them today. Your answers will be used only to decide what the tool should cover and how it should present evidence. [Optionally: with your agreement I may also look at an export you choose to share, only to check that the tool can read your formats.]
>
> **What I will keep.** Notes of what you say, recorded by role and years of experience, not by name, unless you agree below to be credited. Your answers are your own estimates and will be reported as such, in aggregate with other practitioners' answers, never as measured results.
>
> **Files.** I do not need real files. If you choose to share an export, you should first remove or mask supplier names, bank account numbers and personal data where you can. Any file I receive will be stored only in a private folder on my machine that is excluded from version control. It will not be copied into test data, examples, logs, issue trackers, shared documents or any AI model prompt, and will be used only for [the purpose above].
>
> **Retention and deletion.** I will keep notes until [date, for example the end of the project's first release] and any file for [N days / until the format check is done, whichever is sooner]. I will then delete them and confirm to you in writing. You can ask me at any time to delete what you have given me; I will do so within [N] days.
>
> **Publication.** Only aggregate findings you have not objected to will be published. Quotes will be used only with your approval of the exact wording.
>
> **Credit.** [ ] You may credit me by name and organisation. [ ] Please keep me anonymous.
>
> Please reply "agreed" to confirm. You can withdraw at any time.

Consent record (outside the repository; summarise here):

| Code | Consent date | Files shared? | Retention end | Deletion confirmed | Credit agreed? |
|---|---|---|---|---|---|
| P1 | pending | pending | pending | pending | pending |
| P2 | pending | pending | pending | pending | pending |

Handling rules (`PLAN.md` section 2, `AGENTS.md` section 2): real exports go only to ignored `data/private/`, never into fixtures, tests, issues, logs or model prompts.

## 4. Interview guide (45 minutes maximum)

Design rules: open questions first; do not name the project's three families until the closing questions; ask for the last concrete case before generalising; record the practitioner's own words and wording of exception types; do not correct them. All times and costs are estimates; ask for a range and how they arrived at it.

**A. Context (3 min)**
1. What is your role, and how long have you handled supplier payments or bank reconciliation?
2. Which accounting or ERP system and which banks do you work with? Roughly how many supplier payments a month?

**B. Exceptions in general (8 min)**
3. In your work, what kinds of problems arise between what the books say was paid and what the bank shows? Please list them in your own words.
4. Which of these happen most often? Which cost the most when they go wrong? How do you tell?
5. Tell me about the last one you investigated. What triggered it, and what happened?

**C. How each is investigated (12 min)**
For up to three exception types they named (let them choose):

6. How do you find out about it, and what do you look at first?
7. Which systems, reports or files do you use? Which exports or screens? What do you do by hand?
8. How long does one take, typically and at worst? Where does the time go?
9. What do you need to see before you act on it? What would make you stop and ask someone else?
10. What is the correct next step, and who decides it?
11. How do you decide that it is not a real problem (for example, still in transit, a fee, an instalment)? How often is that the case?

**D. Cost and consequence (5 min)**
12. What happens if one of these is missed or stays unresolved? Can you give an example, with a rough amount or effort, and how you know?
13. How often does that happen, as far as you can tell?

**E. Trust in automated help (5 min)**
14. If a tool proposed a diagnosis for one of these, what would you want it to show? 
15. What would make you distrust it, or stop using it? Have you used a tool that did this badly, and what went wrong?
16. Which actions should such a tool never take on its own?

**F. Formats and data (6 min)**
17. How do you get data out of your accounting system for these checks: which report, file type, API or screen? Can you describe the columns you rely on (not share them)?
18. What format do your banks give you: CSV, an XML statement, a text statement, or screens only? Which reference fields do you rely on to match a payment? How often are they missing or unreliable?
19. Is there anything you cannot see from the files you have that you need?

**G. Closing (4 min)**
20. Here are three kinds of problem we are considering: the same invoice paid twice; a payment in the books that never reaches the bank; a bank amount that differs from the books. For each: is it something you deal with, how often relative to your other exceptions, and is it worth investigating? What is missing from this list?
21. Who else should I talk to? May I come back with a few example reports for your reaction?

Record answers in section 5 using the practitioner's own wording. Mark every number as an estimate.

## 5. Findings

All entries are **pending — no practitioners interviewed yet**. Fill one table per practitioner; do not paraphrase beyond what was said; mark disagreements rather than reconciling them.

### 5.1 Per-practitioner record

Repeat this block for P1, P2, and each further practitioner.

#### P1 — pending — no practitioners interviewed yet

| Item | Response (self-reported) |
|---|---|
| Role / experience / org size / region | pending |
| Accounting system(s) and bank(s) | pending |
| Exceptions named, most frequent | pending |
| Exceptions named, most costly | pending |
| How each is investigated, tools and exports | pending |
| Time per exception type (estimate, range) | pending |
| Cost of a missed duplicate (estimate, basis) | pending |
| Cost of an unresolved settlement (estimate, basis) | pending |
| Evidence needed before acting | pending |
| Correct next step per exception | pending |
| What would cause distrust of an automated diagnosis | pending |
| Accounting export used (report/file/API; columns relied on) | pending |
| Bank format used; reference fields relied on | pending |
| Reaction to the three families (frequent? costly? missing ones?) | pending |
| Files shared? permitted use | pending |

#### P2 — pending — no practitioners interviewed yet

Same table as P1; all responses pending.

### 5.2 Summary

| Measure | Result | n | Roles | Notes |
|---|---|---|---|---|
| Duplicate payment: ranked among costly exceptions? | pending | 0 | pending | pending — no practitioners interviewed yet |
| Missing settlement: ranked among costly exceptions? | pending | 0 | pending | pending — no practitioners interviewed yet |
| Amount discrepancy: ranked among costly exceptions? | pending | 0 | pending | pending — no practitioners interviewed yet |
| Exception types named that are not in the three families | pending | 0 | pending | pending — no practitioners interviewed yet |
| Estimated time per exception, by type | pending | 0 | pending | self-reported estimate |
| Estimated cost of a miss, by type | pending | 0 | pending | self-reported estimate |
| Evidence required before acting | pending | 0 | pending | pending — no practitioners interviewed yet |
| Accounting exports in use | pending | 0 | pending | pending — no practitioners interviewed yet |
| Bank formats in use | pending | 0 | pending | pending — no practitioners interviewed yet |
| Causes of distrust | pending | 0 | pending | pending — no practitioners interviewed yet |
| Disagreements between practitioners | pending | 0 | pending | pending — no practitioners interviewed yet |

### 5.3 Effect on decisions

pending — no practitioners interviewed yet. When filled: state whether the three families are confirmed, revised or dropped; state the first accounting and bank formats chosen against `docs/export-formats.md`; record both as decisions.
