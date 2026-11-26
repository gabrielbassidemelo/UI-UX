#!/usr/bin/env bash
# Configuração guiada do healthmon em uma VM (Oracle Always Free ou qualquer Linux com Docker).
# Execute como o usuário padrão da VM (ubuntu), dentro do repositório clonado:
#     ./deploy/oracle/configurar.sh              primeira instalação
#     ./deploy/oracle/configurar.sh --recuperar  VM nova, restaurando o histórico da cópia externa
#
# Ordem: a chave de recuperação e a cópia externa são configuradas e TESTADAS com o banco ainda vazio.
# Só depois, e com a sua autorização explícita, a chave da Garmin é pedida e dados reais chegam à máquina.
# Todo segredo é digitado dentro do contêiner, com eco desligado e sem registro de saída (--log-driver none):
# não passa por linha de comando, histórico do shell, log do sudo, conversas, código ou GitHub.
# O script pode ser repetido: etapas já concluídas são puladas.
set -euo pipefail
cd "$(dirname "$0")/../.."
umask 077
MODO="${1:-instalar}"
C="docker compose"
RUN="$C run --rm healthmon healthmon"
# Comandos que mostram ou recebem segredos: contêiner avulso, interativo, sem log em disco.
SEC="docker run --rm -it --log-driver none --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges -v $PWD/data:/data healthmon:local healthmon"
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ask() { local a; read -r -p "$1 " a; printf '%s' "$a"; }
has() { sudo grep -q "^$1=..*" data/secrets.env; }

command -v docker >/dev/null || { echo "Docker não encontrado (aguarde o cloud-init: cloud-init status --wait)."; exit 1; }
docker info >/dev/null 2>&1 || { echo "Seu usuário ainda não está no grupo docker. Saia e entre de novo no terminal (ou rode: newgrp docker)."; exit 1; }
sudo -n true 2>/dev/null || { echo "Este script precisa de sudo sem senha (usuário padrão 'ubuntu' da imagem da Oracle)."; exit 1; }
sudo mkdir -p data && sudo chown 10001:10001 data && sudo chmod 700 data

say "1 Construindo a imagem ($(uname -m)); alguns minutos na primeira vez…"
$C build --quiet

say "2 Criando a estrutura de dados…"
$RUN init >/dev/null

say "3 Senha de acesso ao painel"
if sudo test -s data/dashboard_auth.json; then echo "Já definida."; else $SEC senha-dashboard; fi

if [ "$MODO" = "--recuperar" ]; then
  say "4 Recuperação: URL da cópia externa"
  has OCI_PAR_URL || $SEC definir-segredo OCI_PAR_URL
  say "5 Recuperação: escolher e restaurar o backup (pede a sua chave de recuperação)"
  echo "Nenhuma chave nova é gerada e nenhum backup é enviado antes de a restauração terminar."
  $SEC restaurar
else
  say "4 Chave de recuperação dos backups"
  if sudo test -s data/recovery_public.key; then
    echo "Já existe. (Os backups só abrem com a chave de recuperação que você guardou.)"
  else
    echo "A chave aparece UMA vez. Guarde no app Senhas do iPhone; em seguida o sistema pede que você a cole de volta para conferir."
    NOVA_CHAVE=1
  fi
  say "5 Cópia externa dos backups (Oracle Object Storage)"
  if ! has OCI_PAR_URL; then
    echo "Crie o bucket e a URL pré-autenticada conforme docs/RECUPERACAO_DESASTRES.md. A URL é uma credencial."
    $SEC definir-segredo OCI_PAR_URL
  fi
  N=$($RUN backup-externo --contar | tr -dc '0-9') || { echo "Não consegui listar o bucket. Confira a URL (precisa permitir listagem)."; exit 1; }
  if [ "${NOVA_CHAVE:-0}" = 1 ] && [ "${N:-0}" -gt 0 ]; then
    echo "O bucket JÁ CONTÉM $N backup(s) e esta máquina não tem chave de recuperação."
    echo "Se você está reconstruindo o servidor, use:  ./deploy/oracle/configurar.sh --recuperar"
    echo "Se quer mesmo começar do zero, crie um bucket novo e uma URL nova."
    exit 1
  fi
  [ "${NOVA_CHAVE:-0}" = 1 ] && $SEC chaves-recuperacao
  echo "Testando com o banco ainda vazio: backup → envio → conferência…"
  $RUN backup >/dev/null
  if ! $RUN backup-externo; then
    echo "O envio externo FALHOU. Corrija a URL ($SEC definir-segredo OCI_PAR_URL) e rode o script de novo."
    echo "Nenhum dado real será solicitado enquanto o backup externo não funcionar."
    exit 1
  fi
  echo "Agora a prova de que a SUA chave abre o backup (cole a chave guardada):"
  $SEC verificar-backup --com-chave || { echo "A chave não abriu o backup. Não prossiga: gere outra com 'chaves-recuperacao --forcar'."; exit 1; }
