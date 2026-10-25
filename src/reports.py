"""Report generation for CytoBridge.

v1 provides a plain-text report summary built from structured FISH results.
It also provides ``parse_iscn``, which validates karyotype strings against a
small, documented educational subset of ISCN (International System for Human
Cytogenomic Nomenclature) and returns structured clones (task P4-001).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field


def build_report_summary(conn: sqlite3.Connection, order_id: int) -> str:
    """Build a human-readable summary of an order's FISH results.

    Lists each probe with its scored/abnormal cell counts and interpretation,
    then an overall line noting whether any probe was abnormal.
    """
    header = conn.execute(
        "SELECT o.accession_number, o.priority, pa.panel_name, "
        "       pt.mrn, pt.last_name, pt.first_name "
        "FROM lab_order o "
        "JOIN panel pa   ON pa.panel_id = o.panel_id "
        "JOIN patient pt ON pt.patient_id = o.patient_id "
        "WHERE o.order_id = ?",
        (order_id,),
    ).fetchone()
    if header is None:
        raise ValueError(f"Order {order_id} does not exist.")

    rows = conn.execute(
        "SELECT pr.probe_code, pr.probe_name, pr.target, "
        "       fr.cells_scored, fr.cells_abnormal, fr.interpretation, "
        "       fr.signal_pattern "
        "FROM fish_result fr "
        "JOIN probe pr ON pr.probe_id = fr.probe_id "
        "WHERE fr.order_id = ? "
        "ORDER BY pr.probe_id",
        (order_id,),
    ).fetchall()

    lines: list[str] = []
    lines.append(f"{header['panel_name']} — Accession {header['accession_number']}")
    lines.append(
        f"Patient: {header['last_name']}, {header['first_name']} "
        f"(MRN {header['mrn']})   Priority: {header['priority']}"
    )
    lines.append("")
    lines.append("Probe results:")

    abnormal_targets: list[str] = []
    for r in rows:
        pct = (
            (100.0 * r["cells_abnormal"] / r["cells_scored"])
            if r["cells_scored"]
            else 0.0
        )
        lines.append(
            f"  - {r['probe_name']} ({r['target']}): "
            f"{r['cells_abnormal']}/{r['cells_scored']} cells "
            f"({pct:.1f}%) — {r['interpretation']}"
        )
        if r["interpretation"] == "ABNORMAL":
            abnormal_targets.append(r["target"])

    lines.append("")
    if abnormal_targets:
        lines.append("Overall: ABNORMAL — " + "; ".join(abnormal_targets))
    else:
        lines.append("Overall: No abnormality detected by this FISH panel.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ISCN karyotype parser - educational subset (task P4-001)
# ---------------------------------------------------------------------------
#
# Cytogenetics reports carry ISCN nomenclature strings such as
#   "46,XY,t(9;22)(q34;q11.2)[20]"   or   "47,XX,+8[15]/46,XX[5]".
# ISCN is a large standard. This parser accepts only a small, documented
# subset and REJECTS everything else, telling the caller whether the input is
# a construct outside the subset ("unsupported") or a broken example of a
# supported construct ("malformed"). It never returns a partial parse as valid.
#
# Accepted (all synthetic examples):
#   - a chromosome total, then XX or XY;
#   - autosomal gains and losses, e.g. +8, -7 (autosomes 1-22);
#   - simple two-autosome translocations, e.g. t(9;22)(q34;q11.2);
#   - written-out clones separated by "/", each optionally ending in a
#     positive cell count, e.g. [20].
# Checks performed: lexical band syntax (arm p/q, region/band digits, optional
# sub-band) - not proof that a band exists on that chromosome; lower-numbered
# chromosome first in a translocation; abnormalities in chromosome-number
# order; and, because only balanced translocations and autosomal gains/losses
# are accepted, chromosome total = 46 + gains - losses.
# Out of scope (rejected as unsupported): deletions, duplications, inversions,
# isochromosomes, additional material, derivatives and other structural forms;
# sex-chromosome gains/losses and complements other than XX/XY; markers;
# translocations involving X or Y or more than two chromosomes; constitutional
# "c"; composite [cp]; ranges "~"; uncertainty "?"; idem/sl/sdl; chimerism "//";
# FISH (nuc ish) nomenclature. No clinical meaning is attached to any result.

MAX_ISCN_LENGTH = 512
MAX_ISCN_CLONES = 8
ISCN_SUBSET_NOTE = (
    "Accepted by CytoBridge's educational ISCN karyotype subset (chromosome "
    "total, XX/XY, autosomal gains and losses, simple two-autosome "
    "translocations, written-out clones and cell counts). Not full ISCN "
    "validation and not a clinical interpretation."
)

_COUNT_RE = re.compile(r"[1-9]\d{0,2}")
_CELLS_RE = re.compile(r"[1-9]\d{0,3}")
_NUMERICAL_RE = re.compile(r"([+-])([1-9]\d?)")
_BAND = r"[pq]\d{1,2}(?:\.\d{1,3})?"
_TRANSLOCATION_RE = re.compile(rf"t\(([1-9]\d?);([1-9]\d?)\)\(({_BAND});({_BAND})\)")
_CLONE_CELLS_RE = re.compile(r"(.*)\[([^\[\]]*)\]")
_STRUCTURAL_PREFIXES = (
    "del(", "dup(", "inv(", "i(", "add(", "der(", "ins(", "r(", "dic(",
    "idic(", "ider(", "hsr(", "fra(", "trc(", "tas(", "rob(",
)


@dataclass(frozen=True)
class ISCNIssue:
    """Why a string was rejected: ``kind`` is "unsupported" or "malformed"."""

    kind: str
    token: str
    message: str


@dataclass(frozen=True)
class ISCNClone:
    """One accepted clone (cell line) of a karyotype."""

    chromosome_count: int
    sex_chromosomes: str
    abnormalities: tuple[str, ...]
    cell_count: int | None


@dataclass
class ISCNParseResult:
    """Structured output of parsing an ISCN karyotype string.

    ``is_valid`` means "accepted by the documented educational subset", not
    full ISCN conformance or clinical validity. When ``is_valid`` is False,
    ``clones`` and ``abnormalities`` are empty and ``issues`` says why.
    """

    raw: str
    is_valid: bool = False
    abnormalities: list[str] = field(default_factory=list)
    notes: str = ""
    clones: list[ISCNClone] = field(default_factory=list)
    issues: list[ISCNIssue] = field(default_factory=list)


def _malformed(token: str, message: str) -> ISCNIssue:
    return ISCNIssue("malformed", token, message)


def _unsupported(token: str, message: str) -> ISCNIssue:
    return ISCNIssue("unsupported", token, message)


def _reject(raw: str, issues: list[ISCNIssue]) -> ISCNParseResult:
    first = issues[0]
    return ISCNParseResult(
        raw=raw,
        is_valid=False,
        notes=f"Rejected ({first.kind}): {first.message}",
        issues=issues,
    )


def _classify_unknown_abnormality(token: str) -> ISCNIssue:
    """Explain an abnormality token that is not an accepted form."""
    if token == "":
        return _malformed(token, "Empty field between commas.")
    if "?" in token or "~" in token:
        return _unsupported(token, "Uncertainty ('?') and range ('~') markers are outside this subset.")
    if token.startswith("t("):
        first_group = token[2:].split(")", 1)[0]
        if first_group.count(";") >= 2:
            return _unsupported(token, "Translocations of three or more chromosomes are outside this subset.")
        if any(part in ("X", "Y") for part in first_group.split(";")):
            return _unsupported(token, "Translocations involving X or Y are outside this subset.")
        return _malformed(token, "Write a translocation as t(a;b)(band;band), e.g. t(9;22)(q34;q11.2).")
    if token.startswith(_STRUCTURAL_PREFIXES):
        return _unsupported(token, f"'{token.split('(', 1)[0]}' rearrangements are outside this subset.")
    if re.fullmatch(r"[+-][XY]+c?", token):
        return _unsupported(token, "Sex-chromosome gains and losses are outside this subset.")
    if re.fullmatch(r"\+\d*mar\d*", token) or token in ("idem", "sl", "sdl") or token.startswith(("idem", "sdl")):
        return _unsupported(token, "Marker chromosomes and idem/sl/sdl shorthand are outside this subset.")
    if token.endswith("c") and _NUMERICAL_RE.fullmatch(token[:-1]):
        return _unsupported(token, "Constitutional ('c') changes are outside this subset.")
    return _malformed(token, "Unrecognized abnormality.")


def _parse_clone(text: str) -> tuple[ISCNClone | None, list[ISCNIssue]]:
    if text == "":
        return None, [_malformed("", "Empty clone between '/' separators.")]
    cell_count: int | None = None
    body = text
    if "[" in text or "]" in text:
        match = _CLONE_CELLS_RE.fullmatch(text)
        if not match:
            return None, [_malformed(text, "A cell count is one bracketed number at the end of a clone, e.g. [20].")]
        body, cells = match.group(1), match.group(2)
        if cells.startswith("cp"):
            return None, [_unsupported(f"[{cells}]", "Composite karyotypes ([cp...]) are outside this subset.")]
        if not _CELLS_RE.fullmatch(cells):
            return None, [_malformed(f"[{cells}]", "A cell count must be a positive whole number.")]
        cell_count = int(cells)

    tokens = body.split(",")
    if len(tokens) < 2:
        return None, [_malformed(body, "A clone starts with a chromosome total and XX or XY, e.g. 46,XX.")]
    count_token, sex_token, abnormality_tokens = tokens[0], tokens[1], tokens[2:]
    issues: list[ISCNIssue] = []

    count: int | None = None
    if _COUNT_RE.fullmatch(count_token):
        count = int(count_token)
    elif "~" in count_token or "<" in count_token:
        issues.append(_unsupported(count_token, "Chromosome-number ranges and ploidy shorthand are outside this subset."))
    else:
        issues.append(_malformed(count_token, "The chromosome total must be a whole number, e.g. 46."))

    if sex_token not in ("XX", "XY"):
        if sex_token in ("idem", "sl") or sex_token.startswith("sdl"):
            issues.append(_unsupported(sex_token, "idem/sl/sdl clone shorthand is outside this subset; write each clone out."))
        elif re.fullmatch(r"[XY]{1,6}c?", sex_token) or re.fullmatch(r"[+-][XY]+c?", sex_token):
            issues.append(_unsupported(sex_token, "Only XX or XY is accepted; other sex-chromosome complements are outside this subset."))
        else:
            issues.append(_malformed(sex_token, "XX or XY must follow the chromosome total, e.g. 46,XY."))

    gains = losses = 0
    last_chromosome = 0
    accepted: list[str] = []
    for token in abnormality_tokens:
        chromosome: int | None = None
        numerical = _NUMERICAL_RE.fullmatch(token)
        translocation = _TRANSLOCATION_RE.fullmatch(token)
        if numerical:
            number = int(numerical.group(2))
            if number > 22:
                issues.append(_malformed(token, "Autosomes are numbered 1-22."))
                continue
            if numerical.group(1) == "+":
                gains += 1
            else:
                losses += 1
            chromosome = number
        elif translocation:
            first, second = int(translocation.group(1)), int(translocation.group(2))
            if first > 22 or second > 22:
                issues.append(_malformed(token, "Autosomes are numbered 1-22."))
                continue
            if first == second:
                issues.append(_malformed(token, "A translocation joins two different chromosomes."))
                continue
            if first > second:
                issues.append(_malformed(token, "List the lower-numbered chromosome first, e.g. t(9;22)."))
                continue
            chromosome = first
        else:
            issues.append(_classify_unknown_abnormality(token))
            continue
        if chromosome < last_chromosome:
            issues.append(_malformed(token, "List abnormalities in chromosome-number order."))
            continue
        last_chromosome = chromosome
        accepted.append(token)

    if not issues and count is not None:
        expected = 46 + gains - losses
        if count != expected:
            issues.append(
                _malformed(
                    count_token,
                    f"Chromosome total {count} does not equal 46 + {gains} gain(s) - {losses} loss(es) = {expected}.",
                )
            )
    if issues:
        return None, issues
    assert count is not None
    return ISCNClone(count, sex_token, tuple(accepted), cell_count), []


def parse_iscn(iscn_text: str) -> ISCNParseResult:
    """Parse an ISCN karyotype string against the educational subset above.

    Returns an accepted result with structured clones, or a rejected result
    (``is_valid=False``) whose ``issues`` classify every problem found as
    unsupported notation or malformed supported notation. The raw input is
    always preserved unchanged in ``raw``.
    """
    if not isinstance(iscn_text, str):
        raise TypeError("parse_iscn expects a string.")
    raw = iscn_text
    text = iscn_text.strip()
    if not text:
        return _reject(raw, [_malformed("", "Empty ISCN string.")])
    if len(text) > MAX_ISCN_LENGTH:
        return _reject(raw, [_malformed(text[:24], f"Longer than {MAX_ISCN_LENGTH} characters.")])
    if any(ch.isspace() for ch in text):
        return _reject(raw, [_malformed(text, "ISCN karyotypes contain no spaces.")])
    if "//" in text:
        return _reject(raw, [_unsupported("//", "Chimerism ('//') is outside this subset.")])
    clone_texts = text.split("/")
    if len(clone_texts) > MAX_ISCN_CLONES:
        return _reject(raw, [_malformed(text[:24], f"More than {MAX_ISCN_CLONES} clones.")])

    clones: list[ISCNClone] = []
    issues: list[ISCNIssue] = []
    for clone_text in clone_texts:
        clone, clone_issues = _parse_clone(clone_text)
        issues.extend(clone_issues)
        if clone is not None:
            clones.append(clone)
    if issues:
        return _reject(raw, issues)

    abnormalities: list[str] = []
    for clone in clones:
        for item in clone.abnormalities:
            if item not in abnormalities:
                abnormalities.append(item)
    return ISCNParseResult(
        raw=raw,
        is_valid=True,
        abnormalities=abnormalities,
        notes=ISCN_SUBSET_NOTE,
        clones=clones,
    )
