# Implantação pelo iPhone, validação real e recuperação de falhas

**Estado em 26/11/2026:** os scripts foram escritos e tiveram a sintaxe verificada; o sistema foi executado no
ambiente de desenvolvimento apenas com dados sintéticos. **Nada foi executado em uma VM da Oracle, em um bucket
real, contra a API real da Garmin ou em um iPhone real.** Cada etapa abaixo que depende de você está marcada
com 🔶. O sistema só pode ser considerado operacional depois da lista da seção 8.

Tudo é feito pelo Safari do iPhone. Leia antes `INFRAESTRUTURA.md` (riscos da Oracle) e a seção
"Antes de usar dados reais" de `SEGURANCA_LGPD.md`.

Regra geral: **segredos nunca passam pelo chat.** A chave da Garmin, a URL do bucket e as senhas são digitadas
somente no terminal do servidor, com a tela oculta. A chave de recuperação aparece uma vez no terminal e vai
direto para o app Senhas do iPhone.

## 1. 🔶 Contas (cerca de 20 minutos)
1. **Oracle Cloud** (cloud.oracle.com → *Start for free*). Região de origem: **Brazil East (São Paulo)** ou
   Brazil Southeast (Vinhedo). A região de origem não pode ser trocada depois, e os recursos gratuitos só
   existem nela. O cadastro pede cartão para verificação de identidade. **Não converta a conta para
   Pay As You Go** e não crie nada além do descrito aqui.
2. **Tailscale** (tailscale.com, plano Personal gratuito). Instale o app Tailscale no iPhone e entre.

## 2. 🔶 Criar a máquina virtual
No console da Oracle: *Compute → Instances → Create instance*.
- *Image*: **Ubuntu 24.04**.
- *Shape*: **VM.Standard.E2.1.Micro** (AMD, rótulo *Always Free-eligible*). Alternativa: VM.Standard.A1.Flex
  (Arm) com 1 OCPU e 6 GB, também dentro do limite gratuito. Use a que o console oferecer com o rótulo.
- Se aparecer *Out of host capacity*: é falta temporária de máquinas gratuitas na região. Tente o outro
  formato (AMD ↔ Arm), outro *availability domain* ou repita mais tarde. Não aceite a sugestão de upgrade.
- *Advanced options → Management → Initialization script*: cole todo o conteúdo de
  `deploy/oracle/cloud-init.yaml` (abra o arquivo no GitHub pelo Safari, toque em *Raw*, copie tudo).
- Salve a chave SSH oferecida no app Arquivos (acesso de emergência).

Aguarde uns 8 minutos: o script instala Docker, Tailscale e atualizações automáticas de segurança.

## 3. 🔶 Rede privada
1. No console da Oracle, abra o **Cloud Shell** (ícone `>_`) e conecte na VM:
   ```bash
   ssh -i <chave-salva> ubuntu@<IP público da instância>
   ```
2. Na VM:
   ```bash
   cloud-init status --wait
   sudo tailscale up --ssh
   ```
   Abra o link exibido e autorize a máquina na sua conta Tailscale.
3. A partir daqui use o terminal do painel da Tailscale: login.tailscale.com → *Machines* → a VM → *SSH*.
   Funciona no Safari do iPhone, com o app Tailscale ligado.

## 4. 🔶 Obter o código
No terminal da VM:
```bash
sudo -iu healthmon
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 && cat ~/.ssh/id_ed25519.pub
```
Copie a linha exibida e adicione em GitHub → repositório → *Settings → Deploy keys → Add deploy key*
(somente leitura, **sem** marcar *Allow write access*). Depois:
```bash
git clone git@github.com:gabrielbassidemelo/UI-UX.git healthmon
cd healthmon
git checkout claude/garmin-cirqa-health-monitoring-2mpc9g
```

## 5. 🔶 Bucket para a cópia externa dos backups
Siga `RECUPERACAO_DESASTRES.md`, passos 2 e 3: criar o bucket privado (com versionamento e regra de ciclo de
vida) e a URL pré-autenticada (PAR) com permissão de escrita e listagem. Deixe a URL copiada; ela será colada
no terminal no próximo passo.

