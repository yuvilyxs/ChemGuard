"""Generate downloadable safety reports from sourced PubChem records."""

from __future__ import annotations

import csv
import io
import json
import os
import textwrap
from datetime import datetime, timezone
from html import escape
from typing import Any, Dict, List, Optional

import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    LongTable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    TableStyle,
)


def _value_text(value: Any) -> str:
    if isinstance(value, dict):
        fragments = value.get("StringWithMarkup")
        if isinstance(fragments, list):
            texts = [
                fragment.get("String", "").strip()
                for fragment in fragments
                if isinstance(fragment, dict)
            ]
            rendered = " ".join(text for text in texts if text)
            unit = value.get("Unit")
            return f"{rendered} {unit}" if unit and rendered else rendered
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, list):
        return "; ".join(_value_text(item) for item in value)
    return str(value) if value is not None else ""


def _report_rows(
    compound: Dict[str, Any], safety_profile: Dict[str, Any]
) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []

    def add(
        section: str, field: str, value: Any, source: Optional[str] = None
    ) -> None:
        rendered = _value_text(value)
        if rendered:
            value_chunks = textwrap.wrap(
                rendered,
                width=800,
                break_long_words=True,
                break_on_hyphens=False,
            )
            source_chunks = textwrap.wrap(
                source or "",
                width=500,
                break_long_words=True,
                break_on_hyphens=False,
            )
            for index in range(max(len(value_chunks), len(source_chunks))):
                rows.append(
                    {
                        "Section": section,
                        "Field": (
                            field
                            if index == 0
                            else f"{field} (continued {index + 1})"
                        ),
                        "Value": (
                            value_chunks[index]
                            if index < len(value_chunks)
                            else ""
                        ),
                        "Source": (
                            source_chunks[index]
                            if index < len(source_chunks)
                            else ""
                        ),
                    }
                )

    add("Identity", "PubChem CID", compound.get("cid"))
    add("Identity", "IUPAC name", compound.get("iupac_name"))
    add("Identity", "Molecular formula", compound.get("molecular_formula"))
    add("Identity", "Molecular weight (g/mol)", compound.get("molecular_weight"))
    for name, value in compound.get("properties", {}).items():
        if name not in {"MolecularFormula", "MolecularWeight", "IUPACName"}:
            add("Molecular properties", name, value)

    for heading, records in compound.get(
        "physical_chemical_properties", {}
    ).items():
        for record in records:
            value = record.get("value")
            citation = record.get("reference", [])
            source = "; ".join(citation) if isinstance(citation, list) else str(citation)
            add("Physical and chemical properties", heading, value, source)

    if safety_profile.get("available"):
        for classification in safety_profile.get("ghs_classifications", []):
            source = classification.get("source_name") or "Source not stated in PubChem annotation"
            for signal in classification.get("signal_words", []):
                add("GHS classification", "Signal word", signal, source)
            for pictogram in classification.get("pictograms", []):
                add(
                    "GHS classification",
                    f"Pictogram {pictogram.get('code', '')}".strip(),
                    pictogram.get("name"),
                    pictogram.get("url") or source,
                )
            for statement in classification.get("hazard_statements", []):
                field = statement.get("code") or "Hazard statement"
                classification_details = statement.get("classifications", [])
                if classification_details:
                    for detail in classification_details:
                        category = " — ".join(
                            part
                            for part in (
                                detail.get("hazard_class"),
                                detail.get("category"),
                            )
                            if part
                        )
                        add(
                            "GHS hazard classification",
                            field,
                            category,
                            "PubChem GHS Rev. 11 hazard reference",
                        )
                add(
                    f"GHS {statement.get('area', 'Uncategorized').lower()} hazard",
                    field,
                    statement.get("statement"),
                    source,
                )
            for note in classification.get("notes", []):
                add("GHS classification notes", "PubChem source note", note, source)
            for entry in classification.get("other_classification_data", []):
                add(
                    "Additional GHS source data",
                    entry.get("name") or "Classification data",
                    entry.get("values", []),
                    source,
                )
        for precautionary in safety_profile.get("precautionary_statements", []):
            add(
                "GHS precautionary statements",
                precautionary.get("code"),
                precautionary.get("statement")
                or "Statement text unavailable; open the PubChem GHS reference.",
                precautionary.get("url"),
            )
    else:
        add(
            "Verification",
            "GHS annotations",
            "Unavailable in PubChem; supplier SDS review required.",
        )

    for entry in safety_profile.get("storage_information", []):
        source = entry.get("source_name") or ""
        if entry.get("source_url"):
            source = f"{source} {entry['source_url']}".strip()
        citations = entry.get("reference_citations", [])
        if citations:
            source = f"{source}; {'; '.join(citations)}".strip("; ")
        add(
            "Storage information",
            entry.get("heading") or "Safety and storage",
            entry.get("value_text"),
            source,
        )

    for reference in safety_profile.get("supplier_sds_references", []):
        source = reference.get("url") or reference.get("source_url") or ""
        add(
            "Supplier SDS references",
            reference.get("supplier") or "Supplier",
            reference.get("citation"),
            source,
        )
    if not safety_profile.get("supplier_sds_references"):
        add(
            "Verification",
            "Supplier SDS",
            "No supplier SDS citation found in the retrieved PubChem annotations.",
        )

    for evidence in safety_profile.get("pairwise_evidence", []):
        claims = evidence.get("claims", {})
        claim_text = "; ".join(
            f"{name}: {', '.join(values)}" for name, values in claims.items()
        )
        source = evidence.get("source_name") or ""
        if evidence.get("source_url"):
            source = f"{source} {evidence['source_url']}".strip()
        add(
            "Other chemical reaction annotations",
            f"Related CID {evidence.get('related_cid')} ({evidence.get('heading')})",
            claim_text,
            source,
        )

    add(
        "Verification",
        "GHS data status",
        "PubChem source annotations available; source reports may differ by product, concentration, or jurisdiction."
        if safety_profile.get("available")
        else "PubChem safety annotations unavailable; unverified.",
    )
    add(
        "Verification",
        "Chemical compatibility",
        "Not assessed in this single-compound report; consult the current SDSs for the exact products and conditions.",
    )

    for label, url in compound.get("source_urls", {}).items():
        add("Sources", label.replace("_", " ").title(), url, url)
    for label, url in safety_profile.get("source_urls", {}).items():
        add("Sources", label.replace("_", " ").title(), url, url)
    for reference in safety_profile.get("source_references", []):
        add(
            "Sources",
            reference.get("SourceName") or "PubChem source reference",
            reference.get("URL"),
            reference.get("URL"),
        )
    return rows


