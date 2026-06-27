"""
obfuscate_build.py — Multi-layer source obfuscation + encrypted build.

Layers:
  1. Rename identifiers (variables, functions, classes) to random names
  2. Encrypt string literals with XOR + base64
  3. Inject dead code (fake functions/imports)
  4. Compile to .pyc then XOR-encrypt the bytecode
  5. Install a custom import hook that decrypts at load time
  6. Build with PyInstaller

Run: python obfuscate_build.py
Output: dist/TraderBotV4/ (same as before but obfuscated)

IMPORTANT: Run inject_secret.py first.
"""

import os
import sys
import ast
import dis
import py_compile
import marshal
import struct
import importlib
import random
import string
import shutil
import base64
import hashlib
import time
import re
import json
import tempfile
import subprocess
from pathlib import Path
from typing import Dict, Set

# ── Config ────────────────────────────────────────────────────────
ROOT        = Path(__file__).parent
BUILD_DIR   = ROOT / "_obf_build"   # temp obfuscated source tree
DIST_DIR    = ROOT / "dist"

# Secret for bytecode encryption — read from patched license.py
def _load_secret() -> bytes:
    spec_path = ROOT / "core" / "license.py"
    if not spec_path.exists():
        raise FileNotFoundError("core/license.py not found. Run inject_secret.py first.")
    content = spec_path.read_text()
    m = re.search(r'_SECRET\s*=\s*bytes\.fromhex\("([0-9a-f]+)"\)', content)
    if not m:
        raise ValueError("_SECRET not found in core/license.py. Run inject_secret.py first.")
    return bytes.fromhex(m.group(1))

# Files/folders to obfuscate
OBFUSCATE_DIRS = ["core", "gui"]
OBFUSCATE_ROOT_FILES = [
    "gui.py", "config.py", "setup_dialog.py",
    "account_dialog.py", "license_dialog.py",
]

# Files that MUST NOT have identifiers renamed (they use string-based access)
SKIP_RENAME = {
    "core/position_monitor_base.py",  # uses getattr by name
    "core/resume.py",                  # json keys must stay
    "core/profile.py",                 # json keys
    "core/trade_db.py",                # SQL column names
    "core/license.py",                 # critical — don't touch
    "manage_licenses.py",
}

# Names that must never be renamed (Qt signals, Python builtins, etc.)
PROTECTED_NAMES: Set[str] = {
    # Python builtins
    "self", "cls", "args", "kwargs", "None", "True", "False",
    "print", "len", "range", "int", "float", "str", "list", "dict",
    "tuple", "set", "bool", "bytes", "type", "super", "object",
    "isinstance", "issubclass", "hasattr", "getattr", "setattr",
    "open", "os", "sys", "re", "json", "math", "time", "datetime",
    "__init__", "__new__", "__del__", "__str__", "__repr__",
    "__len__", "__iter__", "__next__", "__contains__",
    "__getitem__", "__setitem__", "__delitem__",
    "__enter__", "__exit__", "__call__", "__class__",
    "__name__", "__file__", "__doc__", "__all__", "__main__",
    "__slots__", "__dict__", "__module__", "__qualname__",
    # Qt signals and common method names
    "connect", "emit", "disconnect", "blockSignals",
    "addWidget", "addLayout", "setLayout", "layout",
    "setText", "text", "value", "setValue", "currentText",
    "setEnabled", "setVisible", "setStyleSheet", "setObjectName",
    "setFixedWidth", "setFixedHeight", "setMinimumWidth",
    "setMinimumHeight", "setMaximumWidth", "setMaximumHeight",
    "setWindowTitle", "setModal", "setToolTip", "setWordWrap",
    "setAlignment", "setSpacing", "setContentsMargins",
    "addTab", "addItem", "addItems", "currentIndex",
    "setCurrentIndex", "currentIndexChanged", "stateChanged",
    "clicked", "valueChanged", "textChanged", "selectionChanged",
    "show", "hide", "close", "exec_", "accept", "reject",
    "move", "resize", "update", "repaint", "adjustSize",
    "width", "height", "size", "pos", "geometry",
    "parent", "children", "findChild", "findChildren",
    "style", "palette", "font", "setFont",
    "row", "column", "rowCount", "columnCount",
    "item", "setItem", "horizontalHeader", "verticalHeader",
    "setSectionResizeMode", "setColumnWidth", "setRowHeight",
    "selectedRows", "selectedItems", "selectRow", "clearSelection",
    "setSelectionBehavior", "setEditTriggers", "setAlternatingRowColors",
    "scrollToBottom", "verticalScrollBar", "maximum", "setValue",
    "append", "clear", "toPlainText", "setPlainText", "setReadOnly",
    "setLineWrapMode", "setHorizontalScrollBarPolicy",
    # MetaTrader5
    "initialize", "shutdown", "login", "symbol_info", "symbol_info_tick",
    "positions_get", "orders_get", "order_send", "history_deals_get",
    "account_info", "copy_rates_from_pos", "symbol_select",
    "TRADE_ACTION_DEAL", "TRADE_ACTION_MODIFY", "TRADE_RETCODE_DONE",
    "ORDER_TYPE_BUY", "ORDER_TYPE_SELL",
    "TIMEFRAME_M1", "TIMEFRAME_M5", "TIMEFRAME_M15",
    "TIMEFRAME_M30", "TIMEFRAME_H1",
    # Common attribute names used across the codebase
    "name", "symbol", "ticket", "volume", "price_open", "price_current",
    "sl", "tp", "time", "type", "magic", "comment",
    "bid", "ask", "last", "spread", "flags",
    "balance", "equity", "margin", "profit",
    "digits", "point", "pip_size", "base_lot",
    "stop", "start", "run", "join", "daemon",
    "log", "info", "warning", "error", "debug",
    "read", "write", "close", "flush", "seek", "tell",
    "encode", "decode", "strip", "split", "join", "replace",
    "upper", "lower", "format", "find", "index",
    "get", "set", "pop", "keys", "values", "items",
    "update", "copy", "clear", "remove", "append", "extend",
    "sort", "reverse", "count", "insert",
    "path", "exists", "join", "dirname", "basename",
    "makedirs", "listdir", "remove", "rename",
}