## 6. 🔶 Configuração guiada
```bash
./deploy/oracle/configurar.sh
```
O script faz, nesta ordem, e pode ser repetido sem perder o que já foi feito:

| Etapa | O que acontece | O que você faz |
|---|---|---|
| 1–2 | Constrói a imagem e cria a estrutura de dados | Nada |
| 3 | Senha do painel | Digita uma senha de 10+ caracteres |
| 4 | Gera a chave de recuperação dos backups | **Guarda a chave `HMRK-…` no app Senhas.** Ela não fica no servidor |
| 5 | Grava a URL do bucket e testa backup → envio → conferência com o banco vazio | Cola a URL (oculta) |
| 6 | Verificações de segurança (permissões, painel só local, sem root, IA externa desligada, Tailscale ligado, sem exposição pública) | Nada; se algo falhar, o script para |
| 7 | Mostra para onde os dados vão e pede autorização | Digita `AUTORIZO` (ou encerra) |
| 8 | Chave pessoal da Garmin | Gera em connect.garmin.com/app/settings/personalApiToken e cola (oculta) |
| 9 | Validação real da API da Garmin | Nada |
| 10 | Inicia o serviço e publica o painel só na sua rede Tailscale, com HTTPS | Abre o endereço no Safari |

Sem backup externo funcionando o script **não pede** a chave da Garmin.

## 7. 🔶 Depois da configuração
1. **Painel no iPhone:** com o Tailscale ligado, abra o endereço mostrado, entre com a senha, toque em
   Compartilhar → *Adicionar à Tela de Início*. Abra pelo ícone e, em Configurações, ative as notificações.
2. **Teste de notificação:** em Configurações → *Enviar notificação de teste*. Bloqueie a tela e confirme que
   chega. (No terminal: `docker compose run --rm healthmon healthmon testar-notificacao`.)
3. **Perfil e triagem de saúde:** responda todas as perguntas. Sem elas o sistema só descreve os dados.
4. **Histórico:** `docker compose run --rm healthmon healthmon historico --dias 365`
   (pode ser repetido; continua de onde parou se o teto diário de requisições for atingido).
5. **O que a CIRQA realmente entrega:** `docker compose run --rm healthmon healthmon disponibilidade`.
6. **Conferência com o app Garmin Connect:** em Configurações → *Conferência com o app*, compare os valores de
   ontem com os do app e marque igual/diferente em cada indicador.
7. **Ensaio de recuperação** (obrigatório antes de confiar no backup): `RECUPERACAO_DESASTRES.md`,
   seção "Recuperação completa em VM nova". O ensaio rápido, que não toca no banco em uso:
   ```bash
   docker compose run --rm -v "$PWD/deploy:/deploy:ro" healthmon bash /deploy/oracle/ensaio-recuperacao.sh
   ```
8. **Cópia independente da Oracle:** em Configurações → Backup → *Baixar backup cifrado*, salve no app
   Arquivos (iCloud) uma vez por semana. O arquivo só abre com a chave de recuperação.
9. **Fechar o SSH público:** `./deploy/oracle/fechar-ssh-publico.sh` e, no console da Oracle, remova a regra da
   porta 22 em *Networking → Virtual cloud networks → Security Lists*. A VM fica sem porta aberta à internet.

## 8. Critérios de "pronto" (todos dependem de execução real)
- [ ] 🔶 VM criada com rótulo Always Free; conta **não** convertida para Pay As You Go.
- [ ] 🔶 `configurar.sh` terminou as 10 etapas sem falha.
- [ ] 🔶 `validar-garmin` aprovado: autenticação, renovação do token, estrutura, unidades, fuso e histórico.
- [ ] 🔶 Conferência com o app Garmin Connect feita por você, indicador por indicador.
- [ ] 🔶 Backup diário criado, enviado e conferido por 3 dias seguidos (Configurações → Backup).
- [ ] 🔶 Restauração provada: ensaio rápido e, de preferência, recuperação completa em uma VM nova.
- [ ] 🔶 Painel aberto no iPhone pela rede privada; notificação recebida com a tela bloqueada.
- [ ] 🔶 Relatórios das 08h e das 22h gerados sozinhos por 3 dias, sem intervenção.
- [ ] 🔶 Fatura da Oracle em R$ 0 no fim do primeiro mês (*Billing → Cost analysis*).

