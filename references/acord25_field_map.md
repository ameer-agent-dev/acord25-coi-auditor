# ACORD 25 Field Map and Extraction Guide

Use this reference when verifying extracted fields, resolving `NEEDS_HUMAN_CHECK` flags, or diagnosing non-standard broker layouts on ACORD 25 (2016/03 and 2025/12) Certificate of Liability Insurance forms.

## Document Signatures and Page Classification

`scripts/extract_acord25.py` classifies every PDF page before extracting fields so multi-document vendor packets do not crash the audit:

- **ACORD 25 page**: Contains `CERTIFICATE OF LIABILITY INSURANCE` or `ACORD 25` header text.
- **Endorsement page**: Contains ISO form numbers (`CG 20 10`, `CG 20 37`, `CG 24 04`, `CG 20 01`) or endorsement titles (`ADDITIONAL INSURED`, `WAIVER OF TRANSFER OF RIGHTS OF RECOVERY`, `PRIMARY AND NONCONTRIBUTORY`).
- **Skipped non-COI page**: Contains `FORM W-9`, `REQUEST FOR TAXPAYER IDENTIFICATION`, or blank/cover text without ACORD 25 or endorsement markers. Logged under `skipped_pages`.
- **Scanned/image-only page**: Yields fewer than 40 extractable text characters. Flagged `SCANNED_PDF` and routed to `MANUAL_REVIEW_REQUIRED` with zero fabricated values.

## Core Header and Party Blocks

| ACORD 25 Section | Target JSON Field | Extraction Notes |
| --- | --- | --- |
| PRODUCER | `producer_name`, `broker_email`, `producer_phone` | Top-left block; broker email is captured from `E-MAIL ADDRESS:` or first valid email in producer block. |
| INSURED | `vendor_name` | Named subcontractor or vendor directly below Producer; must match contracted legal entity. |
| CERTIFICATE HOLDER | `certificate_holder.name`, `certificate_holder.block` | Bottom-left block; exact legal name is compared against `requirements.yaml`. |
| DESCRIPTION OF OPERATIONS | `description_of_operations` | Middle-lower free-text box; scanned for Additional Insured, Primary and Non-Contributory, Waiver of Subrogation, and project/job references. |

## Coverages Grid Mapping

Each coverage row in the ACORD 25 grid includes policy number, effective date (`MM/DD/YYYY`), expiration date (`MM/DD/YYYY`), `ADDL INSD` flag, `SUBR WVD` flag, and dollar limits.

### Commercial General Liability (CGL)

- `gl_occ_limit`: `EACH OCCURRENCE` dollar limit.
- `gl_agg_limit`: `GENERAL AGGREGATE` dollar limit.
- `gl_products_comp_op_agg`: `PRODUCTS - COMP/OP AGG` dollar limit.
- `gl_agg_basis`: Checkbox under `GEN'L AGGREGATE LIMIT APPLIES PER`: `POLICY`, `PRO-JECT` (normalized to `PROJECT`), or `LOC`. Commercial GC contracts typically require `PROJECT` so losses on other jobs do not exhaust the aggregate.
- `addl_insd_flag`: `Y` if marked in the CGL `ADDL INSD` column, `N` if explicitly blank/N, or `NEEDS_HUMAN_CHECK` if ambiguous.
- `wos_flag`: `Y` if marked in the `SUBR WVD` column (GL or WC), `N` if unchecked, or `NEEDS_HUMAN_CHECK` if ambiguous.

### Automobile Liability

- `auto_csl_limit`: `COMBINED SINGLE LIMIT (Ea accident)` dollar limit.
- Verify policy effective and expiration dates alongside `ADDL INSD` and `SUBR WVD` flags.

### Umbrella / Excess Liability

- `umbrella_occ_limit`: `EACH OCCURRENCE` dollar limit.
- `umbrella_agg_limit`: `AGGREGATE` dollar limit.
- Optional in some small-vendor tiers; validated whenever `umbrella_each_occurrence_min > 0` in `requirements.yaml`.

### Workers Compensation and Employers' Liability

- `wc_statutory`: `PER STATUTE` checkbox or literal `STATUTORY` marker. Treat `STATUTORY` as compliant for Workers' Compensation statutory benefits.
- `el_each_accident`: `E.L. EACH ACCIDENT` limit (still required even when WC is `STATUTORY`).
- `el_disease_ea_employee`: `E.L. DISEASE - EA EMPLOYEE` limit.
- `el_disease_policy_limit`: `E.L. DISEASE - POLICY LIMIT` limit.
- `wc_exp_date`: Expiration date in ISO `YYYY-MM-DD` format.

## Confidence and Citation Rules

- Every extracted field records `page` (1-based), `line` (1-based text line), and a short verbatim `snippet` under `citations`.
- Dates are normalized from `MM/DD/YYYY` or `MM-DD-YYYY` to ISO `YYYY-MM-DD`. Unparseable dates become `NEEDS_HUMAN_CHECK`.
- Dollar amounts strip `$` and commas into integer USD values. Non-numeric strings (other than `STATUTORY` for WC) become `NEEDS_HUMAN_CHECK`.
