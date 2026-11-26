#!/usr/bin/env bash
# Instalação nativa (macOS/Linux/Raspberry Pi) em um venv local.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip >/dev/null
pip install -e .
healthmon init
echo
echo "Próximos passos:"
echo "  1) Edite ~/.healthmon/secrets.env (frase-senha de backup; Telegram opcional)"
echo "  2) .venv/bin/healthmon login              # e-mail/senha Garmin + MFA, uma única vez"
echo "  3) .venv/bin/healthmon senha-dashboard"
echo "  4) .venv/bin/healthmon historico --dias 90"
echo "  5) Instale o serviço: deploy/healthmon.service (Linux) ou deploy/com.healthmon.plist (macOS)"
