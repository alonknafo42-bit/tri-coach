#!/bin/bash
cd "$(dirname "$0")" || exit 1
echo
echo "  ============================================"
echo "    Ari Coach — installation"
echo "  ============================================"
echo
if pgrep -x Claude >/dev/null 2>&1; then
  echo "  [!] Claude Desktop is open. Close it completely, then run me again."
  echo "      (it rewrites its own settings while running)"
  echo
  read -r; exit 1
fi
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "  Installing uv (one time, ~20 seconds)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh || { echo "  [X] uv install failed"; read -r; exit 1; }
  export PATH="$HOME/.local/bin:$PATH"
fi
command -v uv >/dev/null 2>&1 || { echo "  [X] uv not found. Reopen this window and try again."; read -r; exit 1; }
uv run python install.py
rc=$?
echo
[ $rc -eq 0 ] && echo "  Done. Read what is printed above — two steps are left." \
              || echo "  [X] Something failed. Send the text above to Alon."
echo
echo "  Press Enter to close."
read -r
