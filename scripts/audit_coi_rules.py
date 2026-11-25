#!/usr/bin/env python3
"""Deterministic ACORD 25 contract rule validator and deliverable generator.

Validates coi_extracted.json against a contract requirements.yaml (or JSON)
schema as of a reference audit date, classifies each vendor, and writes:
  - coi_compliance_register.csv
  - coi_audit_report.md
  - deficiency_emails/<vendor_slug>_deficiency.md

Exit codes:
  0 - Audit completed cleanly
  2 - Invalid arguments or missing input files
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


CSV_COLUMNS = [
    "vendor_name",
    "broker_email",
    "gl_occ_limit",
    "gl_agg_limit",
    "gl_agg_basis",
    "wc_exp_date",
    "days_to_next_expiry",
    "addl_insd_flag",
    "wos_flag",
    "endorsement_attached",
    "cert_holder_match",
    "status",
    "deficiency_codes",
]

DEFAULT_TEMPLATE = """Subject: Action Required: Certificate of Insurance (COI) Compliance Deficiencies - {vendor_name}

To: {broker_email}
Cc: Subcontractor Compliance / Accounts Payable

Hello,

We completed an operational compliance review of the ACORD 25 Certificate of Insurance submitted for **{vendor_name}** (Audit Date: {audit_date}, Status: **{status}**).

To clear payment holds and maintain active site compliance, please provide a revised ACORD 25 certificate and supporting policy endorsement pages addressing the following items:

{deficiency_bullets}

### Required Certificate Holder Block

```text
{certificate_holder_name}
{certificate_holder_address}
```

### Required Endorsement Attachments

Per the ACORD 25 notice, checked `ADDL INSD` and `SUBR WVD` boxes alone confer no policy rights without attached endorsement forms. Please attach actual carrier endorsement pages for:

{required_endorsement_bullets}

Please reply with the updated PDF packet at your earliest convenience.

