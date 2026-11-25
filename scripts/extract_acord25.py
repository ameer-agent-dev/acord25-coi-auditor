#!/usr/bin/env python3
"""Deterministic ACORD 25 Certificate of Insurance PDF extractor.

Scans a single PDF or directory of PDFs using pdfplumber, separates ACORD 25
certificate pages from endorsement attachments and unrelated pages (such as
Form W-9), flags scanned/image-only PDFs cleanly without fabricating values,
and writes structured JSON with exact page and line citations.

Exit codes:
  0 - Extraction completed cleanly
  2 - Invalid arguments, missing input path, or missing pdfplumber dependency
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import pdfplumber
except ImportError:
    pdfplumber = None  # type: ignore[assignment]


DATE_RE = re.compile(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})\b")
MONEY_RE = re.compile(r"\$\s*([0-9][0-9,]*(?:\.[0-9]{2})?)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")

ENDORSEMENT_FORM_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("CG 20 10", re.compile(r"\bCG\s*20\s*10\b", re.IGNORECASE)),
    ("CG 20 37", re.compile(r"\bCG\s*20\s*37\b", re.IGNORECASE)),
    ("CG 24 04", re.compile(r"\bCG\s*24\s*04\b", re.IGNORECASE)),
    ("CG 20 01", re.compile(r"\bCG\s*20\s*01\b", re.IGNORECASE)),
    ("CG 25 03", re.compile(r"\bCG\s*25\s*03\b", re.IGNORECASE)),
    ("WC 00 03 13", re.compile(r"\bWC\s*00\s*03\s*13\b", re.IGNORECASE)),
]


def normalize_date(raw: str) -> str | None:
    raw = raw.strip()
    for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%m/%d/%y", "%m-%d-%y"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse_money(raw: str) -> int | None:
    m = MONEY_RE.search(raw)
    if not m:
        return None
    cleaned = m.group(1).replace(",", "")
    try:
        return int(round(float(cleaned)))
    except ValueError:
        return None


def detect_endorsement_forms(text: str) -> list[str]:
    found: list[str] = []
    for code, pat in ENDORSEMENT_FORM_PATTERNS:
        if pat.search(text):
            found.append(code)
    return found


def classify_page(text: str, min_text_chars: int) -> str:
    stripped = text.strip()
    if len(stripped) < min_text_chars:
        return "SCANNED"
    upper = stripped.upper()
    if "CERTIFICATE OF LIABILITY INSURANCE" in upper or "ACORD 25" in upper:
        return "ACORD_25"
    if "FORM W-9" in upper or "REQUEST FOR TAXPAYER IDENTIFICATION" in upper:
        return "W9_OR_UNRELATED"
    if (
        detect_endorsement_forms(stripped)
        or "THIS ENDORSEMENT CHANGES THE POLICY" in upper
        or "WAIVER OF TRANSFER OF RIGHTS" in upper
        or "ADDITIONAL INSURED - OWNERS" in upper
    ):
        return "ENDORSEMENT"
    return "W9_OR_UNRELATED"


def find_line_value(
    lines: list[tuple[int, int, str]],
    pattern: re.Pattern[str],
    start_idx: int = 0,
    end_idx: int | None = None,
) -> tuple[int, int, str, re.Match[str]] | None:
    subset = lines[start_idx:end_idx]
    for page_no, line_no, text in subset:
        m = pattern.search(text)
        if m:
            return page_no, line_no, text, m
    return None


def extract_flag_after_label(text: str, label_re: re.Pattern[str]) -> str | None:
    m = label_re.search(text)
    if not m:
        return None
    val = m.group(1).strip().upper()
    if val in ("Y", "YES", "X", "[X]", "(X)"):
        return "Y"
    if val in ("N", "NO", "[]", "[ ]", "NONE"):
        return "N"
    return None


def extract_acord25_fields(
    file_path: Path,
    acord_pages: list[tuple[int, str]],
    endorsement_pages: list[tuple[int, str, list[str]]],
    skipped_pages: list[dict[str, Any]],
) -> dict[str, Any]:
    lines: list[tuple[int, int, str]] = []
    for page_no, text in acord_pages:
        for idx, line in enumerate(text.splitlines(), start=1):
            cleaned = line.strip()
            if cleaned:
                lines.append((page_no, idx, cleaned))

    citations: dict[str, dict[str, Any]] = {}

    def cite(field: str, page_no: int, line_no: int, snippet: str) -> None:
        citations[field] = {
            "page": page_no,
            "line": line_no,
            "snippet": snippet[:160],
        }

    # Producer and broker email
    producer_name: str = "NEEDS_HUMAN_CHECK"
    broker_email: str = "NEEDS_HUMAN_CHECK"
    producer_phone: str | None = None

    for i, (pg, ln, txt) in enumerate(lines):
        up = txt.upper()
        if up.startswith("PRODUCER:") or up == "PRODUCER":
            after = txt.split(":", 1)[1].strip() if ":" in txt else ""
            if after:
                producer_name = after
                cite("producer_name", pg, ln, txt)
            elif i + 1 < len(lines):
                producer_name = lines[i + 1][2]
                cite("producer_name", lines[i + 1][0], lines[i + 1][1], lines[i + 1][2])
            break

    for pg, ln, txt in lines:
        em = EMAIL_RE.search(txt)
        if em:
            broker_email = em.group(0)
            cite("broker_email", pg, ln, txt)
            break

    for pg, ln, txt in lines:
        ph = PHONE_RE.search(txt)
        if ph:
            producer_phone = ph.group(0)
            break

    # Vendor / Insured name
    vendor_name: str = "NEEDS_HUMAN_CHECK"
    for i, (pg, ln, txt) in enumerate(lines):
        up = txt.upper()
        if re.match(r"^INSURED\b", up):
            after = txt.split(":", 1)[1].strip() if ":" in txt else ""
            if after and not after.upper().startswith("PRODUCER"):
                vendor_name = after
                cite("vendor_name", pg, ln, txt)
            elif i + 1 < len(lines):
                vendor_name = lines[i + 1][2]
                cite("vendor_name", lines[i + 1][0], lines[i + 1][1], lines[i + 1][2])
            break

    # Section slicing for coverages
    def find_section_idx(markers: tuple[str, ...]) -> int | None:
        for i, (_, _, txt) in enumerate(lines):
            up = txt.upper()
            if any(m in up for m in markers):
                return i
        return None

    gl_idx = find_section_idx(("COMMERCIAL GENERAL LIABILITY", "GENERAL LIABILITY"))
    auto_idx = find_section_idx(("AUTOMOBILE LIABILITY",))
    umb_idx = find_section_idx(("UMBRELLA LIAB", "EXCESS LIAB", "UMBRELLA LIABILITY"))
    wc_idx = find_section_idx(("WORKERS COMPENSATION", "WORKERS' COMPENSATION"))
    desc_idx = find_section_idx(("DESCRIPTION OF OPERATIONS",))
    holder_idx = find_section_idx(("CERTIFICATE HOLDER",))

    section_starts = sorted(
        x for x in (gl_idx, auto_idx, umb_idx, wc_idx, desc_idx, holder_idx) if x is not None
    )

    def section_slice(start: int | None) -> list[tuple[int, int, str]]:
        if start is None:
            return []
        next_starts = [s for s in section_starts if s > start]
        end = next_starts[0] if next_starts else len(lines)
        return lines[start:end]

    def parse_dates_and_policy(
        sec: list[tuple[int, int, str]], prefix: str
    ) -> tuple[str | None, str | None, str | None]:
        policy_no: str | None = None
        eff_date: str | None = None
        exp_date: str | None = None
        for pg, ln, txt in sec:
            if policy_no is None:
                pm = re.search(r"POLICY\s*(?:NUMBER|NO\.?|#)\s*[:\-]?\s*([A-Za-z0-9\-/]+)", txt, re.IGNORECASE)
                if pm:
                    policy_no = pm.group(1)
                    cite(f"{prefix}.policy_number", pg, ln, txt)
            dates = DATE_RE.findall(txt)
            if len(dates) >= 2 and eff_date is None and exp_date is None:
                d1 = normalize_date(dates[0])
                d2 = normalize_date(dates[1])
                if d1 and d2:
                    eff_date, exp_date = d1, d2
                    cite(f"{prefix}.eff_date", pg, ln, txt)
                    cite(f"{prefix}.exp_date", pg, ln, txt)
            elif len(dates) == 1:
                up = txt.upper()
                d = normalize_date(dates[0])
                if d:
                    if "EFF" in up and eff_date is None:
                        eff_date = d
                        cite(f"{prefix}.eff_date", pg, ln, txt)
                    elif "EXP" in up and exp_date is None:
                        exp_date = d
                        cite(f"{prefix}.exp_date", pg, ln, txt)
        return policy_no, eff_date, exp_date

    def parse_flags(sec: list[tuple[int, int, str]], prefix: str) -> tuple[str, str]:
        addl = "NEEDS_HUMAN_CHECK"
        wos = "NEEDS_HUMAN_CHECK"
        addl_re = re.compile(r"ADDL\s*INSD\s*[:\-]?\s*(\[?[A-Za-z ]?\]?)", re.IGNORECASE)
        wos_re = re.compile(r"SUBR\s*WVD\s*[:\-]?\s*(\[?[A-Za-z ]?\]?)", re.IGNORECASE)
        for pg, ln, txt in sec:
            if addl == "NEEDS_HUMAN_CHECK":
                val = extract_flag_after_label(txt, addl_re)
                if val:
                    addl = val
                    cite(f"{prefix}.addl_insd", pg, ln, txt)
            if wos == "NEEDS_HUMAN_CHECK":
                val = extract_flag_after_label(txt, wos_re)
                if val:
                    wos = val
                    cite(f"{prefix}.subr_wvd", pg, ln, txt)
        return addl, wos

    # General Liability
    gl_sec = section_slice(gl_idx)
    gl_pol, gl_eff, gl_exp = parse_dates_and_policy(gl_sec, "general_liability")
    gl_addl, gl_wos = parse_flags(gl_sec, "general_liability")
    gl_occ: int | str = "NEEDS_HUMAN_CHECK"
    gl_agg: int | str = "NEEDS_HUMAN_CHECK"
    gl_prod: int | str = "NEEDS_HUMAN_CHECK"
    gl_basis: str = "NEEDS_HUMAN_CHECK"

    for pg, ln, txt in gl_sec:
        up = txt.upper()
        if "EACH OCCURRENCE" in up and gl_occ == "NEEDS_HUMAN_CHECK":
            val = parse_money(txt)
            if val is not None:
                gl_occ = val
                cite("general_liability.each_occurrence", pg, ln, txt)
        if "GENERAL AGGREGATE" in up and gl_agg == "NEEDS_HUMAN_CHECK":
            val = parse_money(txt)
            if val is not None:
                gl_agg = val
                cite("general_liability.general_aggregate", pg, ln, txt)
        if ("PRODUCTS" in up and "COMP/OP" in up) and gl_prod == "NEEDS_HUMAN_CHECK":
            val = parse_money(txt)
            if val is not None:
                gl_prod = val
                cite("general_liability.products_comp_op_agg", pg, ln, txt)
        if gl_basis == "NEEDS_HUMAN_CHECK":
            if re.search(r"(?:\[X\]\s*|\(X\)\s*|X\s+)(?:PRO-?JECT|PROJECT)", up) or re.search(
                r"AGGREGATE\s+(?:LIMIT\s+)?APPLIES\s+PER\s*[:\-]?\s*(?:PRO-?JECT|PROJECT)\b", up
            ):
                gl_basis = "PROJECT"
                cite("general_liability.aggregate_basis", pg, ln, txt)
            elif re.search(r"(?:\[X\]\s*|\(X\)\s*|X\s+)POLICY\b", up) or re.search(
                r"AGGREGATE\s+(?:LIMIT\s+)?APPLIES\s+PER\s*[:\-]?\s*POLICY\b", up
            ):
                gl_basis = "POLICY"
                cite("general_liability.aggregate_basis", pg, ln, txt)
            elif re.search(r"(?:\[X\]\s*|\(X\)\s*|X\s+)LOC\b", up) or re.search(
                r"AGGREGATE\s+(?:LIMIT\s+)?APPLIES\s+PER\s*[:\-]?\s*LOC\b", up
            ):
                gl_basis = "LOC"
                cite("general_liability.aggregate_basis", pg, ln, txt)

    # Automobile Liability
    auto_sec = section_slice(auto_idx)
    auto_pol, auto_eff, auto_exp = parse_dates_and_policy(auto_sec, "automobile_liability")
    auto_addl, auto_wos = parse_flags(auto_sec, "automobile_liability")
    auto_csl: int | str | None = "NEEDS_HUMAN_CHECK" if auto_sec else None
    for pg, ln, txt in auto_sec:
        up = txt.upper()
        if "COMBINED SINGLE LIMIT" in up:
            val = parse_money(txt)
            if val is not None:
                auto_csl = val
                cite("automobile_liability.combined_single_limit", pg, ln, txt)

    # Umbrella / Excess Liability
    umb_sec = section_slice(umb_idx)
    umb_pol, umb_eff, umb_exp = parse_dates_and_policy(umb_sec, "umbrella_liability")
    umb_addl, umb_wos = parse_flags(umb_sec, "umbrella_liability")
    umb_occ: int | str | None = "NEEDS_HUMAN_CHECK" if umb_sec else None
    umb_agg: int | str | None = "NEEDS_HUMAN_CHECK" if umb_sec else None
    for pg, ln, txt in umb_sec:
        up = txt.upper()
        if "EACH OCCURRENCE" in up:
            val = parse_money(txt)
            if val is not None:
                umb_occ = val
                cite("umbrella_liability.each_occurrence", pg, ln, txt)
        if "AGGREGATE" in up:
            val = parse_money(txt)
            if val is not None:
                umb_agg = val
                cite("umbrella_liability.aggregate", pg, ln, txt)

    # Workers Compensation & Employers Liability
    wc_sec = section_slice(wc_idx)
    wc_pol, wc_eff, wc_exp = parse_dates_and_policy(wc_sec, "workers_compensation")
    _, wc_wos = parse_flags(wc_sec, "workers_compensation")
    wc_statutory: bool | str = "NEEDS_HUMAN_CHECK"
    el_acc: int | str = "NEEDS_HUMAN_CHECK"
    el_emp: int | str = "NEEDS_HUMAN_CHECK"
    el_pol: int | str = "NEEDS_HUMAN_CHECK"
    for pg, ln, txt in wc_sec:
        up = txt.upper()
        if "STATUTORY" in up or re.search(r"(?:\[X\]|\(X\)|X\s+)PER\s+STATUTE", up):
            wc_statutory = True
            cite("workers_compensation.statutory_limits", pg, ln, txt)
        if "E.L. EACH ACCIDENT" in up or "EL EACH ACCIDENT" in up:
            val = parse_money(txt)
            if val is not None:
                el_acc = val
                cite("workers_compensation.el_each_accident", pg, ln, txt)
        if "EA EMPLOYEE" in up:
            val = parse_money(txt)
            if val is not None:
                el_emp = val
                cite("workers_compensation.el_disease_ea_employee", pg, ln, txt)
        if "POLICY LIMIT" in up:
            val = parse_money(txt)
            if val is not None:
                el_pol = val
                cite("workers_compensation.el_disease_policy_limit", pg, ln, txt)

    # Description of Operations
    desc_sec = section_slice(desc_idx)
    desc_lines: list[str] = []
    if desc_sec:
        cite("description_of_operations", desc_sec[0][0], desc_sec[0][1], desc_sec[0][2])
        for _, _, txt in desc_sec:
            if txt.upper().startswith("DESCRIPTION OF OPERATIONS"):
                after = txt.split(":", 1)[1].strip() if ":" in txt else ""
                if after:
                    desc_lines.append(after)
            else:
                desc_lines.append(txt)
    desc_text = " ".join(desc_lines).strip()
    desc_upper = desc_text.upper()
    desc_signals = {
        "mentions_additional_insured": "ADDITIONAL INSURED" in desc_upper,
        "mentions_primary_non_contributory": bool(
            re.search(r"PRIMARY\s+(?:AND|&)\s+NON[-\s]?CONTRIBUTORY", desc_upper)
        ),
        "mentions_waiver_of_subrogation": "WAIVER OF SUBROGATION" in desc_upper,
    }

    # Certificate Holder
    holder_sec = section_slice(holder_idx)
    holder_lines: list[str] = []
    if holder_sec:
        for i, (pg, ln, txt) in enumerate(holder_sec):
            if i == 0 and txt.upper().startswith("CERTIFICATE HOLDER"):
                after = txt.split(":", 1)[1].strip() if ":" in txt else ""
                if after:
                    holder_lines.append(after)
                    cite("certificate_holder", pg, ln, txt)
            else:
                if txt.upper().startswith("CANCELLATION") or txt.upper().startswith("AUTHORIZED REPRESENTATIVE"):
                    break
                if not holder_lines:
                    cite("certificate_holder", pg, ln, txt)
                holder_lines.append(txt)
    cert_holder_name = holder_lines[0] if holder_lines else "NEEDS_HUMAN_CHECK"
    cert_holder_block = "\n".join(holder_lines)

    # Endorsements detected inside the same PDF
    endorsements_found: list[dict[str, Any]] = []
    endorsement_codes: set[str] = set()
    for pg, _, forms in endorsement_pages:
        for code in forms:
            endorsement_codes.add(code)
        endorsements_found.append({"source_file": file_path.name, "page": pg, "forms": forms})

    return {
        "source_file": file_path.name,
        "source_path": file_path.as_posix(),
        "is_scanned": False,
        "status_hint": None,
        "flags": [],
        "acord25_pages": [pg for pg, _ in acord_pages],
        "skipped_pages": skipped_pages,
        "vendor_name": vendor_name,
        "producer_name": producer_name,
        "broker_email": broker_email,
        "producer_phone": producer_phone,
        "coverages": {
            "general_liability": {
                "policy_number": gl_pol,
                "eff_date": gl_eff or "NEEDS_HUMAN_CHECK",
                "exp_date": gl_exp or "NEEDS_HUMAN_CHECK",
                "each_occurrence": gl_occ,
                "general_aggregate": gl_agg,
                "products_comp_op_agg": gl_prod,
                "aggregate_basis": gl_basis,
                "addl_insd": gl_addl,
                "subr_wvd": gl_wos,
            },
            "automobile_liability": {
                "policy_number": auto_pol,
                "eff_date": auto_eff,
                "exp_date": auto_exp,
                "combined_single_limit": auto_csl,
                "addl_insd": auto_addl,
                "subr_wvd": auto_wos,
            },
            "umbrella_liability": {
                "policy_number": umb_pol,
                "eff_date": umb_eff,
                "exp_date": umb_exp,
                "each_occurrence": umb_occ,
                "aggregate": umb_agg,
                "addl_insd": umb_addl,
                "subr_wvd": umb_wos,
            },
            "workers_compensation": {
                "policy_number": wc_pol,
                "eff_date": wc_eff,
                "exp_date": wc_exp or "NEEDS_HUMAN_CHECK",
                "statutory_limits": wc_statutory,
                "el_each_accident": el_acc,
                "el_disease_ea_employee": el_emp,
                "el_disease_policy_limit": el_pol,
                "subr_wvd": wc_wos,
            },
        },
        "description_of_operations": desc_text,
        "description_signals": desc_signals,
        "certificate_holder": {
            "name": cert_holder_name,
            "block": cert_holder_block,
        },
        "endorsement_pages_detected": endorsements_found,
        "endorsement_forms_detected": sorted(endorsement_codes),
        "citations": citations,
    }


def process_pdf(pdf_path: Path, min_text_chars: int) -> tuple[str, dict[str, Any]]:
    acord_pages: list[tuple[int, str]] = []
    endorsement_pages: list[tuple[int, str, list[str]]] = []
    skipped_pages: list[dict[str, Any]] = []
    scanned_pages: list[int] = []

    try:
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
            for idx, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                kind = classify_page(text, min_text_chars)
                if kind == "ACORD_25":
                    acord_pages.append((idx, text))
                elif kind == "ENDORSEMENT":
                    endorsement_pages.append((idx, text, detect_endorsement_forms(text)))
                elif kind == "SCANNED":
                    scanned_pages.append(idx)
                else:
                    reason = "W9_PAGE" if ("W-9" in text.upper() or "TAXPAYER" in text.upper()) else "UNRELATED_PAGE"
                    skipped_pages.append({"page": idx, "reason": reason})
    except Exception as exc:
        return "record", {
            "source_file": pdf_path.name,
            "source_path": pdf_path.as_posix(),
            "is_scanned": False,
            "status_hint": "MANUAL_REVIEW_REQUIRED",
            "flags": ["UNREADABLE_PDF"],
            "error": str(exc),
            "vendor_name": pdf_path.stem,
            "broker_email": "NEEDS_HUMAN_CHECK",
            "coverages": {},
            "description_of_operations": "",
            "description_signals": {},
            "certificate_holder": {"name": "NEEDS_HUMAN_CHECK", "block": ""},
            "endorsement_pages_detected": [],
            "endorsement_forms_detected": [],
            "skipped_pages": [],
            "citations": {},
        }

    if acord_pages:
        record = extract_acord25_fields(pdf_path, acord_pages, endorsement_pages, skipped_pages)
        if scanned_pages:
            record["flags"].append("PARTIAL_SCANNED_PAGES")
            record["scanned_pages"] = scanned_pages
        return "record", record

    if scanned_pages and len(scanned_pages) == total_pages:
        return "record", {
            "source_file": pdf_path.name,
            "source_path": pdf_path.as_posix(),
            "is_scanned": True,
            "status_hint": "MANUAL_REVIEW_REQUIRED",
            "flags": ["SCANNED_PDF"],
            "acord25_pages": [],
            "scanned_pages": scanned_pages,
            "skipped_pages": skipped_pages,
            "vendor_name": pdf_path.stem,
            "producer_name": None,
            "broker_email": None,
            "producer_phone": None,
            "coverages": {},
            "description_of_operations": "",
            "description_signals": {},
            "certificate_holder": {"name": None, "block": ""},
            "endorsement_pages_detected": [],
            "endorsement_forms_detected": [],
            "citations": {},
        }

    if endorsement_pages and not acord_pages:
        forms = sorted({f for _, _, fl in endorsement_pages for f in fl})
        combined_text = "\n".join(t for _, t, _ in endorsement_pages)
        return "endorsement", {
            "source_file": pdf_path.name,
            "source_path": pdf_path.as_posix(),
            "pages": [pg for pg, _, _ in endorsement_pages],
            "forms": forms,
            "text_upper": combined_text.upper(),
        }

    return "skipped", {
        "source_file": pdf_path.name,
        "source_path": pdf_path.as_posix(),
        "skipped_pages": skipped_pages,
        "reason": skipped_pages[0]["reason"] if skipped_pages else "NON_COI_PDF",
    }


def link_standalone_endorsements(
    records: list[dict[str, Any]],
    standalone_endorsements: list[dict[str, Any]],
) -> None:
    for end in standalone_endorsements:
        text_upper = end.get("text_upper", "")
        fname_upper = end["source_file"].upper()
        for rec in records:
            if rec.get("is_scanned"):
                continue
            vname = str(rec.get("vendor_name") or "").upper()
            gl_pol = str(rec.get("coverages", {}).get("general_liability", {}).get("policy_number") or "").upper()
            vtokens = [t for t in re.split(r"[^A-Z0-9]+", vname) if len(t) >= 4]
            matched = False
            if gl_pol and gl_pol in text_upper:
                matched = True
            elif vname and vname != "NEEDS_HUMAN_CHECK" and vname in text_upper:
                matched = True
            elif vtokens and any(tok in fname_upper for tok in vtokens[:2]):
                matched = True
            if matched:
                rec["endorsement_pages_detected"].append(
                    {"source_file": end["source_file"], "pages": end["pages"], "forms": end["forms"]}
                )
                merged = set(rec.get("endorsement_forms_detected", [])) | set(end["forms"])
                rec["endorsement_forms_detected"] = sorted(merged)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="extract_acord25.py",
        description="Extract structured ACORD 25 Certificate of Insurance fields and citations from PDFs.",
        epilog="Exit codes: 0 = success, 2 = invalid arguments or missing pdfplumber.",
    )
    parser.add_argument("--input", required=True, help="Path to a single PDF file or a directory containing PDFs.")
    parser.add_argument("--output", help="Optional JSON output file path (prints JSON to stdout if omitted).")
    parser.add_argument(
        "--min-text-chars",
        type=int,
        default=40,
        help="Minimum extracted characters per page before flagging as SCANNED_PDF (default: 40).",
    )
    args = parser.parse_args()

    if pdfplumber is None:
        print("Error: pdfplumber is required. Install with: pip install pdfplumber", file=sys.stderr)
        return 2

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        print(f"Error: Input path not found: {input_path}", file=sys.stderr)
        return 2

    pdf_files = [input_path] if input_path.is_file() else sorted(input_path.glob("*.pdf"))
    if not pdf_files:
        print(f"Error: No PDF files found in: {input_path}", file=sys.stderr)
        return 2

    records: list[dict[str, Any]] = []
    standalone_endorsements: list[dict[str, Any]] = []
    skipped_files: list[dict[str, Any]] = []

    for pdf_file in pdf_files:
        kind, payload = process_pdf(pdf_file, args.min_text_chars)
        if kind == "record":
            records.append(payload)
        elif kind == "endorsement":
            standalone_endorsements.append(payload)
        else:
            skipped_files.append(payload)

    link_standalone_endorsements(records, standalone_endorsements)
    for end in standalone_endorsements:
        end.pop("text_upper", None)

    result = {
        "input_path": input_path.as_posix(),
        "total_pdfs_scanned": len(pdf_files),
        "coi_records_count": len(records),
        "scanned_files_count": sum(1 for r in records if r.get("is_scanned")),
        "standalone_endorsements_count": len(standalone_endorsements),
        "skipped_files_count": len(skipped_files),
        "records": records,
        "standalone_endorsements": standalone_endorsements,
        "skipped_files": skipped_files,
    }

    serialized = json.dumps(result, indent=2) + "\n"
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(serialized, encoding="utf-8")

    summary = {
        "input_path": result["input_path"],
        "output_path": Path(args.output).resolve().as_posix() if args.output else None,
        "total_pdfs_scanned": result["total_pdfs_scanned"],
        "coi_records_count": result["coi_records_count"],
        "scanned_files_count": result["scanned_files_count"],
        "standalone_endorsements_count": result["standalone_endorsements_count"],
        "skipped_files_count": result["skipped_files_count"],
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