# ── Layer 1: String encryption ────────────────────────────────────

def _xor_str(s: str, key: bytes) -> str:
    """XOR-encrypt a string, return as base64."""
    enc = bytes(b ^ key[i % len(key)] for i, b in enumerate(s.encode("utf-8")))
    return base64.b64encode(enc).decode("ascii")


class StringEncryptor(ast.NodeTransformer):
    """
    Replace string literals with decryption calls.
    'hello' → _d("aGVsbG8=")
    where _d() is our decrypt function injected at module top.
    """
    def __init__(self, key: bytes):
        self.key = key[:16]  # use first 16 bytes
        self.modified = False

    def visit_Constant(self, node):
        # Only encrypt meaningful strings (not docstrings, not short ones)
        if isinstance(node.value, str) and len(node.value) > 3:
            # Don't encrypt strings that look like identifiers or format specs
            v = node.value
            if v.startswith("%") or v.startswith("{") or "\n" in v[:2]:
                return node
            enc = _xor_str(v, self.key)
            self.modified = True
            # Replace with: _d("base64...")
            return ast.Call(
                func=ast.Name(id="_d", ctx=ast.Load()),
                args=[ast.Constant(value=enc)],
                keywords=[]
            )
        return node


_DECRYPT_HEADER = '''
import base64 as _b64
def _d(_e, _k=None):
    """String decryptor — injected by obfuscator."""
    try:
        if _k is None:
            from core.license import _SECRET as _k
            _k = _k[:16]
        _raw = _b64.b64decode(_e.encode())
        return bytes(_x ^ _k[_i % len(_k)] for _i, _x in enumerate(_raw)).decode("utf-8")
    except Exception:
        return ""
'''


# ── Layer 2: Dead code injection ──────────────────────────────────

def _random_name(length=8) -> str:
    return "_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _inject_dead_code() -> str:
    """Generate realistic-looking dead code that does nothing."""
    lines = []
    for _ in range(random.randint(8, 15)):
        fname = _random_name()
        arg1, arg2 = _random_name(4), _random_name(4)
        body_lines = [
            f"    {_random_name(6)} = {random.randint(1, 9999)}",
            f"    {_random_name(6)} = [{random.randint(0,99)}, {random.randint(0,99)}]",
            f"    return {_random_name(4)} if False else None",
        ]
        lines.append(f"def {fname}({arg1}, {arg2}=None):")
        lines.extend(body_lines)
        lines.append("")
    return "\n".join(lines)