def _markdown_cell(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", "<br>")
    )


def _markdown_report(
    title: str, rows: List[Dict[str, str]], generated_at: str
) -> str:
    output = [
        f"# Chemical Safety Report: {_markdown_cell(title)}",
        "",
        f"Generated: {generated_at} (UTC)",
        "",
        "> PubChem annotations are source-attributed reference data, not a substitute "
        "for the current supplier SDS for the exact product and concentration.",
        "",
    ]
    current_section = None
    for row in rows:
        if row["Section"] != current_section:
            current_section = row["Section"]
            output.extend(
                [
                    f"## {_markdown_cell(current_section)}",
                    "",
                    "| Field | Value | Source |",
                    "| --- | --- | --- |",
                ]
            )
        source = _markdown_cell(row["Source"])
        value = _markdown_cell(row["Value"])
        output.append(
            f"| {_markdown_cell(row['Field'])} | {value} | {source} |"
        )
    return "\n".join(output) + "\n"


def _csv_report(rows: List[Dict[str, str]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output, fieldnames=["Section", "Field", "Value", "Source"]
    )
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _register_pdf_fonts() -> str:
    font_directory = os.path.join(os.path.dirname(reportlab.__file__), "fonts")
    regular_font = os.path.join(font_directory, "Vera.ttf")
    bold_font = os.path.join(font_directory, "VeraBd.ttf")
    if os.path.isfile(regular_font) and os.path.isfile(bold_font):
        if "ChemGuardVera" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("ChemGuardVera", regular_font))
            pdfmetrics.registerFont(TTFont("ChemGuardVera-Bold", bold_font))
        return "ChemGuardVera"
    return "Helvetica"