Note: This message is an operational contract-compliance check and does not constitute legal or insurance brokerage advice.
"""


def load_requirements(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    if yaml is None:
        raise RuntimeError("PyYAML is required to read YAML requirements files. Install pyyaml or pass a JSON file.")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("Requirements file must parse to a YAML/JSON mapping.")
    return data


def normalize_entity_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", " ", name.upper()).strip()
    return re.sub(r"\s+", " ", cleaned)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "unknown_vendor"


def parse_iso_date(val: Any) -> date | None:
    if not isinstance(val, str):
        return None
    try:
        return datetime.strptime(val.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def fmt_money(val: Any) -> str:
    if isinstance(val, (int, float)):
        return f"${int(val):,}"
    return str(val) if val is not None else ""


def evaluate_record(
    rec: dict[str, Any],
    reqs: dict[str, Any],
    as_of: date,
    warning_days: int,
) -> dict[str, Any]:
    vendor_name = str(rec.get("vendor_name") or rec.get("source_file") or "UNKNOWN")
    broker_email = str(rec.get("broker_email") or "NEEDS_HUMAN_CHECK")
    flags = list(rec.get("flags") or [])

    if rec.get("is_scanned") or "SCANNED_PDF" in flags or "UNREADABLE_PDF" in flags:
        code = "SCANNED_PDF" if (rec.get("is_scanned") or "SCANNED_PDF" in flags) else "UNREADABLE_PDF"
        return {
            "source_file": rec.get("source_file"),
            "vendor_name": vendor_name,
            "broker_email": "" if rec.get("is_scanned") else broker_email,
            "gl_occ_limit": "",
            "gl_agg_limit": "",
            "gl_agg_basis": "",
            "wc_exp_date": "",
            "days_to_next_expiry": "",
            "addl_insd_flag": "",
            "wos_flag": "",
            "endorsement_attached": "N",
            "cert_holder_match": "N",
            "status": "MANUAL_REVIEW_REQUIRED",
            "deficiency_codes": [code],
            "deficiency_details": [
                f"{code}: PDF has no extractable digital text (or could not be parsed); manual review or digital ACORD 25 required. Zero limits or dates were fabricated."
            ],
            "citations": rec.get("citations") or {},
            "endorsement_forms_detected": [],
        }

    limits = reqs.get("limits", {})
    endorsements = reqs.get("endorsements", {})
    req_holder = str(reqs.get("certificate_holder_name") or "")

    cov = rec.get("coverages", {})
    gl = cov.get("general_liability", {})
    auto = cov.get("automobile_liability", {})
    umb = cov.get("umbrella_liability", {})
    wc = cov.get("workers_compensation", {})
    desc_signals = rec.get("description_signals", {})
    holder = rec.get("certificate_holder", {})
    citations = rec.get("citations", {})

    def cite_str(key: str) -> str:
        c = citations.get(key)
        if not c:
            return f"{rec.get('source_file')}"
        return f"{rec.get('source_file')} p.{c['page']} L{c['line']}"

    hard_codes: list[str] = []
    warn_codes: list[str] = []
    manual_codes: list[str] = []
    details: list[str] = []

    # 1. Policy dates and expiry arithmetic
    expiry_days: list[int] = []
    policy_checks = [
        ("GL", "general_liability", gl.get("exp_date"), True),
        ("AUTO", "automobile_liability", auto.get("exp_date"), int(limits.get("auto_combined_single_limit_min", 0)) > 0),
        ("UMBRELLA", "umbrella_liability", umb.get("exp_date"), int(limits.get("umbrella_each_occurrence_min", 0)) > 0),
        ("WC", "workers_compensation", wc.get("exp_date"), bool(limits.get("wc_statutory_required", True))),
    ]
    for label, prefix, raw_exp, required in policy_checks:
        exp_dt = parse_iso_date(raw_exp)
        if exp_dt is None:
            if required:
                manual_codes.append(f"{label}_EXP_NEEDS_CHECK")
                details.append(f"{label}_EXP_NEEDS_CHECK: Expiration date missing or unreadable ({cite_str(prefix + '.exp_date')}).")
            continue
        delta = (exp_dt - as_of).days
        expiry_days.append(delta)
        if delta < 0:
            hard_codes.append(f"{label}_EXPIRED")
            details.append(f"{label}_EXPIRED: Policy expired on {exp_dt.isoformat()} ({abs(delta)} days past {as_of.isoformat()}; {cite_str(prefix + '.exp_date')}).")
        elif delta <= warning_days:
            warn_codes.append(f"{label}_EXPIRING_SOON")
            details.append(f"{label}_EXPIRING_SOON: Policy expires on {exp_dt.isoformat()} ({delta} days from {as_of.isoformat()}; {cite_str(prefix + '.exp_date')}).")

    days_to_next_expiry: int | str = min(expiry_days) if expiry_days else "NEEDS_HUMAN_CHECK"

    # 2. Dollar limit comparisons
    def check_limit(actual: Any, minimum: int, code: str, label: str, cite_key: str) -> None:
        if minimum <= 0:
            return
        if actual == "NEEDS_HUMAN_CHECK" or actual is None:
            manual_codes.append(f"{code}_NEEDS_CHECK")
            details.append(f"{code}_NEEDS_CHECK: {label} limit missing or unreadable (required {fmt_money(minimum)}; {cite_str(cite_key)}).")
        elif isinstance(actual, (int, float)) and actual < minimum:
            hard_codes.append(code)
            details.append(f"{code}: {label} is {fmt_money(actual)}, below required minimum {fmt_money(minimum)} ({cite_str(cite_key)}).")

    check_limit(gl.get("each_occurrence"), int(limits.get("gl_each_occurrence_min", 0)), "GL_OCC_BELOW_MIN", "GL Each Occurrence", "general_liability.each_occurrence")
    check_limit(gl.get("general_aggregate"), int(limits.get("gl_general_aggregate_min", 0)), "GL_AGG_BELOW_MIN", "GL General Aggregate", "general_liability.general_aggregate")
    check_limit(gl.get("products_comp_op_agg"), int(limits.get("gl_products_comp_op_agg_min", 0)), "GL_PROD_AGG_BELOW_MIN", "GL Products-Comp/Op Aggregate", "general_liability.products_comp_op_agg")
    check_limit(auto.get("combined_single_limit"), int(limits.get("auto_combined_single_limit_min", 0)), "AUTO_CSL_BELOW_MIN", "Auto Combined Single Limit", "automobile_liability.combined_single_limit")
    check_limit(umb.get("each_occurrence"), int(limits.get("umbrella_each_occurrence_min", 0)), "UMBRELLA_OCC_BELOW_MIN", "Umbrella Each Occurrence", "umbrella_liability.each_occurrence")

    # Workers Comp STATUTORY + Employers Liability
    if limits.get("wc_statutory_required", True):
        if wc.get("statutory_limits") is True:
            pass
        elif wc.get("statutory_limits") == "NEEDS_HUMAN_CHECK":
            manual_codes.append("WC_STATUTORY_NEEDS_CHECK")
            details.append(f"WC_STATUTORY_NEEDS_CHECK: Workers' Comp STATUTORY box could not be verified ({cite_str('workers_compensation.statutory_limits')}).")
        else:
            hard_codes.append("WC_STATUTORY_MISSING")
            details.append(f"WC_STATUTORY_MISSING: Workers' Comp PER STATUTE / STATUTORY limit is not marked ({cite_str('workers_compensation.statutory_limits')}).")

    check_limit(wc.get("el_each_accident"), int(limits.get("el_each_accident_min", 0)), "WC_EL_ACC_BELOW_MIN", "WC E.L. Each Accident", "workers_compensation.el_each_accident")
    check_limit(wc.get("el_disease_ea_employee"), int(limits.get("el_disease_ea_employee_min", 0)), "WC_EL_EMP_BELOW_MIN", "WC E.L. Disease - Ea Employee", "workers_compensation.el_disease_ea_employee")
    check_limit(wc.get("el_disease_policy_limit"), int(limits.get("el_disease_policy_limit_min", 0)), "WC_EL_POL_BELOW_MIN", "WC E.L. Disease - Policy Limit", "workers_compensation.el_disease_policy_limit")

    # 3. GL aggregate basis (PROJECT vs POLICY/LOC)
    req_basis = str(limits.get("gl_aggregate_basis_required") or "").upper()
    actual_basis = str(gl.get("aggregate_basis") or "NEEDS_HUMAN_CHECK").upper()
    if req_basis:
        if actual_basis == "NEEDS_HUMAN_CHECK":
            manual_codes.append("GL_AGG_BASIS_NEEDS_CHECK")
            details.append(f"GL_AGG_BASIS_NEEDS_CHECK: GL aggregate basis checkbox (POLICY/PRO-JECT/LOC) unclear; contract requires {req_basis} ({cite_str('general_liability.aggregate_basis')}).")
        elif actual_basis != req_basis:
            hard_codes.append("GL_AGG_BASIS_MISMATCH")
            details.append(f"GL_AGG_BASIS_MISMATCH: GL aggregate applies per {actual_basis}, but contract requires per-{req_basis} ({cite_str('general_liability.aggregate_basis')}).")

    # 4. Certificate Holder exact legal name match
    actual_holder = str(holder.get("name") or "NEEDS_HUMAN_CHECK")
    cert_holder_match = "Y"
    if req_holder:
        if actual_holder == "NEEDS_HUMAN_CHECK":
            cert_holder_match = "NEEDS_HUMAN_CHECK"
            manual_codes.append("CERT_HOLDER_NEEDS_CHECK")
            details.append(f"CERT_HOLDER_NEEDS_CHECK: Certificate Holder block could not be extracted; expected '{req_holder}' ({cite_str('certificate_holder')}).")
        elif normalize_entity_name(actual_holder) != normalize_entity_name(req_holder):
            cert_holder_match = "N"
            hard_codes.append("CERT_HOLDER_MISMATCH")
            details.append(f"CERT_HOLDER_MISMATCH: Certificate Holder is '{actual_holder}', expected '{req_holder}' ({cite_str('certificate_holder')}).")

    # 5. ADDL INSD, SUBR WVD, and Primary & Non-Contributory wording
    addl_flag = str(gl.get("addl_insd") or "NEEDS_HUMAN_CHECK")
    gl_wos = str(gl.get("subr_wvd") or "NEEDS_HUMAN_CHECK")
    wc_wos = str(wc.get("subr_wvd") or "NEEDS_HUMAN_CHECK")
    wos_flag = "Y" if (gl_wos == "Y" or wc_wos == "Y") else ("N" if (gl_wos == "N" and wc_wos == "N") else "NEEDS_HUMAN_CHECK")

    if endorsements.get("require_additional_insured", True):
        if addl_flag == "N":
            hard_codes.append("ADDL_INSD_MISSING")
            details.append(f"ADDL_INSD_MISSING: GL ADDL INSD box is not marked Y ({cite_str('general_liability.addl_insd')}).")
        elif addl_flag == "NEEDS_HUMAN_CHECK":
            manual_codes.append("ADDL_INSD_NEEDS_CHECK")
            details.append(f"ADDL_INSD_NEEDS_CHECK: GL ADDL INSD box could not be confirmed ({cite_str('general_liability.addl_insd')}).")

    if endorsements.get("require_waiver_of_subrogation", True):
        if wos_flag == "N":
            hard_codes.append("WOS_MISSING")
            details.append(f"WOS_MISSING: SUBR WVD box is not marked Y ({cite_str('general_liability.subr_wvd')}).")
        elif wos_flag == "NEEDS_HUMAN_CHECK":
            manual_codes.append("WOS_NEEDS_CHECK")
            details.append(f"WOS_NEEDS_CHECK: SUBR WVD box could not be confirmed ({cite_str('general_liability.subr_wvd')}).")

    if endorsements.get("require_primary_non_contributory", True):
        if not desc_signals.get("mentions_primary_non_contributory", False):
            hard_codes.append("PNC_WORDING_MISSING")
            details.append(f"PNC_WORDING_MISSING: Description of Operations does not confirm Primary and Non-Contributory wording ({cite_str('description_of_operations')}).")

    # 6. Attached endorsement pages check (ACORD 25 2025/12 notice)
    detected_forms = set(rec.get("endorsement_forms_detected") or [])
    required_forms = list(endorsements.get("required_endorsement_forms") or ["CG 20 10", "CG 20 37", "CG 24 04"])
    missing_forms = [f for f in required_forms if f not in detected_forms]
    endorsement_attached = "Y" if (detected_forms and not missing_forms) else "N"

    if endorsements.get("require_endorsement_attachments", True) and missing_forms:
        hard_codes.append("ENDORSEMENT_DOC_MISSING")
        details.append(
            f"ENDORSEMENT_DOC_MISSING: Missing attached endorsement page(s): {', '.join(missing_forms)}. "
            "Per the ACORD 25 (2025/12) notice, checked ADDL INSD / SUBR WVD boxes confer no rights without attached policy endorsements."
        )

    all_codes = hard_codes + manual_codes + warn_codes
    if hard_codes:
        status = "HOLD_DEFICIENT"
    elif manual_codes:
        status = "MANUAL_REVIEW_REQUIRED"
    elif warn_codes:
        status = "EXPIRING_SOON"
    else:
        status = "PASS"

    return {
        "source_file": rec.get("source_file"),
        "vendor_name": vendor_name,
        "broker_email": broker_email,
        "gl_occ_limit": gl.get("each_occurrence", ""),
        "gl_agg_limit": gl.get("general_aggregate", ""),
        "gl_agg_basis": actual_basis,
        "wc_exp_date": wc.get("exp_date", ""),
        "days_to_next_expiry": days_to_next_expiry,
        "addl_insd_flag": addl_flag,
        "wos_flag": wos_flag,
        "endorsement_attached": endorsement_attached,
        "cert_holder_match": cert_holder_match,
        "status": status,
        "deficiency_codes": all_codes,
        "deficiency_details": details,
        "citations": citations,
        "endorsement_forms_detected": sorted(detected_forms),
    }


def write_csv(rows: list[dict[str, Any]], out_path: Path) -> None:
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {
                    "vendor_name": r["vendor_name"],
                    "broker_email": r["broker_email"],
                    "gl_occ_limit": r["gl_occ_limit"],
                    "gl_agg_limit": r["gl_agg_limit"],
                    "gl_agg_basis": r["gl_agg_basis"],
                    "wc_exp_date": r["wc_exp_date"],
                    "days_to_next_expiry": r["days_to_next_expiry"],
                    "addl_insd_flag": r["addl_insd_flag"],
                    "wos_flag": r["wos_flag"],
                    "endorsement_attached": r["endorsement_attached"],
                    "cert_holder_match": r["cert_holder_match"],
                    "status": r["status"],
                    "deficiency_codes": ";".join(r["deficiency_codes"]),
                }
            )


def write_report(
    rows: list[dict[str, Any]],
    skipped_files: list[dict[str, Any]],
    reqs: dict[str, Any],
    as_of: date,
    warning_days: int,
    out_path: Path,
) -> None:
    counts = {
        "PASS": sum(1 for r in rows if r["status"] == "PASS"),
        "EXPIRING_SOON": sum(1 for r in rows if r["status"] == "EXPIRING_SOON"),
        "HOLD_DEFICIENT": sum(1 for r in rows if r["status"] == "HOLD_DEFICIENT"),
        "MANUAL_REVIEW_REQUIRED": sum(1 for r in rows if r["status"] == "MANUAL_REVIEW_REQUIRED"),
    }

    lines = [
        "# ACORD 25 COI Compliance Audit Report",
        "",
        f"- **Audit Date**: {as_of.isoformat()}",
        f"- **Requirements Profile**: {reqs.get('profile_name', 'Custom Contract Profile')}",
        f"- **Certificate Holder**: {reqs.get('certificate_holder_name', 'Not specified')}",
        f"- **Expiry Warning Window**: {warning_days} days",
        f"- **Total Vendors Audited**: {len(rows)}",
        "",
        "## Status Summary",
        "",
        "| Status | Count |",
        "| --- | --- |",
        f"| PASS | {counts['PASS']} |",
        f"| EXPIRING_SOON | {counts['EXPIRING_SOON']} |",
        f"| HOLD_DEFICIENT | {counts['HOLD_DEFICIENT']} |",
        f"| MANUAL_REVIEW_REQUIRED | {counts['MANUAL_REVIEW_REQUIRED']} |",
        "",
        "## Per-Vendor Exception Table",
        "",
        "| Vendor | Source File | Status | Days to Next Expiry | Deficiency Codes | Key Citations / Notes |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    for r in rows:
        codes = ", ".join(r["deficiency_codes"]) if r["deficiency_codes"] else "NONE"
        notes = "; ".join(r["deficiency_details"]) if r["deficiency_details"] else "All checked contract requirements satisfied."
        notes = notes.replace("|", "/")
        lines.append(
            f"| {r['vendor_name']} | {r['source_file']} | {r['status']} | {r['days_to_next_expiry']} | {codes} | {notes} |"
        )

    lines.extend(
        [
            "",
            "## Endorsement Verification Notes",
            "",
            "Per the official ACORD 25 (2025/12) notice, a certificate of insurance is issued as a matter of information only and confers no rights upon the Certificate Holder. Checked `ADDL INSD` or `SUBR WVD` boxes do not amend the underlying policy unless actual carrier endorsement pages (such as `CG 20 10`, `CG 20 37`, and `CG 24 04`) are attached and verified.",
            "",
        ]
    )

    if skipped_files:
        lines.extend(["## Skipped Non-COI Files", ""])
        for sf in skipped_files:
            lines.append(f"- `{sf['source_file']}`: skipped cleanly ({sf.get('reason', 'NON_COI_PDF')}).")
        lines.append("")

    lines.extend(
        [
            "## Operational Disclaimer",
            "",
            "This report is an operational contract-compliance check for general contractors, accounts payable clerks, and property managers. It is not legal advice or insurance brokerage advice.",
            "",
        ]
    )

    out_path.write_text("\n".join(lines), encoding="utf-8")


def write_deficiency_emails(
    rows: list[dict[str, Any]],
    reqs: dict[str, Any],
    as_of: date,
    template_text: str,
    emails_dir: Path,
) -> list[str]:
    emails_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    req_forms = list(reqs.get("endorsements", {}).get("required_endorsement_forms") or ["CG 20 10", "CG 20 37", "CG 24 04"])
    req_endorsement_bullets = "\n".join(f"- `{form}`" for form in req_forms)

    for r in rows:
        if r["status"] == "PASS":
            continue
        slug = slugify(r["vendor_name"])
        email_path = emails_dir / f"{slug}_deficiency.md"
        bullets = "\n".join(f"- {d}" for d in r["deficiency_details"])
        content = template_text.format(
            vendor_name=r["vendor_name"],
            broker_email=r["broker_email"] or "BROKER_EMAIL_REQUIRED",
            audit_date=as_of.isoformat(),
            status=r["status"],
            deficiency_bullets=bullets,
            certificate_holder_name=reqs.get("certificate_holder_name", ""),
            certificate_holder_address=reqs.get("certificate_holder_address", ""),
            required_endorsement_bullets=req_endorsement_bullets,
        )
        email_path.write_text(content, encoding="utf-8")
        written.append(email_path.as_posix())
    return written


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="audit_coi_rules.py",
        description="Validate extracted ACORD 25 JSON against contract insurance requirements and generate compliance deliverables.",
        epilog="Exit codes: 0 = audit completed, 2 = invalid arguments or missing files.",
    )
    parser.add_argument("--extracted", required=True, help="Path to coi_extracted.json from extract_acord25.py.")
    parser.add_argument("--requirements", required=True, help="Path to contract requirements YAML or JSON file.")
    parser.add_argument("--as-of", help="Reference audit date in YYYY-MM-DD format (default: today).")
    parser.add_argument("--warning-days", type=int, help="Expiry warning window in days (overrides requirements YAML; default: 30).")
    parser.add_argument("--template", help="Optional path to deficiency_email_template.md.")
    parser.add_argument("--out-dir", required=True, help="Output directory for CSV register, report, and deficiency_emails/.")
    args = parser.parse_args()

    extracted_path = Path(args.extracted).resolve()
    req_path = Path(args.requirements).resolve()
    out_dir = Path(args.out_dir).resolve()

    if not extracted_path.is_file():
        print(f"Error: Extracted JSON not found: {extracted_path}", file=sys.stderr)
        return 2
    if not req_path.is_file():
        print(f"Error: Requirements file not found: {req_path}", file=sys.stderr)
        return 2

    if args.as_of:
        as_of = parse_iso_date(args.as_of)
        if as_of is None:
            print(f"Error: Invalid --as-of date '{args.as_of}'. Expected YYYY-MM-DD.", file=sys.stderr)
            return 2
    else:
        as_of = date.today()

    try:
        extracted = json.loads(extracted_path.read_text(encoding="utf-8"))
        reqs = load_requirements(req_path)
    except Exception as exc:
        print(f"Error loading inputs: {exc}", file=sys.stderr)
        return 2

    warning_days = args.warning_days if args.warning_days is not None else int(reqs.get("expiry_warning_days", 30))
    template_text = DEFAULT_TEMPLATE
    if args.template:
        tpath = Path(args.template).resolve()
        if not tpath.is_file():
            print(f"Error: Template file not found: {tpath}", file=sys.stderr)
            return 2
        template_text = tpath.read_text(encoding="utf-8")

    records = extracted.get("records", [])
    skipped_files = extracted.get("skipped_files", [])
    evaluated = [evaluate_record(r, reqs, as_of, warning_days) for r in records]

    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "coi_compliance_register.csv"
    report_path = out_dir / "coi_audit_report.md"
    emails_dir = out_dir / "deficiency_emails"

    write_csv(evaluated, csv_path)
    write_report(evaluated, skipped_files, reqs, as_of, warning_days, report_path)
    email_files = write_deficiency_emails(evaluated, reqs, as_of, template_text, emails_dir)

    status_counts = {
        "PASS": sum(1 for r in evaluated if r["status"] == "PASS"),
        "EXPIRING_SOON": sum(1 for r in evaluated if r["status"] == "EXPIRING_SOON"),
        "HOLD_DEFICIENT": sum(1 for r in evaluated if r["status"] == "HOLD_DEFICIENT"),
        "MANUAL_REVIEW_REQUIRED": sum(1 for r in evaluated if r["status"] == "MANUAL_REVIEW_REQUIRED"),
    }

    summary = {
        "audit_date": as_of.isoformat(),
        "warning_days": warning_days,
        "vendors_audited": len(evaluated),
        "skipped_non_coi_files": len(skipped_files),
        "status_counts": status_counts,
        "outputs": {
            "compliance_register_csv": csv_path.as_posix(),
            "audit_report_md": report_path.as_posix(),
            "deficiency_emails": email_files,
        },
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
