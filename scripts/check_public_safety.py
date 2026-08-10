#!/usr/bin/env python3
"""Fail closed on high-confidence public-repository privacy mistakes.

The check scans tracked, UTF-8 text files and (when requested) the author and
committer addresses on newly introduced commits. It intentionally reports the
rule and location without echoing the matched value.
"""

from __future__ import annotations

import argparse
import ipaddress
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
ALLOW_MARKER = "public-safety: allow"
MAX_TEXT_BYTES = 5 * 1024 * 1024

PERSONAL_EMAIL = re.compile(
    r"(?i)\b[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@(?:"
    r"gmail\.com|googlemail\.com|yahoo\.(?:com|co\.uk)|outlook\.com|"
    r"hotmail\.com|live\.com|icloud\.com|me\.com|proton(?:mail)?\.com|"
    r"aol\.com)\b"
)

TEXT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private-key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"
        ),
    ),
    ("github-token", re.compile(r"\b(?:gh[opusr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("openai-api-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("stripe-live-key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}\b")),
    ("credentialed-url", re.compile(r"(?i)https?://[^\s/:@]+:[^\s/@]+@")),
    ("tailscale-fqdn", re.compile(r"(?i)\b[a-z0-9-]+\.[a-z0-9-]+\.ts\.net\b")),
    ("personal-email", PERSONAL_EMAIL),
)

ASSIGNMENT = re.compile(
    r"(?ix)\b(?:api[_-]?key|client[_-]?secret|access[_-]?token|"
    r"auth[_-]?token|password|passwd)\b\s*[:=]\s*[\"']?([^\s\"'#]{8,})"
)
OBVIOUS_PLACEHOLDER = re.compile(
    r"(?i)(?:^/etc/|^\$\{|<[^>]+>|example|placeholder|changeme|dummy|"
    r"redacted|not-a-real|your[_-])"
)
IPV4_CANDIDATE = re.compile(r"(?<![0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9.])")


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout


def tracked_files() -> list[Path]:
    return [ROOT / item for item in git("ls-files", "-z").split("\0") if item]


def is_reportable_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if address.is_loopback or address.is_unspecified:
        return False
    if address in ipaddress.ip_network("192.0.2.0/24"):
        return False
    if address in ipaddress.ip_network("198.51.100.0/24"):
        return False
    if address in ipaddress.ip_network("203.0.113.0/24"):
        return False
    return True


def scan_tree() -> list[str]:
    findings: list[str] = []
    for path in tracked_files():
        if path == SELF or not path.is_file() or path.stat().st_size > MAX_TEXT_BYTES:
            continue
        raw = path.read_bytes()
        if b"\0" in raw:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        relative = path.relative_to(ROOT)
        for line_number, line in enumerate(text.splitlines(), 1):
            if ALLOW_MARKER in line:
                continue
            for rule_name, pattern in TEXT_RULES:
                if pattern.search(line):
                    findings.append(f"{relative}:{line_number}: {rule_name}")
            assignment = ASSIGNMENT.search(line)
            if assignment and not OBVIOUS_PLACEHOLDER.search(assignment.group(1)):
                findings.append(f"{relative}:{line_number}: credential-assignment")
            for candidate in IPV4_CANDIDATE.findall(line):
                if is_reportable_ip(candidate):
                    findings.append(f"{relative}:{line_number}: ip-address")
    return findings


def scan_commit_range(commit_range: str) -> list[str]:
    findings: list[str] = []
    output = git("log", "--format=%H%x00%ae%x00%ce", commit_range)
    for row in output.splitlines():
        parts = row.split("\0")
        if len(parts) != 3:
            continue
        commit, author_email, committer_email = parts
        if PERSONAL_EMAIL.fullmatch(author_email):
            findings.append(f"commit {commit[:12]}: personal-author-email")
        if PERSONAL_EMAIL.fullmatch(committer_email):
            findings.append(f"commit {commit[:12]}: personal-committer-email")
    return findings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--commit-range",
        help="Git revision range containing only newly introduced commits",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    findings = scan_tree()
    if args.commit_range:
        findings.extend(scan_commit_range(args.commit_range))
    findings = sorted(set(findings))
    if findings:
        print("Public-safety check failed:", file=sys.stderr)
        for finding in findings:
            print(f"- {finding}", file=sys.stderr)
        print(
            "Remove the sensitive value or document a narrowly reviewed synthetic "
            f"exception on the same line with '{ALLOW_MARKER}'.",
            file=sys.stderr,
        )
        return 1
    print("Public-safety check passed: no high-confidence findings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
