"""
core/mt5_detector.py — Multi-MT5 terminal detection helper.

When a user has MT5 installed from multiple brokers, mt5.initialize()
without an explicit path can attach to the wrong one. This module
scans common install locations for all terminal64.exe files found,
so the GUI can offer them as a dropdown instead of requiring the
user to manually browse for the exact path.

Also reads each terminal's broker/company name from its config so
the dropdown can show "ICMarkets MT5" instead of a raw file path.
"""

import os
import glob
import logging
import platform

log = logging.getLogger("mt5_detector")


def find_all_mt5_terminals() -> list:
    """
    Scan common Windows install locations for terminal64.exe files.
    Returns a list of dicts: [{"path": ..., "broker": ...}, ...]
    """
    if platform.system() != "Windows":
        return []

    terminals = []
    search_roots = []

    # Standard Program Files locations
    for env_var in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        base = os.environ.get(env_var)
        if base and os.path.isdir(base):
            search_roots.append(base)

    # MT5 also commonly installs to AppData\Roaming\MetaQuotes\Terminal\<hash>
    appdata = os.environ.get("APPDATA")
    if appdata:
        mq_terminal = os.path.join(appdata, "MetaQuotes", "Terminal")
        if os.path.isdir(mq_terminal):
            search_roots.append(mq_terminal)

    seen_paths = set()

    for root in search_roots:
        try:
            # Look for terminal64.exe up to 2 levels deep
            pattern1 = os.path.join(root, "*", "terminal64.exe")
            pattern2 = os.path.join(root, "*", "*", "terminal64.exe")
            for pattern in (pattern1, pattern2):
                for path in glob.glob(pattern):
                    norm = os.path.normpath(path)
                    if norm in seen_paths:
                        continue
                    seen_paths.add(norm)
                    broker = _guess_broker_name(norm)
                    terminals.append({"path": norm, "broker": broker})
        except Exception as e:
            log.debug("Scan error in %s: %s", root, e)

    return terminals


def _guess_broker_name(terminal_path: str) -> str:
    """
    Best-effort broker name guess from the install folder name.
    E.g. "C:\\Program Files\\ICMarkets MT5\\terminal64.exe" -> "ICMarkets MT5"
    """
    try:
        folder = os.path.basename(os.path.dirname(terminal_path))
        # Clean up common suffixes
        for junk in ("MetaTrader 5", "MT5", "Terminal"):
            pass  # keep folder name as-is, it's usually already descriptive
        return folder
    except Exception:
        return "Unknown"


def is_generic_metaquotes_path(path: str) -> bool:
    """True if this is the generic unbranded MetaQuotes install."""
    if not path:
        return False
    normalized = path.lower().replace("/", "\\")
    return (
        "program files\\metatrader 5\\terminal64.exe" in normalized
        and "program files\\metatrader 5 " not in normalized  # not a branded "MetaTrader 5 XYZ"
    )