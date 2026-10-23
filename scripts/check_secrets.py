#!/usr/bin/env python3
"""Fail when likely credentials occur in tracked files or Git history.

Only locations are reported; matching values are never printed.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),  # secret-scan: allow
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[oprsu]_[A-Za-z0-9_]{30,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}\b"),
)
ASSIGNMENT = re.compile(
    r"(?:ZETTLE_API_KEY|LEXOFFICE_API_KEY|API_TOKEN|ACCESS_TOKEN)\s*=\s*([^\s#]+)"
)
PLACEHOLDERS = ("...", "xxx", "example", "placeholder", "test", "fake", "your_")


def _looks_secret(line: str) -> bool:
    if "secret-scan: allow" in line:
        return False
    if any(pattern.search(line) for pattern in PATTERNS):
        return True
    match = ASSIGNMENT.search(line)
    if not match:
        return False
    value = match.group(1).strip("\"'").lower()
    return len(value) >= 16 and not any(marker in value for marker in PLACEHOLDERS)


def _tracked_files() -> list[Path]:
    output = subprocess.check_output(["git", "ls-files", "-z"])
    return [Path(item.decode()) for item in output.split(b"\0") if item]


def main() -> int:
    findings: list[str] = []
    for path in _tracked_files():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(lines, 1):
            if _looks_secret(line):
                findings.append(f"{path}:{number}")

    history = subprocess.check_output(
        ["git", "log", "--all", "-p", "--format="], text=True, errors="replace"
    )
    history_findings = sum(_looks_secret(line) for line in history.splitlines())

    if findings or history_findings:
        print("Mögliche Geheimnisse gefunden (Werte werden nicht ausgegeben):")
        for location in findings:
            print(f"  {location}")
        if history_findings:
            print(f"  Git-Historie: {history_findings} mögliche Treffer")
        return 1

    print("Keine möglichen Geheimnisse in versionierten Dateien oder Historie gefunden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
