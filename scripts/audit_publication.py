#!/usr/bin/env python3
"""Audit a prepared public repository without printing credential values.

Run against the exact directory/files selected for publication. An optional
private provider environment file is read only to detect exact credential
matches; it must remain outside the publication directory. No network is used.
"""

import argparse
import json
import os
from pathlib import Path
import re
from urllib.parse import quote, urlsplit


TOKEN_PATTERNS = (
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{30,})\b")),
    ("api_secret_token", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
)
URL = re.compile(r"https?://[^\s\"'<>`]+")
PLACEHOLDERS = re.compile(r"(?:your|example|placeholder|test|private|secret|dummy|docs-demo|demo)", re.I)
LOCAL_PATH = re.compile(r"/Users/[A-Za-z0-9_.-]+/")
SYNTHETIC_PROVIDER_URLS = {
    "https://eth-mainnet.g.alchemy.com/v2/a%2Fb%3Fc%23d%20e%2Bf%25",
}


def known_secrets(path):
    values = {}
    if path is None:
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        key, value = key.strip(), value.strip().strip("\"'")
        sensitive = any(part in key.upper() for part in ("API_KEY", "TOKEN", "PASSWORD", "SECRET", "RPC_URL"))
        sensitive |= key.startswith(("QUICKNODE_", "ALCHEMY_")) and key.endswith("_URL")
        if not sensitive or len(value) < 8:
            continue
        for variant in {value, quote(value, safe="")}:
            values.setdefault(variant, set()).add(key)
    return values


def audit(root, secrets):
    findings, inspected = [], 0
    for directory, folders, filenames in os.walk(root):
        relative_dir = Path(directory).relative_to(root)
        if ".git" in folders:
            folders.remove(".git")
        for name in list(folders):
            if name in {"data", "__pycache__", ".venv", "node_modules", ".pytest_cache"}:
                findings.append({"severity": "error", "path": str(relative_dir / name), "kind": "local_runtime_directory"})
                folders.remove(name)
        for name in filenames:
            path = Path(directory) / name
            relative = str(path.relative_to(root))
            lowered = name.lower()
            forbidden = ((lowered.startswith(".env") or lowered.endswith(".env")) and name != ".env.example")
            forbidden |= bool(re.search(r"\.(?:sqlite3?|db)(?:-(?:wal|shm|journal))?$|\.py[co]$|\.(?:pem|key|p12|pfx)$", lowered))
            if forbidden:
                findings.append({"severity": "error", "path": relative, "kind": "runtime_or_credential_file"})
                continue
            if path.is_symlink():
                findings.append({"severity": "error", "path": relative, "kind": "publication_symlink"})
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                findings.append({"severity": "error", "path": relative, "kind": "unreviewed_binary"})
                continue
            inspected += 1
            matches = sorted({key for value, keys in secrets.items() if value in text for key in keys})
            if matches:
                findings.append({"severity": "error", "path": relative, "kind": "configured_credential_match", "key_names": matches})
            for kind, pattern in TOKEN_PATTERNS:
                if pattern.search(text):
                    findings.append({"severity": "error", "path": relative, "kind": kind})
            private_urls = 0
            for match in URL.finditer(text):
                value = match.group(0).rstrip(",.;)")
                try:
                    parsed = urlsplit(value)
                    host = parsed.hostname or ""
                except ValueError:
                    continue
                provider = host.endswith((".quiknode.pro", ".quicknode.pro")) or (
                    host.endswith(".g.alchemy.com") and bool(parsed.path.partition("/v2/")[2]))
                if provider and value not in SYNTHETIC_PROVIDER_URLS and not PLACEHOLDERS.search(value):
                    private_urls += 1
            if private_urls:
                findings.append({"severity": "error", "path": relative, "kind": "potential_private_provider_url", "count": private_urls})
            if LOCAL_PATH.search(text):
                findings.append({"severity": "warning", "path": relative, "kind": "nonportable_local_user_path"})
    return {"files_inspected": inspected, "errors": sum(row["severity"] == "error" for row in findings),
            "warnings": sum(row["severity"] == "warning" for row in findings), "findings": findings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Exact prepared repository directory")
    parser.add_argument("--secrets-env", type=Path, help="Private environment file to compare, never printed")
    args = parser.parse_args()
    if not args.root.is_dir():
        parser.error("root must be a prepared repository directory")
    result = audit(args.root.resolve(), known_secrets(args.secrets_env))
    print(json.dumps(result, indent=2))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
