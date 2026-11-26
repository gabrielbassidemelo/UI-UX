#!/usr/bin/env bash
# Configuração guiada do healthmon em uma VM (Oracle Always Free ou qualquer Linux com Docker).
# Execute como o usuário "healthmon", dentro do repositório clonado:   ./deploy/oracle/configurar.sh
#
# Ordem (fase 3): o backup externo e a chave de recuperação são configurados e TESTADOS com o banco
# ainda vazio. Só depois disso, e com a sua autorização explícita, a chave da Garmin é pedida e dados
# reais de saúde chegam à máquina. Segredos são digitados aqui, com eco desligado, e gravados apenas
# em data/secrets.env (permissão 600). Nada é enviado a conversas, código, logs ou GitHub.
# O script pode ser repetido: etapas já concluídas são puladas.
set -euo pipefail
cd "$(dirname "$0")/../.."
umask 077
C="docker compose"
RUN="$C run --rm healthmon healthmon"
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ask() { local a; read -r -p "$1 " a; printf '%s' "$a"; }

command -v docker >/dev/null || { echo "Docker não encontrado (aguarde o cloud-init terminar: cloud-init status --wait)."; exit 1; }
mkdir -p data && chmod 700 data
if [ "$(id -u)" != "10001" ]; then sudo chown -R 10001:10001 data 2>/dev/null || true; fi

say "1/10 Construindo a imagem ($(uname -m)); alguns minutos na primeira vez…"
$C build --quiet

