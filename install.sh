#!/bin/sh
set -e
command -v uv >/dev/null 2>&1 || { curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="$HOME/.local/bin:$PATH"; }
uv tool install git+https://github.com/isglobal-brge/easy-opal.git
echo "Done. Run: easy-opal setup"
