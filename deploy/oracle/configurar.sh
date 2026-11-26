#!/usr/bin/env bash
# Configuração guiada do healthmon em uma VM (Oracle Always Free ou qualquer Linux com Docker).
# Execute como o usuário "healthmon" (ou outro do grupo docker), dentro do repositório clonado:
#     ./deploy/oracle/configurar.sh
# Os segredos são digitados aqui, com eco desligado, e gravados só em data/secrets.env (600).
set -euo pipefail
cd "$(dirname "$0")/../.."
umask 077
C="docker compose"
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

command -v docker >/dev/null || { echo "Docker não encontrado."; exit 1; }
mkdir -p data && chmod 700 data
if [ "$(id -u)" != "10001" ]; then sudo chown -R 10001:10001 data 2>/dev/null || true; fi

say "1/7 Construindo a imagem (alguns minutos na primeira vez)…"
$C build --quiet

say "2/7 Criando a estrutura de dados…"
$C run --rm healthmon healthmon init >/dev/null
SECRETS=data/secrets.env
put() { HM_VALUE="$2" sudo --preserve-env=HM_VALUE python3 -c '
import os,re,sys
p,n=sys.argv[1],sys.argv[2]; v=os.environ["HM_VALUE"]
ls=[l for l in open(p,encoding="utf-8").read().splitlines() if not re.match(rf"^{re.escape(n)}=",l)]
ls.append(f"{n}={v}"); open(p,"w",encoding="utf-8").write("\n".join(ls)+"\n")' "$SECRETS" "$1"; }

say "3/7 Chave pessoal da Garmin"
echo "Gere em https://connect.garmin.com/app/settings/personalApiToken e cole abaixo (não aparece na tela)."
read -r -s -p "Chave pessoal: " GK; echo
[ -n "$GK" ] || { echo "Chave vazia."; exit 1; }
put GARMIN_PERSONAL_API_KEY "$GK"; unset GK
read -r -p "Data de expiração que você escolheu para a chave (AAAA-MM-DD): " EXP
sudo sed -i "s/^chave_expira_em = .*/chave_expira_em = \"$EXP\"/" data/config.toml

say "4/7 Frase-senha dos backups"
BP=$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 28)
put HEALTHMON_BACKUP_PASSPHRASE "$BP"
echo "Anote AGORA esta frase-senha em um gerenciador de senhas. Sem ela os backups não podem ser lidos:"
echo "    $BP"; unset BP
read -r -p "Pressione Enter depois de anotar. " _

say "5/7 Senha de acesso ao dashboard"
$C run --rm healthmon healthmon senha-dashboard

say "6/7 Validando a integração real com a Garmin (mostra só nomes de campos, sem valores)…"
if $C run --rm healthmon healthmon validar-garmin | tee /tmp/validacao-garmin.txt; then
  echo "Validação concluída. Guarde /tmp/validacao-garmin.txt: ele serve para ajustar o leitor de dados se algo faltar."
else
  echo "A validação FALHOU. O serviço será iniciado, mas não considere o sistema pronto até resolver isto."
fi
$C run --rm healthmon healthmon alimentos baixar-taco || echo "Tabela TACO não baixada agora (pode repetir depois)."
$C run --rm healthmon healthmon kb verificar >/dev/null || echo "Verificação das referências não concluída (pode repetir depois)."

say "7/7 Iniciando o serviço e publicando só dentro da sua rede Tailscale…"
sudo sed -i 's/^https = false/https = true/' data/config.toml
$C up -d
if command -v tailscale >/dev/null && tailscale status >/dev/null 2>&1; then
  sudo tailscale serve --bg 8765
  URL="https://$(tailscale status --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
  sudo sed -i "s|^url_dashboard = .*|url_dashboard = \"$URL\"|" data/config.toml
  $C restart healthmon
  echo "Dashboard: $URL  (abra no Safari do iPhone com o Tailscale ligado e adicione à Tela de Início)"
else
  echo "Tailscale ainda não conectado: rode 'sudo tailscale up --ssh' e depois repita este passo."
fi
echo
echo "Importe o histórico quando quiser:  $C run --rm healthmon healthmon historico --dias 90"
