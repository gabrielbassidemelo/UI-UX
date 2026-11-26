#!/usr/bin/env bash
# Ensaio de recuperação: baixa o backup MAIS RECENTE do armazenamento externo, restaura num diretório
# temporário, mostra contagens e apaga tudo. NÃO toca no banco em uso nem em $HEALTHMON_HOME/backups.
# Uso (na VM, com o venv do healthmon ativo):  bash deploy/oracle/ensaio-recuperacao.sh
set -euo pipefail

if ! command -v python >/dev/null 2>&1; then
  echo "Ative o ambiente do healthmon antes (ex.: . .venv/bin/activate)." >&2
  exit 1
fi

TMP="$(mktemp -d)"
chmod 700 "$TMP"
KEY=""
cleanup() {
  KEY=""
  # apaga o banco restaurado (dados de saúde) e o backup baixado
  if command -v shred >/dev/null 2>&1; then
    find "$TMP" -type f -exec shred -u {} + 2>/dev/null || true
  fi
  rm -rf "$TMP"
  echo "Diretório temporário apagado."
}
trap cleanup EXIT

echo "Cole a chave de recuperação (HMRK-...). A digitação não aparece na tela."
read -r -s -p "Chave: " KEY
echo
if [ -z "$KEY" ]; then
  echo "Chave vazia." >&2
  exit 1
fi

# A chave segue por um descritor de arquivo (fd 3), não por argumento nem variável de ambiente.
python - "$TMP" 3<<<"$KEY" <<'PY'
import os, sqlite3, sys
from pathlib import Path

from healthmon import backup, offsite
from healthmon.config import load_config

tmp = Path(sys.argv[1])
key = os.fdopen(3).read().strip()
cfg = load_config()

if not offsite.configured(cfg):
    sys.exit("OCI_PAR_URL não está configurada em secrets.env (ou no ambiente).")

try:
    arq = offsite.download_latest(cfg, tmp / "baixado")
    print(f"Baixado: {arq.name} ({arq.stat().st_size} bytes)")
    h = backup.read_header(arq)
    if h:
        print(f"Criado em (UTC): {h.get('criado_utc')}   dias declarados: {h.get('linhas_daily_metrics')}")
    destino = tmp / "restaurado.db"
    backup.restore_backup(cfg, arq, key, target=destino)      # target explícito: o banco em uso não é tocado
except (offsite.OffsiteError, backup.BackupError) as e:
    sys.exit(f"FALHA no ensaio: {e}")

con = sqlite3.connect(destino)
try:
    print("integrity_check:", con.execute("PRAGMA integrity_check").fetchone()[0])
    print("Linhas por tabela:")
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY 1").fetchall():
        print(f"  {t:28s} {con.execute('SELECT COUNT(*) FROM "' + t.replace('"', '""') + '"').fetchone()[0]}")
    print("Último dia com métricas:", con.execute("SELECT MAX(date) FROM daily_metrics").fetchone()[0])
finally:
    con.close()
print("ENSAIO OK: o backup externo é recuperável com a sua chave.")
PY
