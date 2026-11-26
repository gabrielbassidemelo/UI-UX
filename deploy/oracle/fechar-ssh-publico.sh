#!/usr/bin/env bash
# Depois de confirmar que o acesso por Tailscale SSH funciona, feche o SSH público.
# Também remova a regra de entrada da porta 22 na Security List da VCN no console da Oracle.
set -euo pipefail
tailscale status >/dev/null || { echo "Tailscale não está ativo; não vou fechar o SSH."; exit 1; }
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow in on tailscale0
sudo ufw --force enable
sudo ufw status verbose
