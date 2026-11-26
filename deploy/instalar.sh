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
echo "  1) .venv/bin/healthmon senha-dashboard"
echo "  2) .venv/bin/healthmon chaves-recuperacao  # chave de recuperação dos backups (guarde fora do computador)"
echo "  3) .venv/bin/healthmon trocar-chave-garmin # chave pessoal oficial da Garmin, digitação oculta"
echo "  4) .venv/bin/healthmon validar-garmin && .venv/bin/healthmon historico --dias 90"
echo "  Em servidor (Oracle), use deploy/oracle/configurar.sh em vez deste script."
echo "  5) Instale o serviço: deploy/healthmon.service (Linux) ou deploy/com.healthmon.plist (macOS)"
