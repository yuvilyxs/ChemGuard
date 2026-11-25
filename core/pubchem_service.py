"""Cached access to compound data from the official PubChem APIs."""

from __future__ import annotations

import csv
import io
import re
import threading
import time
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests
import streamlit as st

PUBCHEM_REST_URL = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
PUBCHEM_VIEW_URL = "https://pubchem.ncbi.nlm.nih.gov/rest/pug_view"
PUBCHEM_GHS_URL = "https://pubchem.ncbi.nlm.nih.gov/ghs/ghs_11.html"
PUBCHEM_GHS_TSV_URL = "https://pubchem.ncbi.nlm.nih.gov/ghs/ghscode_11.txt"
REQUEST_TIMEOUT_SECONDS = 15
CACHE_TTL_SECONDS = 24 * 60 * 60
MAX_CIDS_PER_REQUEST = 100
REQUEST_INTERVAL_SECONDS = 0.21

_PROPERTY_NAMES = (
    "MolecularFormula",
    "MolecularWeight",
    "IUPACName",
    "SMILES",
    "ConnectivitySMILES",
    "InChI",
    "InChIKey",
    "XLogP",
    "ExactMass",
    "MonoisotopicMass",
    "TPSA",
    "Complexity",
    "Charge",
    "HBondDonorCount",
    "HBondAcceptorCount",
    "RotatableBondCount",
    "HeavyAtomCount",
    "IsotopeAtomCount",
    "AtomStereoCount",
    "DefinedAtomStereoCount",
    "UndefinedAtomStereoCount",
    "BondStereoCount",
    "DefinedBondStereoCount",
    "UndefinedBondStereoCount",
    "CovalentUnitCount",
)

_CAS_PATTERN = re.compile(r"\d{2,7}-\d{2}-\d")
_CID_PATTERN = re.compile(r"(?i)CID[:\s]+(\d+)")
_FORMULA_PATTERN = re.compile(r"(?:[A-Z][a-z]?\d*)+")
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_STARTED = 0.0


class _PrecautionaryStatementParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.statements: Dict[str, str] = {}
        self._in_precautionary_section = False
        self._in_bold = False
        self._bold_text: List[str] = []
        self._current_code: Optional[str] = None
        self._description: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag == "h2" and dict(attrs).get("id") == "_prec":
            self._in_precautionary_section = True
        elif self._in_precautionary_section and tag == "b":
            self._in_bold = True
            self._bold_text = []
        elif self._in_precautionary_section and tag == "br":
            self._save_statement()

    def handle_endtag(self, tag: str) -> None:
        if tag == "b" and self._in_bold:
            self._in_bold = False
            candidate = "".join(self._bold_text).strip().rstrip(":")
            if re.fullmatch(r"P\d{3}(?:\+P\d{3})*(?:\[\+P\d{3}\])?", candidate):
                self._save_statement()
                self._current_code = candidate
        elif tag in {"p", "h3"} and self._in_precautionary_section:
            self._save_statement()

    def handle_data(self, data: str) -> None:
        if not self._in_precautionary_section:
            return
        if self._in_bold:
            self._bold_text.append(data)
        elif self._current_code is not None:
            self._description.append(data)

    def _save_statement(self) -> None:
        if self._current_code is not None:
            description = " ".join(
                part.strip() for part in self._description if part.strip()
            )
            if description:
                self.statements[self._current_code] = description
        self._current_code = None
        self._description = []


class PubChemError(Exception):
    """Base exception for PubChem lookup failures."""


class PubChemNotFoundError(PubChemError):
    """Raised when PubChem does not contain a requested record."""


class PubChemTimeoutError(PubChemError):
    """Raised when a PubChem request exceeds its timeout."""