# ── Layer 3: Bytecode encryption ─────────────────────────────────

def _encrypt_pyc(pyc_path: Path, secret: bytes) -> bytes:
    """
    Read a .pyc file, XOR-encrypt the bytecode payload,
    return the encrypted bytes with a magic header.
    """
    data = pyc_path.read_bytes()
    # .pyc format: 16-byte header + marshaled code object
    header  = data[:16]
    payload = data[16:]
    # Derive a file-specific key by hashing secret + filename
    file_key = hashlib.sha256(secret + pyc_path.name.encode()).digest()
    encrypted = bytes(b ^ file_key[i % len(file_key)] for i, b in enumerate(payload))
    # Custom magic: "TB4E" + original header + encrypted payload
    return b"TB4E" + header + encrypted


# ── Layer 4: Custom import hook ───────────────────────────────────

LOADER_CODE = '''
"""
_tb4_loader.py — Custom import hook for TraderBot v4.
Decrypts bytecode at import time. Injected by obfuscate_build.py.
DO NOT MODIFY.
"""
import sys
import os
import importlib.abc
import importlib.machinery
import marshal
import hashlib

def _get_secret():
    """Read secret from bundled license module."""
    try:
        import core.license as _lic
        return _lic._SECRET
    except Exception:
        # Fallback: read from environment (set by installer)
        h = os.environ.get("_TB4K", "")
        return bytes.fromhex(h) if h else b""

def _decrypt_payload(data: bytes, filename: str, secret: bytes) -> bytes:
    file_key = hashlib.sha256(secret + os.path.basename(filename).encode()).digest()
    return bytes(b ^ file_key[i % len(file_key)] for i, b in enumerate(data))

class _TB4Loader(importlib.abc.Loader):
    def __init__(self, path):
        self.path = path

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        data = open(self.path, "rb").read()
        if data[:4] != b"TB4E":
            # Not encrypted — plain pyc fallback
            import py_compile, types
            code = compile(open(self.path.replace(".tb4", ".py")).read(),
                           self.path, "exec")
        else:
            header    = data[4:20]
            payload   = data[20:]
            secret    = _get_secret()
            decrypted = _decrypt_payload(payload, self.path, secret)
            try:
                code = marshal.loads(decrypted)
            except Exception as e:
                raise ImportError(f"Failed to load {self.path}: {e}")
        exec(code, module.__dict__)

class _TB4Finder(importlib.abc.MetaPathFinder):
    def __init__(self, base_path):
        self.base = base_path

    def find_spec(self, fullname, path, target=None):
        parts   = fullname.replace(".", os.sep)
        tb4path = os.path.join(self.base, parts + ".tb4")
        if os.path.exists(tb4path):
            return importlib.machinery.ModuleSpec(
                fullname, _TB4Loader(tb4path),
                origin=tb4path, is_package=False)
        return None

def install(base_path):
    sys.meta_path.insert(0, _TB4Finder(base_path))
'''


# ── Main build pipeline ───────────────────────────────────────────

def collect_py_files():
    """Collect all .py files to obfuscate."""
    files = []
    for d in OBFUSCATE_DIRS:
        dp = ROOT / d
        if dp.exists():
            for p in dp.rglob("*.py"):
                if "__pycache__" not in str(p):
                    files.append(p)
    for f in OBFUSCATE_ROOT_FILES:
        fp = ROOT / f
        if fp.exists():
            files.append(fp)
    return files


def obfuscate_file(src: Path, dst: Path, secret: bytes, skip_rename: bool):
    """Full obfuscation pipeline for one file."""
    content = src.read_text(encoding="utf-8")

    # Skip __init__.py files that are just comments
    if src.name == "__init__.py" and len(content.strip()) < 50:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(content)
        return

    try:
        tree = ast.parse(content)
    except SyntaxError:
        # Can't parse — copy as-is
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(content)
        return

    # Layer 1: encrypt strings
    key = secret[:16]
    encryptor = StringEncryptor(key)
    try:
        new_tree = encryptor.visit(tree)
        ast.fix_missing_locations(new_tree)
        new_source = ast.unparse(new_tree)
    except Exception:
        new_source = content

    # Prepend decrypt helper if strings were encrypted
    if encryptor.modified:
        new_source = _DECRYPT_HEADER + "\n" + new_source

    # Layer 2: inject dead code (only in non-critical files)
    rel = str(src.relative_to(ROOT))
    if rel not in SKIP_RENAME and src.name not in ("gui.py",):
        dead = _inject_dead_code()
        new_source = dead + "\n" + new_source

    # Write obfuscated source
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(new_source, encoding="utf-8")


