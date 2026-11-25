# Endorsement Verification Checklist

Use this reference when reviewing attached endorsement PDFs or explaining why checked ACORD 25 boxes alone do not satisfy commercial contract insurance requirements.

## Official ACORD 25 Notice Principle

The ACORD 25 Certificate of Liability Insurance (including the 2025/12 revision) explicitly states that the certificate is issued as a matter of information only and confers no rights upon the Certificate Holder. Checking `Y` in the `ADDL INSD` or `SUBR WVD` columns, or typing wording into the Description of Operations box, does not amend, extend, or alter the actual insurance policy.

Because of this rule:

- If `require_endorsement_attachments: true` in `requirements.yaml` and no supporting endorsement pages are attached, `scripts/audit_coi_rules.py` raises `ENDORSEMENT_DOC_MISSING`.
- Always request the actual ISO endorsement forms (or equivalent carrier blanket endorsement pages) from the subcontractor's broker before releasing payment holds on high-risk trades.

## Key Commercial Construction Endorsement Forms

| Form / Concept | Purpose | What to Verify on the Attached Page |
| --- | --- | --- |
| `CG 20 10` | Additional Insured - Owners, Lessees or Contractors - Scheduled Person or Organization (Ongoing Operations) | Matches policy number on ACORD 25; names the Certificate Holder (or uses blanket written-contract wording). |
| `CG 20 37` | Additional Insured - Owners, Lessees or Contractors - Completed Operations | Covers post-completion liability; confirm both ongoing (`CG 20 10`) and completed operations (`CG 20 37`) when required by contract. |
| `CG 24 04` | Waiver of Transfer of Rights of Recovery Against Others to Us (GL Waiver of Subrogation) | Confirms carrier waives subrogation rights against the Certificate Holder; check schedule or blanket contract language. |
| `WC 00 03 13` | Waiver of Our Right to Recover From Others Endorsement (Workers' Comp Waiver of Subrogation) | Confirms Workers' Compensation carrier waives subrogation rights where permitted by state law. |
| `CG 20 01` (or PNC clause) | Primary and Non-Contributory - Other Insurance Condition | States the subcontractor's policy is primary and the Certificate Holder's own insurance is excess and non-contributory. |

## Common Deficiency Patterns and Cure Requests

- **Box checked `Y`, zero endorsement pages**: Flag `ENDORSEMENT_DOC_MISSING`. Request actual `CG 20 10`, `CG 20 37`, and `CG 24 04` endorsement pages (or carrier equivalent blanket endorsements) from the broker.
- **Ongoing operations only (`CG 20 10` attached, `CG 20 37` missing)**: Note that completed operations coverage is unverified and request `CG 20 37` before final retainage release.
- **Per-policy GL aggregate (`POLICY` checked instead of `PRO-JECT`)**: Flag `GL_AGG_BASIS_MISMATCH`. Request per-project aggregate endorsement (typically `CG 25 03`) and an updated ACORD 25 with `PRO-JECT` marked.
- **Wrong Certificate Holder legal name**: Flag `CERT_HOLDER_MISMATCH`. Request reissued ACORD 25 using the exact legal entity name and address from the executed subcontract.
- **Description of Operations omits Primary and Non-Contributory wording**: Flag `PNC_WORDING_MISSING` when `require_primary_non_contributory: true`. Request updated certificate wording plus `CG 20 01` (or equivalent policy endorsement).

## Operational Disclaimer

This checklist supports operational compliance triage for general contractors, accounts payable teams, and property managers. It is not legal advice or insurance brokerage advice. Escalate disputed manuscript endorsements or non-standard carrier exclusions to legal counsel or a licensed risk advisor.