class PubChemAPIError(PubChemError):
    """Raised when PubChem returns an unexpected or unsuccessful response."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _pace_requests() -> None:
    global _LAST_REQUEST_STARTED

    with _REQUEST_LOCK:
        elapsed = time.monotonic() - _LAST_REQUEST_STARTED
        if elapsed < REQUEST_INTERVAL_SECONDS:
            time.sleep(REQUEST_INTERVAL_SECONDS - elapsed)
        _LAST_REQUEST_STARTED = time.monotonic()


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def _get_json(url: str) -> Dict[str, Any]:
    _pace_requests()
    try:
        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": "ChemGuard/1.0 (PubChem PUG REST client)"},
        )
    except requests.Timeout as exc:
        raise PubChemTimeoutError(
            f"PubChem request timed out after {REQUEST_TIMEOUT_SECONDS} seconds."
        ) from exc
    except requests.RequestException as exc:
        raise PubChemAPIError(f"Unable to reach PubChem: {exc}") from exc

    if response.status_code == 404:
        raise PubChemNotFoundError("PubChem did not find a matching record.")
    if not response.ok:
        detail = response.text.strip().replace("\n", " ")[:500]
        raise PubChemAPIError(
            f"PubChem returned HTTP {response.status_code}: {detail}",
            status_code=response.status_code,
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise PubChemAPIError("PubChem returned an invalid JSON response.") from exc
    if not isinstance(payload, dict):
        raise PubChemAPIError("PubChem returned an unexpected JSON response.")
    return payload


@st.cache_data(ttl=7 * 24 * 60 * 60, show_spinner=False)
def _get_ghs_precautionary_catalog() -> Dict[str, str]:
    _pace_requests()
    try:
        response = requests.get(
            PUBCHEM_GHS_URL,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": "ChemGuard/1.0 (PubChem GHS reference client)"},
        )
    except requests.Timeout as exc:
        raise PubChemTimeoutError(
            f"PubChem GHS reference request timed out after {REQUEST_TIMEOUT_SECONDS} seconds."
        ) from exc
    except requests.RequestException as exc:
        raise PubChemAPIError(f"Unable to reach the PubChem GHS reference: {exc}") from exc
    if not response.ok:
        raise PubChemAPIError(
            f"PubChem GHS reference returned HTTP {response.status_code}.",
            status_code=response.status_code,
        )

    parser = _PrecautionaryStatementParser()
    parser.feed(response.text)
    if not parser.statements:
        raise PubChemAPIError("PubChem returned no precautionary statement reference data.")
    return parser.statements


def get_precautionary_statement_descriptions(codes: List[str]) -> Dict[str, Optional[str]]:
    """Resolve P-codes against PubChem's official GHS Rev. 11 statement reference."""
    catalog = _get_ghs_precautionary_catalog()
    return {code: catalog.get(code) for code in codes}


@st.cache_data(ttl=7 * 24 * 60 * 60, show_spinner=False)
def _get_ghs_hazard_catalog() -> Dict[str, List[Dict[str, str]]]:
    _pace_requests()
    try:
        response = requests.get(
            PUBCHEM_GHS_TSV_URL,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": "ChemGuard/1.0 (PubChem GHS reference client)"},
        )
    except requests.Timeout as exc:
        raise PubChemTimeoutError(
            f"PubChem GHS reference request timed out after {REQUEST_TIMEOUT_SECONDS} seconds."
        ) from exc
    except requests.RequestException as exc:
        raise PubChemAPIError(f"Unable to reach the PubChem GHS reference: {exc}") from exc
    if not response.ok:
        raise PubChemAPIError(
            f"PubChem GHS reference returned HTTP {response.status_code}.",
            status_code=response.status_code,
        )

    catalog: Dict[str, List[Dict[str, str]]] = {}
    reader = csv.DictReader(io.StringIO(response.text), delimiter="\t")
    for row in reader:
        code = row.get("H-Code", "")
        if code.startswith("H"):
            catalog.setdefault(code, []).append(
                {
                    "hazard_class": row.get("GHS Hazard Class", ""),
                    "category": row.get("GHS Hazard Category", ""),
                    "signal_word": row.get("GHSSignalWord", ""),
                    "pictograms": row.get("GHS Pictogram", ""),
                }
            )
    if not catalog:
        raise PubChemAPIError("PubChem returned no GHS hazard classification reference data.")
    return catalog


def get_ghs_hazard_classifications(
    codes: List[str],
) -> Dict[str, List[Dict[str, str]]]:
    """Resolve H-codes against PubChem's official GHS class/category reference."""
    catalog = _get_ghs_hazard_catalog()
    return {code: catalog.get(code, []) for code in codes}