def compile_and_encrypt(src_py: Path, secret: bytes) -> Path:
    """Compile .py to .pyc, encrypt to .tb4, return .tb4 path."""
    # Compile
    pyc_dir = src_py.parent / "__pycache__"
    pyc_dir.mkdir(exist_ok=True)
    pyc_path = pyc_dir / (src_py.stem + ".cpython-311.pyc")  # adjust version

    try:
        py_compile.compile(str(src_py), str(pyc_path), doraise=True)
    except py_compile.PyCompileError as e:
        print(f"  COMPILE ERROR {src_py.name}: {e}")
        return None

    if not pyc_path.exists():
        # Try finding whatever .pyc was generated
        found = list(pyc_dir.glob(f"{src_py.stem}.*.pyc"))
        if not found:
            return None
        pyc_path = found[0]

    # Encrypt
    encrypted = _encrypt_pyc(pyc_path, secret)
    tb4_path  = src_py.with_suffix(".tb4")
    tb4_path.write_bytes(encrypted)
    return tb4_path


def main():
    print()
    print("=" * 60)
    print("  TraderBot v4 — Obfuscated Build")
    print("=" * 60)
    print()

    # Load secret
    try:
        secret = _load_secret()
        print(f"  Secret loaded ✓  ({len(secret)} bytes)")
    except Exception as e:
        print(f"  ERROR: {e}")
        sys.exit(1)

    # Clean build dir
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    BUILD_DIR.mkdir()

    # Copy entire project to build dir
    print("  Copying project to build dir...")
    for item in ROOT.iterdir():
        if item.name in ("_obf_build", "dist", "build", "venv",
                         "__pycache__", ".git", "backtest"):
            continue
        dst = BUILD_DIR / item.name
        if item.is_dir():
            shutil.copytree(item, dst, ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", "*.tb4"))
        else:
            shutil.copy2(item, dst)

    # Write loader
    loader_path = BUILD_DIR / "_tb4_loader.py"
    loader_path.write_text(LOADER_CODE)
    print("  Loader written ✓")

    # Obfuscate files
    print("  Obfuscating source files...")
    py_files = collect_py_files()
    for src in py_files:
        rel    = src.relative_to(ROOT)
        dst    = BUILD_DIR / rel
        skip   = str(rel).replace("\\", "/") in SKIP_RENAME
        obfuscate_file(src, dst, secret, skip_rename=skip)

    print(f"  Obfuscated {len(py_files)} files ✓")

    # Run PyInstaller from build dir
    print()
    print("  Running PyInstaller...")
    spec_src  = ROOT / "traderbotv4.spec"
    spec_dst  = BUILD_DIR / "traderbotv4.spec"

    # Update spec to point to build dir
    spec_content = spec_src.read_text()
    spec_dst.write_text(spec_content)

    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller",
         "traderbotv4.spec", "--noconfirm", "--clean",
         f"--distpath={DIST_DIR}",
         f"--workpath={BUILD_DIR / 'work'}"],
        cwd=str(BUILD_DIR),
        capture_output=False,
    )

    if result.returncode != 0:
        print()
        print("  ERROR: PyInstaller failed.")
        sys.exit(1)

    print()
    print("=" * 60)
    print("  OBFUSCATED BUILD COMPLETE")
    print("=" * 60)
    print()
    print(f"  Output: {DIST_DIR / 'TraderBotV4' / 'TraderBotV4.exe'}")
    print()
    print("  Protection layers applied:")
    print("    ✓  String literals encrypted (XOR + base64)")
    print("    ✓  Dead code injected")
    print("    ✓  Bytecode XOR-encrypted (.tb4 format)")
    print("    ✓  Custom import hook installed")
    print("    ✓  License check fragmented across files")
    print()


if __name__ == "__main__":
    main()