## Renovação da chave da Garmin
O sistema avisa 14 e 3 dias antes da data informada (a Garmin também envia e-mail). Gere a nova chave e, na VM:
```bash
cd ~/healthmon
read -r -s -p "Nova chave: " K; echo
sudo sed -i "s|^GARMIN_PERSONAL_API_KEY=.*|GARMIN_PERSONAL_API_KEY=$K|" data/secrets.env; unset K
sudo sed -i 's/^chave_expira_em = .*/chave_expira_em = "AAAA-MM-DD"/' data/config.toml
docker compose restart healthmon
```
Troque `AAAA-MM-DD` pela nova data antes de colar o terceiro comando.

## Atualização do sistema
```bash
cd ~/healthmon && git pull && docker compose build && docker compose up -d
```

## Recuperação de falhas
| Situação | O que acontece sozinho | O que fazer |
|---|---|---|
| VM reinicia | O contêiner volta; tarefas interrompidas são marcadas e relatórios atrasados do dia são gerados | Nada |
| Garmin fora do ar ou erro 5xx | Novas tentativas com espera crescente; a próxima coleta repete | Nada |
| Limite de requisições (429) | A coleta para e só volta após 60 minutos | Se repetir, aumente `[coleta] intervalo_min` |
| Chave inválida ou expirada | Alerta "Acesso à Garmin recusado" | Renovar a chave (acima) |
| Sono não sincronizou às 08h | 4 novas tentativas a cada 30 minutos; depois o relatório sai com aviso | Abrir o Garmin Connect com a pulseira por perto |
| Formato da resposta mudou | O endpoint é marcado com erro, o resto continua; alerta | `validar-garmin` e ajuste do leitor |
| Backup local falhou | Alerta "Backup diário falhou" | Ver Configurações → Histórico de execuções |
| Envio externo falhou | Novas tentativas às 9h20, 15h20 e 21h20; alerta após 2 falhas ou 36 h sem envio | Conferir a validade da URL do bucket (`RECUPERACAO_DESASTRES.md`, passo 4) |
| Banco corrompido | — | `docker compose run --rm healthmon healthmon restaurar` (baixa o último backup externo e pede a chave) |
| VM perdida ou recuperada pela Oracle | — | `RECUPERACAO_DESASTRES.md`, "Recuperação completa em VM nova" |
| Esqueci a senha do painel | — | `docker compose run --rm healthmon healthmon senha-dashboard` |
| Perdi a chave de recuperação | Os backups antigos ficam ilegíveis | Com o servidor ainda de pé: `healthmon chaves-recuperacao --forcar` e guarde a nova chave |

## Exportar, apagar e desativar tudo
- Exportar tudo em JSON: `docker compose run --rm healthmon healthmon exportar /data/exportacao.json`
- Apagar os dados do servidor: `docker compose run --rm healthmon healthmon apagar-dados --confirmar`
- Desativação completa, na ordem:
  1. Revogar a chave em connect.garmin.com/app/settings/personalApiToken.
  2. `docker compose down` e `apagar-dados --confirmar`.
  3. No console da Oracle: apagar os objetos e o bucket (as versões antigas também), depois *Terminate* na
     instância marcando a exclusão do volume de inicialização.
  4. No painel da Tailscale: remover a máquina.
  5. No iPhone: apagar o ícone do painel, as cópias de backup no app Arquivos e a chave no app Senhas.
  6. No GitHub: remover a *deploy key*.
