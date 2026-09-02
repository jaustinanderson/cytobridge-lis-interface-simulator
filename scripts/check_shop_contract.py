#!/usr/bin/env python3
"""Validate the durable CytoBridge manifest and its human-readable shop card.

The validator intentionally uses only the Python standard library. It checks
the canary's fixed identity and safety boundary while leaving product behavior
to the existing test and demonstration suites.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "PROJECT_MANIFEST.json"
CARD_PATH = ROOT / "SHOP_CARD.md"

EXPECTED_TOP_LEVEL = {
    "manifest_version",
    "project_id",
    "repository",
    "purpose",
    "durable_status",
    "ownership",
    "authority",
    "boundaries",
    "validation",
    "rollback",
    "monitoring",
    "maintenance",
    "capability_bridge",
    "lifecycle_policy",
}

EXPECTED_RESOURCES = [
    ".github/workflows/ci.yml",
    "PROJECT_MANIFEST.json",
    "SHOP_CARD.md",
    "scripts/check_shop_contract.py",
]

EXPECTED_COMMANDS = [
    "python scripts/check_shop_contract.py",
    "python -m pytest -q",
    "python -m src.demo_run",
    "python scripts/check_public_safety.py --commit-range BASE_SHA..HEAD_SHA",
    "git diff --check BASE_SHA..HEAD_SHA",
]

EXPECTED_PROHIBITED_CLAIMS = {
    "CERTIFIED_HL7_CONFORMANCE",
    "CERTIFIED_FHIR_CONFORMANCE",
    "CLINICAL_VALIDITY",
    "EPIC_BEAKER_BUILD_EXPERIENCE",
    "PRODUCTION_READINESS",
}

REQUIRED_HEADINGS = [
    "## What it is",
    "## What this project proves",
    "## What it does not prove",
    "## Safety and privacy boundary",
    "## Ownership and authority",
    "## How to validate",
    "## Rollback",
    "## Monitoring and maintenance",
    "## Austin mastery boundary",
    "## Next bounded learning bridge",
]

FORBIDDEN_TRANSIENT_KEYS = {
    "pull_request",
    "pull_request_url",
    "current_branch",
    "head_sha",
    "draft",
    "awaiting_review",
}


def load_manifest(errors: list[str]) -> dict[str, Any]:
    try:
        document = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"PROJECT_MANIFEST.json is unreadable or invalid JSON: {exc}")
        return {}
    if not isinstance(document, dict):
        errors.append("PROJECT_MANIFEST.json must contain one JSON object")
        return {}
    return document


def value_at(document: dict[str, Any], path: str, errors: list[str]) -> Any:
    value: Any = document
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            errors.append(f"missing required manifest field: {path}")
            return None
        value = value[part]
    return value


def require_equal(
    document: dict[str, Any], path: str, expected: Any, errors: list[str]
) -> None:
    actual = value_at(document, path, errors)
    if actual is not None and actual != expected:
        errors.append(f"{path} must equal {expected!r}; found {actual!r}")


def find_forbidden_keys(value: Any, location: str = "manifest") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}"
            if key in FORBIDDEN_TRANSIENT_KEYS:
                findings.append(child_location)
            findings.extend(find_forbidden_keys(child, child_location))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(find_forbidden_keys(child, f"{location}[{index}]"))
    return findings


def validate_manifest(document: dict[str, Any], errors: list[str]) -> None:
    if set(document) != EXPECTED_TOP_LEVEL:
        errors.append(
            "top-level manifest keys must match the durable canary contract; "
            f"found {sorted(document)}"
        )

    require_equal(document, "manifest_version", "1.0.0", errors)
    require_equal(
        document, "project_id", "CYTOBRIDGE-LIS-INTERFACE-SIMULATOR", errors
    )
    require_equal(
        document,
        "repository.name",
        "jaustinanderson/cytobridge-lis-interface-simulator",
        errors,
    )
    require_equal(document, "repository.default_branch", "main", errors)
    require_equal(document, "durable_status.state", "COMPLETE", errors)
    require_equal(
        document, "durable_status.autonomous_routine", "DISABLED", errors
    )
    require_equal(document, "boundaries.data_classification", "SYNTHETIC_ONLY", errors)
    require_equal(document, "rollback.changed_resources", EXPECTED_RESOURCES, errors)
    require_equal(document, "validation.commands", EXPECTED_COMMANDS, errors)
    require_equal(
        document,
        "authority.merge_without_exact_head_authorization",
        "PROHIBITED",
        errors,
    )
    require_equal(
        document,
        "rollback.destructive_actions",
        "SEPARATE_AUTHORIZATION_REQUIRED",
        errors,
    )
    require_equal(
        document,
        "lifecycle_policy.austin_mastered",
        "DEMONSTRATION_REQUIRED_NOT_INFERRED_FROM_AI_OUTPUT",
        errors,
    )

    claims = value_at(document, "boundaries.prohibited_claims", errors)
    if isinstance(claims, list) and set(claims) != EXPECTED_PROHIBITED_CLAIMS:
        errors.append("boundaries.prohibited_claims does not match the required set")

    allowed = value_at(document, "boundaries.allowed_data", errors)
    prohibited = value_at(document, "boundaries.prohibited_data", errors)
    if isinstance(allowed, list) and isinstance(prohibited, list):
        overlap = set(allowed) & set(prohibited)
        if overlap:
            errors.append(f"allowed_data and prohibited_data overlap: {sorted(overlap)}")

    for location in find_forbidden_keys(document):
        errors.append(f"transient pull-request state is forbidden in {location}")


def validate_card(document: dict[str, Any], errors: list[str]) -> None:
    try:
        card = CARD_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"SHOP_CARD.md is unreadable: {exc}")
        return

    try:
        card.encode("ascii")
    except UnicodeEncodeError:
        errors.append("SHOP_CARD.md must remain plain ASCII")

    for heading in REQUIRED_HEADINGS:
        if heading not in card:
            errors.append(f"SHOP_CARD.md is missing heading: {heading}")

    for command in EXPECTED_COMMANDS:
        if command not in card:
            errors.append(f"SHOP_CARD.md is missing validation command: {command}")

    required_phrases = [
        "jaustinanderson/cytobridge-lis-interface-simulator",
        "All examples and identifiers must remain synthetic.",
        "Austin owns product decisions, merge decisions, releases, and consequential",
        "does not establish Epic",
        "AI-generated files and passing checks do not demonstrate Austin's mastery.",
    ]
    for phrase in required_phrases:
        if phrase not in card:
            errors.append(f"SHOP_CARD.md is missing required boundary text: {phrase}")

    for phrase in ("awaiting review", "current branch", "draft PR", "head SHA"):
        if phrase.lower() in card.lower():
            errors.append(f"SHOP_CARD.md contains transient state phrase: {phrase}")

    repository_name = value_at(document, "repository.name", errors)
    if isinstance(repository_name, str) and repository_name not in card:
        errors.append("manifest repository name is not present in SHOP_CARD.md")


def main() -> int:
    errors: list[str] = []
    document = load_manifest(errors)
    if document:
        validate_manifest(document, errors)
        validate_card(document, errors)

    if errors:
        print("Shop contract validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("Shop contract validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
