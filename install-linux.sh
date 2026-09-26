#!/usr/bin/env bash
# Instaluje skrot Claude AutoShutdown na Linuksie (GNOME / KDE / XFCE - standard XDG).
#
#   ./install-linux.sh              skrot w menu aplikacji + na pulpicie
#   ./install-linux.sh --autostart  dodatkowo start po zalogowaniu (program startuje ROZBROJONY)
#   ./install-linux.sh --uninstall  usuwa wszystkie trzy skroty
#
# Program dziala z tego katalogu - config.json, autoshutdown.log i STOP leza obok
# autoshutdown.py. Nic nie jest kopiowane poza plikami .desktop.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="claude-autoshutdown.desktop"
MENU_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"

if [[ "${1:-}" == "--uninstall" ]]; then
  rm -f "$MENU_DIR/$NAME" "$DESKTOP_DIR/$NAME" "$AUTOSTART_DIR/$NAME"
  echo "Usunieto skroty ($MENU_DIR, $DESKTOP_DIR, $AUTOSTART_DIR)."
  exit 0
fi

PYTHON="$(command -v python3 || true)"
if [[ -z "$PYTHON" ]]; then
  echo "Brak python3." >&2
  exit 1
fi
if ! "$PYTHON" -c "import tkinter" 2>/dev/null; then
  echo "Brak Tkintera. Zainstaluj: sudo apt install python3-tk" >&2
  exit 1
fi
if ! command -v busctl >/dev/null; then
  echo "Uwaga: brak busctl (systemd) - program nie sprawdzi uprawnien do wylaczenia." >&2
fi

write_entry() {
  local target="$1"
  mkdir -p "$(dirname "$target")"
  cat >"$target" <<EOF
[Desktop Entry]
Type=Application
Version=1.5
Name=Claude AutoShutdown
Comment=Shut down only after every Claude Code session has finished
Comment[pl]=Wylacza komputer dopiero gdy wszystkie sesje Claude Code skoncza prace
Exec="$PYTHON" "$APP_DIR/autoshutdown.py"
Path=$APP_DIR
Icon=$APP_DIR/assets/icon.png
Terminal=false
Categories=Utility;
StartupNotify=true
StartupWMClass=Claudeautoshutdown
EOF
  chmod +x "$target"
}

[[ -f "$APP_DIR/assets/icon.png" ]] || "$PYTHON" "$APP_DIR/make_icon.py" >/dev/null

write_entry "$MENU_DIR/$NAME"
write_entry "$DESKTOP_DIR/$NAME"
# GNOME uruchamia skrot z pulpitu dopiero, gdy jest oznaczony jako zaufany.
gio set "$DESKTOP_DIR/$NAME" metadata::trusted true 2>/dev/null || true
command -v update-desktop-database >/dev/null && update-desktop-database "$MENU_DIR" 2>/dev/null || true
echo "Skrot: $MENU_DIR/$NAME"
echo "Skrot: $DESKTOP_DIR/$NAME"

if [[ "${1:-}" == "--autostart" ]]; then
  write_entry "$AUTOSTART_DIR/$NAME"
  echo "Autostart: $AUTOSTART_DIR/$NAME"
fi