def _classify_query(query: str, search_type: str) -> Tuple[str, str]:
    value = query.strip()
    if not value:
        raise ValueError("A chemical name, CAS number, formula, or PubChem CID is required.")

    kind = search_type.strip().lower()
    supported_types = {"auto", "name", "cas", "formula", "cid"}
    if kind not in supported_types:
        raise ValueError(
            "search_type must be one of: auto, name, cas, formula, or cid."
        )
    if kind != "auto":
        if kind == "cid":
            cid_match = _CID_PATTERN.fullmatch(value)
            if cid_match:
                value = cid_match.group(1)
        return kind, value

    cid_match = _CID_PATTERN.fullmatch(value)
    if cid_match:
        return "cid", cid_match.group(1)
    if value.isdecimal():
        return "cid", value
    if _CAS_PATTERN.fullmatch(value):
        return "cas", value
    if _FORMULA_PATTERN.fullmatch(value):
        return "formula", value
    return "name", value


def _search_url(query: str, search_type: str) -> str:
    kind, value = _classify_query(query, search_type)
    if kind == "cid":
        if not value.isdecimal() or int(value) < 1:
            raise ValueError("A PubChem CID must be a positive integer.")
        return f"{PUBCHEM_REST_URL}/compound/cid/{int(value)}/cids/JSON"

    encoded_value = quote(value, safe="")
    if kind == "formula":
        return f"{PUBCHEM_REST_URL}/compound/fastformula/{encoded_value}/cids/JSON"
    return f"{PUBCHEM_REST_URL}/compound/name/{encoded_value}/cids/JSON"


def _cids_from_response(payload: Dict[str, Any]) -> List[int]:
    identifier_list = payload.get("IdentifierList")
    if not isinstance(identifier_list, dict):
        raise PubChemAPIError("PubChem returned an invalid compound identifier list.")
    cids = identifier_list.get("CID", [])
    if not isinstance(cids, list):
        raise PubChemAPIError("PubChem returned an invalid compound identifier list.")
    return list(
        dict.fromkeys(
            cid
            for cid in cids
            if isinstance(cid, int) and not isinstance(cid, bool) and cid > 0
        )
    )


def search_compounds(query: str, search_type: str = "auto") -> List[int]:
    """Return matching PubChem CIDs; formula searches may return multiple CIDs."""
    url = _search_url(query, search_type)
    try:
        return _cids_from_response(_get_json(url))
    except PubChemNotFoundError:
        return []


def _chunked(values: List[int], chunk_size: int) -> List[List[int]]:
    return [
        values[start : start + chunk_size]
        for start in range(0, len(values), chunk_size)
    ]


def _property_url(cids: List[int]) -> str:
    cid_path = ",".join(str(cid) for cid in cids)
    properties = ",".join(_PROPERTY_NAMES)
    return (
        f"{PUBCHEM_REST_URL}/compound/cid/{cid_path}/property/"
        f"{properties}/JSON"
    )


def _synonym_url(cids: List[int]) -> str:
    cid_path = ",".join(str(cid) for cid in cids)
    return f"{PUBCHEM_REST_URL}/compound/cid/{cid_path}/synonyms/JSON"


def _properties_by_cid(cids: List[int]) -> Dict[int, Dict[str, Any]]:
    result: Dict[int, Dict[str, Any]] = {}
    for chunk in _chunked(cids, MAX_CIDS_PER_REQUEST):
        payload = _get_json(_property_url(chunk))
        table = payload.get("PropertyTable")
        if not isinstance(table, dict) or not isinstance(
            table.get("Properties"), list
        ):
            raise PubChemAPIError("PubChem returned an invalid property table.")
        rows = table["Properties"]
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("CID"), int):
                result[row["CID"]] = row
    return result


def _synonyms_by_cid(cids: List[int]) -> Dict[int, List[str]]:
    result: Dict[int, List[str]] = {}
    for chunk in _chunked(cids, MAX_CIDS_PER_REQUEST):
        payload = _get_json(_synonym_url(chunk))
        information_list = payload.get("InformationList")
        if not isinstance(information_list, dict) or not isinstance(
            information_list.get("Information"), list
        ):
            raise PubChemAPIError("PubChem returned an invalid synonym list.")
        rows = information_list["Information"]
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("CID"), int):
                continue
            names = row.get("Synonym", [])
            if isinstance(names, str):
                names = [names]
            result[row["CID"]] = [name for name in names if isinstance(name, str)]
    return result


def _property_source_url(cid: int) -> str:
    return _property_url([cid])


def _synonym_source_url(cid: int) -> str:
    return _synonym_url([cid])


def _view_source_url(cid: int) -> str:
    return (
        f"{PUBCHEM_VIEW_URL}/data/compound/{cid}/JSON"
        "?heading=Chemical%20and%20Physical%20Properties"
    )


