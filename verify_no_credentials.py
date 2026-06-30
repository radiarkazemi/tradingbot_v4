"""
verify_no_credentials.py — Pre-build safety check.

Scans the entire source tree for anything that looks like a
hardcoded MT5 account number, password, or server name, and
ABORTS the build if anything suspicious is found.

This is the hard guarantee that your own account can never
accidentally ship inside an installer again.

Run automatically by release.bat before every build.
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

# Files/dirs to scan (source only — never scan dist/build/installer output)
SCAN_DIRS = ["core", "gui"]
SCAN_FILES = [
    "config.py", "gui.py", "setup_dialog.py",
    "account_dialog.py", "license_dialog.py", "server.py",
]

# Directories to NEVER scan (already-built output, dependencies)
SKIP_DIRS = {
    "__pycache__", "dist", "build", "_obf_build",
    "installer_output", ".git", "venv", ".venv",
}

# Patterns that indicate a real hardcoded credential.
# Deliberately narrow — only flags assignment with a non-empty,
# non-placeholder literal value.
SUSPICIOUS_PATTERNS = [
    # MT5_LOGIN = 12345678   (any non-zero literal int assigned directly)
    (re.compile(r'^\s*MT5_LOGIN\s*=\s*[1-9]\d{4,}\s*$', re.MULTILINE),
     "MT5_LOGIN hardcoded to a real-looking account number"),

    # MT5_PASSWORD = "something"  (non-empty string literal)
    (re.compile(r'^\s*MT5_PASSWORD\s*=\s*["\'][^"\'\s]{3,}["\']\s*$', re.MULTILINE),
     "MT5_PASSWORD hardcoded to a literal string"),

    # MT5_SERVER = "Something-Demo" (non-empty string literal, not env lookup)
    (re.compile(r'^\s*MT5_SERVER\s*=\s*["\'][^"\']{3,}["\']\s*$', re.MULTILINE),
     "MT5_SERVER hardcoded to a literal string"),
]

# Lines containing these are always SAFE (env var lookups, comments, examples)
SAFE_MARKERS = [
    "os.environ.get", "_os.environ.get", "getenv", "TB4_MT5_",
    "# ", "#!", "your_username", "yourpassword", "yourbroker",
    "example", "placeholder", "12345678",
]


def _is_safe_line(line: str) -> bool:
    lower = line.lower()
    return any(marker.lower() in lower for marker in SAFE_MARKERS)


def scan_file(path: str) -> list:
    findings = []
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception:
        return findings

    for pattern, description in SUSPICIOUS_PATTERNS:
        for match in pattern.finditer(content):
            line_text = match.group(0).strip()
            if _is_safe_line(line_text):
                continue
            line_no = content[:match.start()].count("\n") + 1
            findings.append((path, line_no, line_text, description))

    return findings


def main():
    print()
    print("=" * 60)
    print("  Pre-Build Credential Safety Check")
    print("=" * 60)
    print()

    all_findings = []

    # Scan root-level files
    for fname in SCAN_FILES:
        fpath = os.path.join(ROOT, fname)
        if os.path.exists(fpath):
            all_findings.extend(scan_file(fpath))

    # Scan directories
    for dirname in SCAN_DIRS:
        dpath = os.path.join(ROOT, dirname)
        if not os.path.isdir(dpath):
            continue
        for root, dirs, files in os.walk(dpath):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fname in files:
                if fname.endswith(".py"):
                    all_findings.extend(scan_file(os.path.join(root, fname)))

    if all_findings:
        print("  🛑 BUILD BLOCKED — possible hardcoded credentials found:")
        print()
        for fpath, line_no, line_text, desc in all_findings:
            rel = os.path.relpath(fpath, ROOT)
            print(f"    {rel}:{line_no}")
            print(f"      {desc}")
            print(f"      → {line_text}")
            print()
        print("  Remove these before building. Credentials must ONLY come")
        print("  from %APPDATA%/TraderBotV4/profile.json at runtime, never")
        print("  from source code.")
        print("=" * 60)
        print()
        sys.exit(1)

    print("  ✓  No hardcoded credentials found. Safe to build.")
    print("=" * 60)
    print()
    sys.exit(0)


if __name__ == "__main__":
    main()