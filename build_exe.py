"""Buduje samodzielny ClaudeAutoShutdown.exe (PyInstaller, onefile, bez konsoli).

Uzycie:
    python -m pip install -e .[build]
    python build_exe.py

Wynik: dist/ClaudeAutoShutdown.exe. Plik jest niepodpisany - Windows SmartScreen
pokaze ostrzezenie przy pierwszym uruchomieniu pobranego pliku (README o tym mowi).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ICON = ROOT / "assets" / "icon.ico"
NAME = "ClaudeAutoShutdown"


def main() -> int:
    if not ICON.exists():
        subprocess.run([sys.executable, str(ROOT / "make_icon.py")], check=True)
    for stale in (ROOT / "build", ROOT / "dist"):
        shutil.rmtree(stale, ignore_errors=True)
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--windowed",                      # brak okna konsoli; bledy ida do crash.log
        "--name", NAME,
        "--icon", str(ICON),
        "--add-data", f"{ICON}{';' if sys.platform == 'win32' else ':'}assets",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
        str(ROOT / "autoshutdown.py"),
    ]
    print(" ".join(cmd))
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print(f"PyInstaller zakonczyl sie kodem {result.returncode}")
        return result.returncode
    exe = ROOT / "dist" / f"{NAME}.exe"
    if not exe.exists():
        print("brak pliku wynikowego:", exe)
        return 1
    print(f"OK: {exe} ({exe.stat().st_size / 1_048_576:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