def get_compound_safety_record(cid: int) -> Dict[str, Any]:
    """Retrieve the PubChem PUG View Safety and Hazards annotation record for a CID."""
    if not isinstance(cid, int) or isinstance(cid, bool) or cid < 1:
        raise ValueError("cid must be a positive integer.")
    url = (
        f"{PUBCHEM_VIEW_URL}/data/compound/{cid}/JSON"
        "?heading=Safety%20and%20Hazards"
    )
    return _get_json(url)


def _flatten_property_sections(
    sections: Any, flattened: Optional[Dict[str, List[Dict[str, Any]]]] = None
) -> Dict[str, List[Dict[str, Any]]]:
    if flattened is None:
        flattened = {}
    if not isinstance(sections, list):
        return flattened

    for section in sections:
        if not isinstance(section, dict):
            continue
        heading = section.get("TOCHeading")
        information = section.get("Information", [])
        if isinstance(heading, str) and isinstance(information, list):
            for item in information:
                if not isinstance(item, dict) or "Value" not in item:
                    continue
                entry: Dict[str, Any] = {"value": item["Value"]}
                if "Name" in item:
                    entry["name"] = item["Name"]
                if "Reference" in item:
                    entry["reference"] = item["Reference"]
                if "Description" in section:
                    entry["description"] = section["Description"]
                if "URL" in section:
                    entry["url"] = section["URL"]
                flattened.setdefault(heading, []).append(entry)
        _flatten_property_sections(section.get("Section", []), flattened)
    return flattened


def _physical_chemical_properties(cid: int) -> Dict[str, List[Dict[str, Any]]]:
    try:
        payload = _get_json(_view_source_url(cid))
    except PubChemNotFoundError:
        return {}
    record = payload.get("Record")
    if not isinstance(record, dict) or not isinstance(record.get("Section", []), list):
        raise PubChemAPIError("PubChem returned an invalid physical-property record.")
    sections = record.get("Section", [])
    for section in sections:
        if (
            isinstance(section, dict)
            and section.get("TOCHeading") == "Chemical and Physical Properties"
        ):
            return _flatten_property_sections(section.get("Section", []))
    return {}


def _details_for_cids(
    cids: List[int], search_url: Optional[str] = None
) -> List[Dict[str, Any]]:
    if not cids:
        return []
    properties = _properties_by_cid(cids)
    synonyms = _synonyms_by_cid(cids)
    results: List[Dict[str, Any]] = []

    for cid in cids:
        compound_properties = properties.get(cid, {})
        results.append(
            {
                "cid": cid,
                "molecular_formula": compound_properties.get("MolecularFormula"),
                "molecular_weight": compound_properties.get("MolecularWeight"),
                "iupac_name": compound_properties.get("IUPACName"),
                "synonyms": synonyms.get(cid, []),
                "smiles": compound_properties.get("SMILES"),
                "connectivity_smiles": compound_properties.get("ConnectivitySMILES"),
                "inchi": compound_properties.get("InChI"),
                "inchi_key": compound_properties.get("InChIKey"),
                "properties": {
                    key: value
                    for key, value in compound_properties.items()
                    if key != "CID"
                },
                "physical_chemical_properties": _physical_chemical_properties(cid),
                "source_urls": {
                    "pubchem_compound": (
                        f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}"
                    ),
                    "search": search_url
                    or f"{PUBCHEM_REST_URL}/compound/cid/{cid}/cids/JSON",
                    "pug_rest_properties": _property_source_url(cid),
                    "pug_rest_synonyms": _synonym_source_url(cid),
                    "pug_view_physical_chemical_properties": _view_source_url(cid),
                },
            }
        )
    return results


def get_compound_details(cid: int) -> Dict[str, Any]:
    """Retrieve available PubChem data for one positive compound identifier."""
    if not isinstance(cid, int) or cid < 1:
        raise ValueError("cid must be a positive integer.")
    return _details_for_cids([cid])[0]


def lookup_compounds(
    query: str, search_type: str = "auto"
) -> List[Dict[str, Any]]:
    """Search by name, CAS number, formula, or CID and return compound records."""
    url = _search_url(query, search_type)
    try:
        cids = _cids_from_response(_get_json(url))
    except PubChemNotFoundError:
        return []
    return _details_for_cids(cids, search_url=url)