say "2/10 Criando a estrutura de dados…"
$RUN init >/dev/null
SECRETS=data/secrets.env
put() { HM_VALUE="$2" sudo --preserve-env=HM_VALUE python3 -c '
import os,re,sys
p,n=sys.argv[1],sys.argv[2]; v=os.environ["HM_VALUE"]
ls=[l for l in open(p,encoding="utf-8").read().splitlines() if not re.match(rf"^{re.escape(n)}=",l)]
ls.append(f"{n}={v}"); open(p,"w",encoding="utf-8").write("\n".join(ls)+"\n")' "$SECRETS" "$1"; }
has() { sudo grep -q "^$1=..*" "$SECRETS"; }

say "3/10 Senha de acesso ao dashboard"
if sudo test -s data/dashboard_auth.json 2>/dev/null; then echo "Já definida."; else $RUN senha-dashboard; fi

say "4/10 Chave de recuperação dos backups"
if sudo test -s data/recovery_public.key; then
  echo "Já existe. (Os backups só abrem com a chave de recuperação que você guardou.)"
else
  echo "A chave aparece UMA vez. Guarde no app Senhas do iPhone antes de continuar."
  $RUN chaves-recuperacao
fi

say "5/10 Cópia externa dos backups (Oracle Object Storage)"
if has OCI_PAR_URL; then
  echo "URL de acesso já gravada."
else
  echo "Crie o bucket e a URL pré-autenticada conforme docs/RECUPERACAO_DESASTRES.md e cole a URL abaixo"
  echo "(não aparece na tela; ela é uma credencial)."
  read -r -s -p "URL pré-autenticada (PAR): " PAR; echo
  [ -n "$PAR" ] || { echo "Sem cópia externa não é permitido receber dados reais. Rode o script de novo quando tiver a URL."; exit 1; }
  put OCI_PAR_URL "$PAR"; unset PAR
fi
echo "Testando com o banco ainda vazio: backup → envio → conferência…"
$RUN backup >/dev/null
if ! $RUN backup-externo; then
  echo "O envio externo FALHOU. Corrija a URL (edite data/secrets.env) e rode o script de novo."
  echo "Nenhum dado real será solicitado enquanto o backup externo não funcionar."
  exit 1
fi
$RUN verificar-backup

say "6/10 Verificações de segurança"
FAIL=0
chk() { if eval "$2"; then echo "  ok    $1"; else echo "  FALHA $1"; FAIL=1; fi; }
chk "secrets.env com permissão 600"            '[ "$(sudo stat -c %a data/secrets.env)" = "600" ]'
chk "pasta data/ com permissão 700"            '[ "$(sudo stat -c %a data)" = "700" ]'
chk "painel publicado só em 127.0.0.1"         'grep -q "127.0.0.1:8765:8765" docker-compose.yml'
chk "contêiner sem root e somente leitura"     'grep -q "read_only: true" docker-compose.yml && grep -q "^USER" Dockerfile'
chk "IA externa desabilitada"                  'sudo grep -Eq "^modo = \"regras\"" data/config.toml'
chk "integração não oficial desabilitada"      'sudo grep -Eq "^fonte = \"oficial\"" data/config.toml'
chk "Tailscale conectado"                      'command -v tailscale >/dev/null && tailscale status >/dev/null 2>&1'
chk "Tailscale Funnel (exposição pública) desligado" '! (sudo tailscale funnel status 2>/dev/null | grep -q "Funnel on")'
[ "$FAIL" = 0 ] || { echo "Corrija as falhas acima e rode o script de novo (para o Tailscale: sudo tailscale up --ssh)."; exit 1; }

say "7/10 Autorização para receber dados reais de saúde"
cat <<'TXT'
A partir daqui a sua chave da Garmin será usada e dados reais chegarão a ESTA máquina.
  • Onde ficam: no arquivo data/healthmon.db desta VM (Oracle Cloud, na região da sua conta).
  • Quem tem acesso técnico: a Oracle (infraestrutura da VM e do bucket; no bucket só existe arquivo
    cifrado), a Tailscale (coordena a conexão; o tráfego é cifrado de ponta a ponta) e a Garmin (origem).
  • O que sai da VM: pedidos à API da Garmin; o backup JÁ CIFRADO para o bucket; avisos curtos de
    notificação para o seu iPhone. Nada vai para GitHub, serviços de IA ou terceiros.
  • Como desfazer: `healthmon apagar-dados`, revogar a chave no site da Garmin, apagar bucket e VM.
Detalhes: docs/SEGURANCA_LGPD.md (seção "Antes de usar dados reais").
TXT
[ "$(ask 'Digite AUTORIZO para continuar (qualquer outra coisa encerra sem pedir a chave):')" = "AUTORIZO" ] || { echo "Encerrado sem dados reais. Tudo o que foi feito até aqui continua válido."; exit 0; }

say "8/10 Chave pessoal da Garmin"
if has GARMIN_PERSONAL_API_KEY; then
  echo "Já gravada."
else
  echo "Gere em https://connect.garmin.com/app/settings/personalApiToken e cole abaixo (não aparece na tela)."
  read -r -s -p "Chave pessoal: " GK; echo
  [ -n "$GK" ] || { echo "Chave vazia."; exit 1; }
  put GARMIN_PERSONAL_API_KEY "$GK"; unset GK
  EXP=$(ask "Data de expiração que você escolheu para a chave (AAAA-MM-DD):")
  [[ "$EXP" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || { echo "Data inválida; use AAAA-MM-DD."; exit 1; }
  sudo sed -i "s/^chave_expira_em = .*/chave_expira_em = \"$EXP\"/" data/config.toml
fi

say "9/10 Validando a integração real com a Garmin (o relatório não contém valores de saúde)…"
if $RUN validar-garmin; then
  echo "Validação concluída. Relatório: data/validacao_garmin.json."
  echo "Falta a sua conferência com o app Garmin Connect: no painel, abra Configurações › Conferência com o app."
else
  echo "A validação FALHOU. O serviço será iniciado, mas o sistema NÃO deve ser considerado pronto até resolver isto."
fi
$RUN alimentos baixar-taco || echo "Tabela TACO não baixada agora (pode repetir depois)."

say "10/10 Iniciando o serviço e publicando só dentro da sua rede Tailscale…"
sudo sed -i 's/^https = false/https = true/' data/config.toml
$C up -d
sudo tailscale serve --bg 8765
URL="https://$(tailscale status --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
sudo sed -i "s|^url_dashboard = .*|url_dashboard = \"$URL\"|" data/config.toml
$C restart healthmon
echo "Painel: $URL  (abra no Safari do iPhone com o Tailscale ligado e adicione à Tela de Início)"
echo
echo "Próximos passos (docs/IMPLANTACAO.md):"
echo "  • importar o histórico:      $RUN historico --dias 365"
echo "  • ensaio de recuperação:     veja docs/RECUPERACAO_DESASTRES.md (faça antes de confiar no backup)"
echo "  • fechar o SSH público:      ./deploy/oracle/fechar-ssh-publico.sh"
