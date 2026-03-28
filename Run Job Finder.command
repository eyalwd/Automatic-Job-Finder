#!/bin/zsh
cd "$(dirname "$0")"
source .venv/bin/activate
caffeinate -i python main.py
echo ""
echo "Done. Press any key to close."
read -k1