def _pdf_report(title: str, rows: List[Dict[str, str]], generated_at: str) -> bytes:
    font_name = _register_pdf_fonts()
    bold_font = (
        "ChemGuardVera-Bold" if font_name == "ChemGuardVera" else "Helvetica-Bold"
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ChemGuardTitle",
        parent=styles["Title"],
        fontName=bold_font,
        fontSize=17,
        leading=21,
        textColor=colors.HexColor("#10253f"),
        alignment=TA_CENTER,
        spaceAfter=8,
    )
    section_style = ParagraphStyle(
        "ChemGuardSection",
        parent=styles["Heading2"],
        fontName=bold_font,
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#11877e"),
        spaceBefore=10,
        spaceAfter=5,
    )
    cell_style = ParagraphStyle(
        "ChemGuardCell",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=7.5,
        leading=9.5,
        wordWrap="CJK",
    )

    def paragraph(value: str) -> Paragraph:
        safe_value = escape(value).replace("\r", "").replace("\n", "<br/>")
        return Paragraph(safe_value, cell_style)

    story = [
        Paragraph(f"Chemical Safety Report: {escape(title)}", title_style),
        Paragraph(f"Generated {escape(generated_at)} UTC", cell_style),
        Spacer(1, 0.12 * inch),
        paragraph(
            "PubChem annotations are reference data, not a substitute for the current "
            "supplier SDS for the exact product and concentration."
        ),
        Spacer(1, 0.14 * inch),
    ]

    current_section = None
    section_rows: List[Dict[str, str]] = []

    def append_section() -> None:
        if current_section is None or not section_rows:
            return
        story.append(Paragraph(escape(current_section), section_style))
        data = [[paragraph("Field"), paragraph("Value"), paragraph("Source")]]
        data.extend(
            [
                paragraph(row["Field"]),
                paragraph(row["Value"]),
                paragraph(row["Source"]),
            ]
            for row in section_rows
        )
        table = LongTable(
            data,
            colWidths=[1.25 * inch, 3.1 * inch, 2.45 * inch],
            repeatRows=1,
            hAlign="LEFT",
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#10253f")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), bold_font),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#dce6eb")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(table)
        story.append(Spacer(1, 0.08 * inch))

    for row in rows:
        if current_section != row["Section"]:
            append_section()
            section_rows = []
            current_section = row["Section"]
        section_rows.append(row)
    append_section()

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.45 * inch,
        leftMargin=0.45 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
        title=f"Chemical Safety Report: {title}",
        author="ChemGuard",
    )
    document.build(story)
    return buffer.getvalue()


def create_safety_report(
    compound: Dict[str, Any], safety_profile: Dict[str, Any]
) -> Dict[str, Any]:
    """Build Markdown, CSV, and PDF downloads from one selected compound."""
    if not isinstance(compound.get("cid"), int):
        raise ValueError("A valid selected PubChem compound is required.")
    title = compound.get("iupac_name") or f"PubChem CID {compound['cid']}"
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    rows = _report_rows(compound, safety_profile)
    return {
        "title": title,
        "generated_at": generated_at,
        "rows": rows,
        "markdown": _markdown_report(title, rows, generated_at),
        "csv": _csv_report(rows),
        "pdf": _pdf_report(title, rows, generated_at),
    }
