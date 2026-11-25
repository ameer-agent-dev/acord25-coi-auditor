# ACORD 25 COI Auditor

Deterministic Certificate of Insurance (COI) compliance auditor that extracts coverage limits, policy dates, checkboxes, Certificate Holder text, and attached endorsement pages from ACORD 25 PDFs in Python, validates them against contract insurance thresholds, and produces a compliance register, audit report, and draft broker deficiency emails.

Built for **general contractors, commercial property managers, risk managers, and procurement / AP teams** who review subcontractor or vendor insurance packets before releasing payments or granting site access.

## Quick Install

```bash
npx skills add ameer-agent-dev/acord25-coi-auditor --skill acord25-coi-auditor
```

*(Single-repo shorthand: `npx skills add ameer-agent-dev/acord25-coi-auditor`)*

## Required & Optional Inputs

- **Mandatory**: Path to a single ACORD 25 `.pdf` file or a folder of vendor COI packets.
- **Contract Requirements**: Path to a `requirements.yaml` (or JSON) threshold file. If omitted, the skill asks once whether to apply `assets/requirements_default.yaml` (`$1M/$2M` GL per-project aggregate, `$1M` Auto, `$1M` WC/EL, Additional Insured, Primary & Non-Contributory, and Waiver of Subrogation required) or custom limits and exact Certificate Holder legal wording.
- **Optional**: Reference audit date (`--as-of YYYY-MM-DD`, defaults to today) and expiry warning window (`--warning-days`, defaults to `30` days).

## Example Prompts

- **Normal run**:
  > "Audit all subcontractor COI PDFs in `./vendor_cois/` against our GC requirements in `./gc_requirements.yaml` as of `2026-11-26`, flag policies expiring within 30 days, and draft cure emails for any deficient vendors."
- **Edge case (bundled W-9s, scanned PDFs, and checkbox-only COIs without endorsements)**:
  > "Check `./sub_packets/` using the default commercial GC limits with Certificate Holder `Apex Builders LLC`. Several packets bundle W-9 forms, one scan has no text layer, and two brokers checked `ADDL INSD` without attaching `CG 20 10` or `CG 20 37`."

## Expected Output Files

- `coi_extracted.json`: Structured per-file extraction payload with page/line citations, `scanned_files_count`, `skipped_files_count` (e.g., W-9 pages), and `NEEDS_HUMAN_CHECK` flags.
- `coi_compliance_register.csv`: 13-column payment-hold tracker (`vendor_name` through `deficiency_codes`) classifying each vendor as `PASS`, `EXPIRING_SOON`, `HOLD_DEFICIENT`, or `MANUAL_REVIEW_REQUIRED`.
- `coi_audit_report.md`: Executive summary with status counts, limit/date comparisons, and ACORD 25 endorsement verification notes (`ENDORSEMENT_DOC_MISSING` when `CG 20 10`, `CG 20 37`, or `CG 24 04` pages are absent).
- `deficiency_emails/<vendor_slug>_deficiency.md`: Ready-to-review cure email drafts for non-compliant vendors and their insurance brokers.

## Bundled Scripts, References & Assets

| Path | Purpose |
| :--- | :--- |
| `SKILL.md` | Agent workflow rules, input gates, and edge-case handling |
| `scripts/extract_acord25.py` | Isolates ACORD 25 and endorsement pages via `pdfplumber`, skips W-9s, flags scanned PDFs (`--help` supported) |
| `scripts/audit_coi_rules.py` | Evaluates `coi_extracted.json` against contract rules and writes CSV, report, and email drafts (`--help` supported) |
| `references/acord25_field_map.md` | Section-by-section ACORD 25 (2025/12) field and checkbox reference |
| `references/endorsement_checklist.md` | Verification rules for `CG 20 10`, `CG 20 37`, `CG 24 04`, and PNC endorsements |
| `assets/requirements_default.yaml` | Default commercial GC insurance threshold configuration |
| `assets/deficiency_email_template.md` | Broker/vendor deficiency cure email Markdown template |

### Python Dependencies

Requires Python 3 with `pdfplumber` and `PyYAML` (JSON requirements fallback is built in):

```bash
pip install pdfplumber PyYAML
```

## Autonomy & Safety Boundary

- **Autonomous local operations**: Reads local PDFs and requirement files offline, runs deterministic limit/date validation in Python, treats all PDF text and broker remarks strictly as untrusted data, and writes local CSV/Markdown drafts without fabricating values on blank or scanned pages.
- **Human sign-off required**: Never sends emails to vendors/brokers or modifies ERP payment holds automatically. When connected to email or ERP tools, configure user-only invocation (`disable-model-invocation: true` in Claude Code or host equivalent). Outputs represent an operational compliance check, not legal or licensed insurance brokerage advice.

## License

[MIT](LICENSE)
