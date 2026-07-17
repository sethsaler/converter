#!/usr/bin/env bash
# Install Format Converter for the current user.
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/sethsaler/converter/main/install.sh | bash
set -euo pipefail

REPO="${CONVERTER_REPO:-sethsaler/converter}"
BRANCH="${CONVERTER_BRANCH:-main}"
INSTALL_DIR="${CONVERTER_DIR:-$HOME/.local/share/converter}"
BIN_DIR="${CONVERTER_BIN:-$HOME/.local/bin}"
BIN_NAME="converter"

info()  { printf '→ %s\n' "$*"; }
ok()    { printf '✓ %s\n' "$*"; }
warn()  { printf '! %s\n' "$*" >&2; }
die()   { printf '✗ %s\n' "$*" >&2; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

need_cmd curl
need_cmd python3
need_cmd unzip

PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PY_MAJOR="$(python3 -c 'import sys; print(sys.version_info.major)')"
PY_MINOR="$(python3 -c 'import sys; print(sys.version_info.minor)')"
if (( PY_MAJOR < 3 || (PY_MAJOR == 3 && PY_MINOR < 10) )); then
  die "Python 3.10+ required (found $PY_VER)"
fi

info "Installing Format Converter → $INSTALL_DIR"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

ARCHIVE="$TMP/converter.zip"
URL="https://github.com/${REPO}/archive/refs/heads/${BRANCH}.zip"
info "Downloading $URL"
curl -fsSL "$URL" -o "$ARCHIVE"

unzip -q "$ARCHIVE" -d "$TMP"
SRC="$TMP/converter-${BRANCH}"
# GitHub may use a different folder name if the default branch tip differs
if [[ ! -d "$SRC" ]]; then
  SRC="$(find "$TMP" -maxdepth 1 -type d -name 'converter-*' | head -1)"
fi
[[ -d "$SRC" ]] || die "Could not find extracted source"

mkdir -p "$(dirname "$INSTALL_DIR")"
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
# Copy project files (exclude .git if present)
cp -R "$SRC"/. "$INSTALL_DIR"/

info "Creating virtualenv"
python3 -m venv "$INSTALL_DIR/.venv"
# shellcheck disable=SC1091
source "$INSTALL_DIR/.venv/bin/activate"
pip install -q --upgrade pip
pip install -q -r "$INSTALL_DIR/requirements.txt"
ok "Dependencies installed"

mkdir -p "$BIN_DIR"
WRAPPER="$BIN_DIR/$BIN_NAME"
cat > "$WRAPPER" <<EOF
#!/usr/bin/env bash
set -euo pipefail
ROOT="$INSTALL_DIR"
# shellcheck disable=SC1091
source "\$ROOT/.venv/bin/activate"
exec python "\$ROOT/main.py" "\$@"
EOF
chmod +x "$WRAPPER"
chmod +x "$INSTALL_DIR/run.sh" "$INSTALL_DIR/Converter.command" 2>/dev/null || true
ok "CLI installed: $WRAPPER"

# macOS: put a double-click app launcher in Applications (symlink to .command)
if [[ "$(uname -s)" == "Darwin" ]]; then
  APP_LINK="$HOME/Applications/Converter.command"
  mkdir -p "$HOME/Applications"
  ln -sfn "$INSTALL_DIR/Converter.command" "$APP_LINK"
  # Clear quarantine on the real launcher so Gatekeeper is less noisy
  xattr -dr com.apple.quarantine "$INSTALL_DIR" 2>/dev/null || true
  ok "Double-click launcher: $APP_LINK"
fi

# PATH hint
if ! echo ":$PATH:" | grep -q ":$BIN_DIR:"; then
  warn "$BIN_DIR is not on your PATH"
  SHELL_NAME="$(basename "${SHELL:-bash}")"
  case "$SHELL_NAME" in
    zsh)  RC="$HOME/.zshrc" ;;
    fish) RC="$HOME/.config/fish/config.fish" ;;
    *)    RC="$HOME/.bashrc" ;;
  esac
  if [[ "$SHELL_NAME" == "fish" ]]; then
    warn "Add:  fish_add_path $BIN_DIR"
  else
    warn "Add to $RC:"
    warn "  export PATH=\"$BIN_DIR:\$PATH\""
  fi
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  warn "ffmpeg not found — needed for video/audio. Install with: brew install ffmpeg"
fi

echo
ok "Format Converter ready"
echo
echo "  GUI:   converter"
echo "         open ~/Applications/Converter.command   # macOS double-click"
echo "  CLI:   converter photo.heic -f jpg"
echo "         converter compress video.mov -p small"
echo "         converter --formats"
echo
echo "  Uninstall: rm -rf \"$INSTALL_DIR\" \"$BIN_DIR/$BIN_NAME\""
[[ "$(uname -s)" == "Darwin" ]] && echo "             rm -f \"$HOME/Applications/Converter.command\""
echo
