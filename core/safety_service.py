"""Source-attributed PubChem GHS and storage-safety annotations."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

from core.pubchem_service import (
    PUBCHEM_GHS_URL,
    PUBCHEM_VIEW_URL,
    PubChemError,
    PubChemNotFoundError,
    get_compound_safety_record,
    get_ghs_hazard_classifications,
    get_precautionary_statement_descriptions,
    PUBCHEM_GHS_TSV_URL,
)

_P_CODE_PATTERN = re.compile(r"P\d{3}(?:\+P\d{3})*(?:\[\+P\d{3}\])?")
_H_CODE_PATTERN = re.compile(r"\bH\d{3}(?:\+H\d{3})*\b")
_CATEGORY_PATTERN = re.compile(r"\[([^\]]+)\]")
_CID_PATTERN = re.compile(r"\bCID-(\d+)\b")
_URL_PATTERN = re.compile(r"https?://[^\s\])>,;]+")
_SDS_PATTERN = re.compile(r"\b(?:material\s+)?safety data sheet\b", re.IGNORECASE)
_NO_CONFLICT_PATTERN = re.compile(
    r"\b(?:no|none|not known)\b.{0,60}\b"
    r"(?:incompatib|dangerous reactions?|hazardous reactions?|materials to avoid)\b",
    re.IGNORECASE,
)
_PAIRWISE_HEADINGS = {
    "Chemical Dangers",
    "CSL Reaction Information",
    "Conditions to Avoid",
    "Incompatible Materials",
    "Incompatibilities",
    "Storage Conditions",
}
_TRUSTED_WARNING_SOURCES = (
    "international chemical safety cards",
    "hazardous substances data bank",
    "cameo chemicals",
    "safety data sheet",
)


def _value_fragments(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, dict):
        return [{"text": str(value), "markup": []}] if value is not None else []

    fragments = value.get("StringWithMarkup")
    if isinstance(fragments, list):
        return [
            {
                "text": fragment.get("String", ""),
                "markup": fragment.get("Markup", []),
            }
            for fragment in fragments
            if isinstance(fragment, dict)
        ]

    for key in ("Number", "Integer", "String", "Boolean"):
        entries = value.get(key)
        if isinstance(entries, list):
            return [{"text": str(entry), "markup": []} for entry in entries]
    return []


def _text_for_value(value: Any) -> str:
    return " ".join(
        fragment["text"].strip()
        for fragment in _value_fragments(value)
        if fragment["text"].strip()
    )


def _walk_sections(
    sections: Any, parents: Tuple[str, ...] = ()
) -> Iterable[Tuple[Dict[str, Any], Tuple[str, ...]]]:
    if not isinstance(sections, list):
        return
    for section in sections:
        if not isinstance(section, dict):
            continue
        heading = section.get("TOCHeading")
        path = parents + ((heading,) if isinstance(heading, str) else ())
        yield section, path
        yield from _walk_sections(section.get("Section", []), path)


def _reference_map(record: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    references = record.get("Reference", [])
    if not isinstance(references, list):
        return {}
    return {
        reference["ReferenceNumber"]: reference
        for reference in references
        if isinstance(reference, dict)
        and isinstance(reference.get("ReferenceNumber"), int)
    }


def _reference_for(
    info: Dict[str, Any], references: Dict[int, Dict[str, Any]]
) -> Dict[str, Any]:
    reference_number = info.get("ReferenceNumber")
    source = references.get(reference_number, {})
    return {
        "reference_number": reference_number,
        "source_name": source.get("SourceName"),
        "source_id": source.get("SourceID"),
        "source_url": source.get("URL"),
        "description": source.get("Description"),
        "citations": info.get("Reference", [])
        if isinstance(info.get("Reference", []), list)
        else [],
    }


def _information_rows(
    section: Dict[str, Any],
    path: Tuple[str, ...],
    references: Dict[int, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    information = section.get("Information", [])
    if not isinstance(information, list):
        return []

    rows = []
    for item in information:
        if not isinstance(item, dict):
            continue
        reference = _reference_for(item, references)
        rows.append(
            {
                "heading": path[-1] if path else "",
                "section_path": " > ".join(path),
                "description": section.get("Description"),
                "name": item.get("Name") or "",
                "value": item.get("Value"),
                "value_text": _text_for_value(item.get("Value")),
                "fragments": _value_fragments(item.get("Value")),
                "reference_number": reference["reference_number"],
                "source_name": reference["source_name"],
                "source_url": item.get("URL")
                or section.get("URL")
                or reference["source_url"],
                "reference_citations": reference["citations"],
                "source_description": reference["description"],
            }
        )
    return rows


def _all_information(
    sections: Any, references: Dict[int, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    rows = []
    for section, path in _walk_sections(sections):
        rows.extend(_information_rows(section, path, references))
    return rows


def _classification_category(statement: str) -> Optional[str]:
    match = _CATEGORY_PATTERN.search(statement)
    if not match:
        return None
    category = match.group(1).strip()
    return re.sub(r"^(?:Danger|Warning)\s+", "", category, flags=re.IGNORECASE)


def _hazard_area(statement: str) -> str:
    code_match = _H_CODE_PATTERN.search(statement)
    if not code_match:
        return "Uncategorized"
    first_code = code_match.group(0).split("+", 1)[0]
    if first_code.startswith("H2"):
        return "Physical"
    if first_code.startswith("H3"):
        return "Health"
    if first_code.startswith("H4"):
        return "Environmental"
    return "Uncategorized"


def _pictograms_from_info(info: Dict[str, Any]) -> List[Dict[str, str]]:
    pictograms = []
    for fragment in _value_fragments(info.get("Value")):
        for markup in fragment.get("markup", []):
            if (
                isinstance(markup, dict)
                and markup.get("Type") == "Icon"
                and isinstance(markup.get("URL"), str)
            ):
                url = markup["URL"]
                pictograms.append(
                    {
                        "code": url.rsplit("/", 1)[-1].split(".", 1)[0],
                        "name": markup.get("Extra", ""),
                        "url": url,
                    }
                )
    return pictograms


def _ghs_classifications(
    sections: Any, references: Dict[int, Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[str]]:
    groups: Dict[Any, Dict[str, Any]] = {}
    for section, path in _walk_sections(sections):
        if not path or path[-1] != "GHS Classification":
            continue
        information = section.get("Information", [])
        if not isinstance(information, list):
            continue
        for index, info in enumerate(information):
            if not isinstance(info, dict):
                continue
            reference_number = info.get("ReferenceNumber")
            group_key = (
                reference_number
                if isinstance(reference_number, int)
                else f"unattributed-{index}"
            )
            group = groups.setdefault(
                group_key,
                {
                    "reference_number": reference_number,
                    "source_name": references.get(reference_number, {}).get(
                        "SourceName"
                    ),
                    "source_url": references.get(reference_number, {}).get("URL"),
                    "signal_words": [],
                    "pictograms": [],
                    "hazard_statements": [],
                    "precautionary_codes": [],
                    "notes": [],
                    "other_classification_data": [],
                },
            )
            name = str(info.get("Name") or "")
            fragments = _value_fragments(info.get("Value"))
            texts = [
                fragment["text"].strip()
                for fragment in fragments
                if fragment["text"].strip()
            ]
            if name.casefold().startswith("pictogram"):
                group["pictograms"].extend(_pictograms_from_info(info))
            elif name.casefold() == "signal":
                group["signal_words"].extend(texts)
            elif "hazard statement" in name.casefold():
                for text in texts:
                    codes = _H_CODE_PATTERN.findall(text)
                    category = _classification_category(text)
                    if codes or category:
                        group["hazard_statements"].append(
                            {
                                "code": " + ".join(codes) if codes else None,
                                "statement": text,
                                "category": category,
                                "area": _hazard_area(text),
                                "source_name": group["source_name"],
                                "reference_number": reference_number,
                            }
                        )
                    else:
                        group["notes"].append(text)
            elif "precautionary" in name.casefold():
                for text in texts:
                    group["precautionary_codes"].extend(_P_CODE_PATTERN.findall(text))
            elif name.casefold() == "note":
                group["notes"].extend(texts)
            else:
                group["other_classification_data"].append(
                    {"name": name, "values": texts}
                )

    p_codes = []
    for group in groups.values():
        group["signal_words"] = list(dict.fromkeys(group["signal_words"]))
        group["hazard_statements"] = _deduplicate_dicts(
            group["hazard_statements"], ("code", "statement")
        )
        group["precautionary_codes"] = list(
            dict.fromkeys(group["precautionary_codes"])
        )
        group["pictograms"] = _deduplicate_dicts(
            group["pictograms"], ("code", "url")
        )
        group["notes"] = list(dict.fromkeys(group["notes"]))
        p_codes.extend(group["precautionary_codes"])
    return list(groups.values()), list(dict.fromkeys(p_codes))


def _deduplicate_dicts(
    values: List[Dict[str, Any]], keys: Tuple[str, ...]
) -> List[Dict[str, Any]]:
    result = []
    seen = set()
    for value in values:
        key = tuple(value.get(name) for name in keys)
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _supplier_sds_references(
    information: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    references = []
    seen = set()
    for row in information:
        for citation in row["reference_citations"]:
            if not isinstance(citation, str) or not _SDS_PATTERN.search(citation):
                continue
            if citation in seen:
                continue
            seen.add(citation)
            urls = _URL_PATTERN.findall(citation)
            references.append(
                {
                    "supplier": citation.split(";", 1)[0].strip(),
                    "citation": citation,
                    "url": urls[-1].rstrip(".,:") if urls else None,
                    "source_name": row["source_name"],
                    "source_url": row["source_url"],
                    "section": row["section_path"],
                    "reference_number": row["reference_number"],
                }
            )
    return references


def _related_compounds(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    related = []
    seen = set()
    for fragment in row["fragments"]:
        for markup in fragment.get("markup", []):
            if not isinstance(markup, dict):
                continue
            extra = markup.get("Extra", "")
            match = _CID_PATTERN.search(extra) if isinstance(extra, str) else None
            if not match:
                continue
            cid = int(match.group(1))
            url = markup.get("URL")
            if cid not in seen and isinstance(url, str):
                seen.add(cid)
                related.append({"cid": cid, "url": url})
    return related


def _pairwise_evidence(
    sections: Any, references: Dict[int, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    evidence = []
    for section, path in _walk_sections(sections):
        heading = path[-1] if path else ""
        if heading not in _PAIRWISE_HEADINGS:
            continue
        information = section.get("Information", [])
        if not isinstance(information, list):
            continue

        grouped: Dict[Any, List[Dict[str, Any]]] = {}
        for index, item in enumerate(information):
            if not isinstance(item, dict):
                continue
            reference_number = item.get("ReferenceNumber")
            group_key = (
                reference_number
                if isinstance(reference_number, int)
                else f"unattributed-{index}"
            )
            grouped.setdefault(group_key, []).append(item)

        for group_key, items in grouped.items():
            claims: Dict[str, List[str]] = {}
            related = []
            user_reported = False
            direct_urls = []
            for item in items:
                name = str(item.get("Name") or "Source text")
                text = _text_for_value(item.get("Value"))
                if text:
                    claims.setdefault(name, []).append(text)
                if name.casefold() == "reference source" and "user-reported" in text.casefold():
                    user_reported = True
                direct_url = item.get("URL")
                if isinstance(direct_url, str):
                    direct_urls.append(direct_url)
                related.extend(
                    _related_compounds(
                        {"fragments": _value_fragments(item.get("Value"))}
                    )
                )

            reference_number = (
                group_key if isinstance(group_key, int) else None
            )
            source = references.get(reference_number, {})
            source_name = source.get("SourceName")
            source_description = source.get("Description")
            source_text = " ".join(
                text
                for values in claims.values()
                for text in values
            )
            has_explicit_conflict = bool(
                re.search(
                    r"\b(?:react(?:s|ed|ive|ion)|incompatib|not stored with|"
                    r"keep away from|separate from|avoid contact)\b",
                    source_text,
                    flags=re.IGNORECASE,
                )
            )
            if heading == "CSL Reaction Information":
                has_explicit_conflict = bool(
                    claims.get("Reactants/Reagents")
                    and (
                        claims.get("Warning Message")
                        or claims.get("GHS Category")
                    )
                )
            elif heading not in {"Chemical Dangers", "CSL Reaction Information"}:
                has_explicit_conflict = has_explicit_conflict and bool(
                    re.search(
                        r"\b(?:incompatib|not stored with|keep away from|"
                        r"separate from|avoid contact)\b",
                        source_text,
                        flags=re.IGNORECASE,
                    )
                )
            if not has_explicit_conflict:
                continue

            for related_compound in _deduplicate_dicts(related, ("cid", "url")):
                evidence.append(
                    {
                        "related_cid": related_compound["cid"],
                        "related_url": related_compound["url"],
                        "heading": heading,
                        "section_path": " > ".join(path),
                        "claims": claims,
                        "reference_number": reference_number,
                        "source_name": source_name,
                        "source_url": source.get("URL") or (direct_urls[0] if direct_urls else section.get("URL")),
                        "source_description": source_description,
                        "user_reported": user_reported,
                        "source_verified": (
                            not user_reported
                            and isinstance(source_name, str)
                            and any(
                                source_name.lower().find(trusted) >= 0
                                for trusted in _TRUSTED_WARNING_SOURCES
                            )
                        ),
                        "direct_urls": list(dict.fromkeys(direct_urls)),
                    }
                )
    return _deduplicate_dicts(
        evidence,
        ("related_cid", "heading", "reference_number", "section_path"),
    )


def _storage_information(
    sections: Any, references: Dict[int, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    rows = []
    for section, path in _walk_sections(sections):
        if "Handling and Storage" not in path:
            continue
        rows.extend(_information_rows(section, path, references))
    return rows


def _used_references(
    information: List[Dict[str, Any]], references: Dict[int, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    used = {
        row["reference_number"]
        for row in information
        if isinstance(row["reference_number"], int)
        and row["reference_number"] in references
    }
    return [
        references[number]
        for number in sorted(used)
    ]


def get_compound_safety_profile(cid: int) -> Dict[str, Any]:
    """Return source-attributed GHS, storage, and reaction evidence for a PubChem CID."""
    if not isinstance(cid, int) or isinstance(cid, bool) or cid < 1:
        raise ValueError("cid must be a positive integer.")

    source_url = (
        f"{PUBCHEM_VIEW_URL}/data/compound/{cid}/JSON"
        "?heading=Safety%20and%20Hazards"
    )
    try:
        payload = get_compound_safety_record(cid)
    except PubChemNotFoundError:
        return {
            "cid": cid,
            "available": False,
            "message": "PubChem has no Safety and Hazards annotation for this CID.",
            "ghs_classifications": [],
            "hazard_groups": {
                "Physical": [],
                "Health": [],
                "Environmental": [],
                "Uncategorized": [],
            },
            "precautionary_statements": [],
            "storage_information": [],
            "supplier_sds_references": [],
            "pairwise_evidence": [],
            "source_references": [],
            "source_urls": {"pubchem_safety_annotations": source_url},
        }

    record = payload.get("Record")
    if not isinstance(record, dict):
        raise PubChemError("PubChem returned an invalid Safety and Hazards record.")
    references = _reference_map(record)
    sections = record.get("Section", [])
    if not isinstance(sections, list):
        raise PubChemError("PubChem returned invalid Safety and Hazards sections.")

    all_information = _all_information(sections, references)
    classifications, p_codes = _ghs_classifications(sections, references)
    p_statement_texts: Dict[str, Optional[str]] = {}
    p_statement_error = None
    if p_codes:
        try:
            p_statement_texts = get_precautionary_statement_descriptions(p_codes)
        except PubChemError as exc:
            p_statement_error = str(exc)
    hazard_reference_error = None
    hazard_codes = list(
        dict.fromkeys(
            code
            for classification in classifications
            for statement in classification["hazard_statements"]
            for code in (
                [(statement.get("code") or "").replace(" ", "")]
                + (statement.get("code") or "").replace(" ", "").split("+")
            )
            if code
        )
    )
    hazard_reference: Dict[str, List[Dict[str, str]]] = {}
    if hazard_codes:
        try:
            hazard_reference = get_ghs_hazard_classifications(hazard_codes)
        except PubChemError as exc:
            hazard_reference_error = str(exc)
    for classification in classifications:
        for statement in classification["hazard_statements"]:
            code = (statement.get("code") or "").replace(" ", "")
            classifications_for_statement = hazard_reference.get(code, [])
            if not classifications_for_statement and "+" in code:
                classifications_for_statement = [
                    detail
                    for component in code.split("+")
                    for detail in hazard_reference.get(component, [])
                ]
            statement["classifications"] = _deduplicate_dicts(
                classifications_for_statement,
                ("hazard_class", "category"),
            )

    precautionary_statements = [
        {
            "code": code,
            "statement": p_statement_texts.get(code),
            "url": f"{PUBCHEM_GHS_URL}#{quote(code, safe='+')}",
        }
        for code in p_codes
    ]
    hazard_groups = {
        "Physical": [],
        "Health": [],
        "Environmental": [],
        "Uncategorized": [],
    }
    for classification in classifications:
        for statement in classification["hazard_statements"]:
            hazard_groups[statement["area"]].append(statement)

    storage_information = _storage_information(sections, references)
    pairwise_evidence = _pairwise_evidence(sections, references)
    return {
        "cid": cid,
        "available": bool(sections),
        "message": None if sections else "PubChem returned no Safety and Hazards sections.",
        "ghs_classifications": classifications,
        "hazard_groups": hazard_groups,
        "precautionary_statements": precautionary_statements,
        "precautionary_statement_error": p_statement_error,
        "ghs_reference_error": hazard_reference_error,
        "storage_information": storage_information,
        "supplier_sds_references": _supplier_sds_references(all_information),
        "pairwise_evidence": pairwise_evidence,
        "source_references": _used_references(all_information, references),
        "source_urls": {
            "pubchem_safety_annotations": source_url,
            "pubchem_ghs_reference": PUBCHEM_GHS_URL,
            "pubchem_ghs_hazard_reference": PUBCHEM_GHS_TSV_URL,
        },
    }


def _supplier_explicitly_reports_no_conflict(profile: Dict[str, Any]) -> bool:
    for row in profile.get("storage_information", []):
        if not any(
            isinstance(citation, str) and _SDS_PATTERN.search(citation)
            for citation in row.get("reference_citations", [])
        ):
            continue
        if _NO_CONFLICT_PATTERN.search(row.get("value_text", "")):
            return True
    return False


def analyze_storage_pair(
    chemical_a: Dict[str, Any], chemical_b: Dict[str, Any]
) -> Dict[str, Any]:
    """Compare explicit PubChem incompatibility evidence without inferring compatibility."""
    cid_a = chemical_a.get("cid")
    cid_b = chemical_b.get("cid")
    if not isinstance(cid_a, int) or not isinstance(cid_b, int):
        raise ValueError("Both chemicals must have a valid PubChem CID.")

    profile_a = get_compound_safety_profile(cid_a)
    profile_b = get_compound_safety_profile(cid_b)
    evidence = [
        item
        for item in profile_a["pairwise_evidence"]
        if item.get("related_cid") == cid_b
    ] + [
        item
        for item in profile_b["pairwise_evidence"]
        if item.get("related_cid") == cid_a
    ]
    evidence = _deduplicate_dicts(
        evidence,
        ("related_cid", "heading", "reference_number", "source_name"),
    )
    verified_evidence = [
        item for item in evidence if item.get("source_verified") is True
    ]
    reported_evidence = [
        item for item in evidence if item.get("user_reported")
    ]

    if verified_evidence:
        status = "Verified Segregation Warning"
        explanation = (
            "A PubChem-linked source explicitly identifies this chemical pair. "
            "Review the cited evidence and the exact product SDS before handling or storage."
        )
    elif (
        _supplier_explicitly_reports_no_conflict(profile_a)
        and _supplier_explicitly_reports_no_conflict(profile_b)
    ):
        status = "No Listed Conflict Identified"
        explanation = (
            "Both available supplier SDS citations explicitly report no listed "
            "incompatibility. This is not a compatibility certification; verify "
            "the exact products, concentrations, and current SDS revisions."
        )
    else:
        status = "SDS Review Required"
        explanation = (
            "No sufficiently verified pair-specific segregation statement was found "
            "in the retrieved PubChem annotations. Missing data does not indicate "
            "compatibility; review both exact product SDS documents before storage."
        )

    return {
        "chemical_a": {
            "cid": cid_a,
            "name": chemical_a.get("iupac_name"),
            "safety_profile": profile_a,
        },
        "chemical_b": {
            "cid": cid_b,
            "name": chemical_b.get("iupac_name"),
            "safety_profile": profile_b,
        },
        "status": status,
        "explanation": explanation,
        "evidence": verified_evidence,
        "reported_evidence": reported_evidence,
        "sources": list(
            dict.fromkeys(
                item["source_url"]
                for item in verified_evidence + reported_evidence
                if item.get("source_url")
            )
        ),
    }