fi

say "6 Verificações de segurança"
FAIL=0
chk() { if eval "$2"; then echo "  ok    $1"; else echo "  FALHA $1"; FAIL=1; fi; }
chk "secrets.env com permissão 600"            '[ "$(sudo stat -c %a data/secrets.env)" = "600" ]'
chk "pasta data/ com permissão 700"            '[ "$(sudo stat -c %a data)" = "700" ]'
chk "painel publicado só em 127.0.0.1"         'grep -q "127.0.0.1:8765:8765" docker-compose.yml'
chk "contêiner roda sem root (uid 10001)"      '[ "$(docker run --rm --entrypoint id healthmon:local -u)" = "10001" ]'
chk "contêiner somente leitura"                'grep -q "read_only: true" docker-compose.yml'
chk "chave pública de recuperação presente"    'sudo test -s data/recovery_public.key'
chk "IA externa desabilitada"                  'sudo grep -Eq "^modo = \"regras\"" data/config.toml'
chk "integração não oficial desabilitada"      'sudo grep -Eq "^fonte = \"oficial\"" data/config.toml'
chk "Tailscale conectado"                      'command -v tailscale >/dev/null && tailscale status >/dev/null 2>&1'
chk "Tailscale Funnel (exposição pública) desligado" '! (sudo tailscale funnel status 2>/dev/null | grep -q "Funnel on")'
[ "$FAIL" = 0 ] || { echo "Corrija as falhas acima e rode o script de novo (para o Tailscale: sudo tailscale up --ssh)."; exit 1; }

say "7 Autorização para receber dados reais de saúde"
cat <<'TXT'
A partir daqui a sua chave da Garmin será usada e dados reais chegarão a ESTA máquina.
  • Onde ficam: no arquivo data/healthmon.db desta VM (Oracle Cloud, na região da sua conta).
  • Quem tem acesso técnico: a Oracle (infraestrutura da VM e do bucket; no bucket só existe arquivo
    cifrado), a Tailscale (coordena a conexão; o tráfego é cifrado de ponta a ponta) e a Garmin (origem).
  • O que sai da VM: pedidos à API da Garmin; o backup JÁ CIFRADO para o bucket; avisos curtos de
    notificação para o seu iPhone. Nenhum dado de saúde vai para GitHub, serviços de IA ou terceiros.
  • Como desfazer: `healthmon apagar-dados`, revogar a chave no site da Garmin, apagar bucket e VM.
Detalhes: docs/SEGURANCA_LGPD.md (seção "Antes de usar dados reais").
TXT
[ "$(ask 'Digite AUTORIZO para continuar (qualquer outra coisa encerra sem pedir a chave):')" = "AUTORIZO" ] || { echo "Encerrado sem dados reais novos. Tudo o que foi feito até aqui continua válido."; exit 0; }

say "8 Chave pessoal da Garmin"
if has GARMIN_PERSONAL_API_KEY; then
  echo "Já gravada."
else
  echo "Gere em https://connect.garmin.com/app/settings/personalApiToken e cole quando pedido."
  $SEC trocar-chave-garmin
fi

say "9 Validando a integração real com a Garmin (o relatório não contém valores de saúde)…"
if $RUN validar-garmin; then
  echo "Validação concluída. Relatório: data/validacao_garmin.json."
  echo "Falta a sua conferência com o app Garmin Connect: no painel, Configurações › Conferência com o app."
else
  echo "A validação FALHOU. O serviço será iniciado, mas o sistema NÃO deve ser considerado pronto até resolver isto."
fi
$RUN alimentos baixar-taco || echo "Tabela TACO não baixada agora (pode repetir depois)."

say "10 Iniciando o serviço e publicando só dentro da sua rede Tailscale…"
$RUN definir-config dashboard https true
$C up -d
sudo tailscale serve --bg 8765
URL="https://$(tailscale status --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
$RUN definir-config notificacoes url_dashboard "$URL"
$C restart healthmon
echo "Painel: $URL  (abra no Safari do iPhone com o Tailscale ligado e adicione à Tela de Início)"
echo
echo "Próximos passos (docs/IMPLANTACAO.md, seção 7): histórico, conferência com o app, ensaio de recuperação, fechar o SSH público."
