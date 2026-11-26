# Operação: instalação, comandos, agenda e recuperação de falhas

Este documento descreve os comandos de `healthmon --help` na versão atual, a agenda de tarefas definida em
`healthmon/service.py` e os valores padrão de `healthmon/config.example.toml`. Para implantar na VM gratuita,
com a API oficial da Garmin, siga `IMPLANTACAO.md`. Não há execução real de instalação registrada aqui: os
passos abaixo são referência e estão **pendentes de execução pelo usuário** no seu ambiente.

## Onde rodar
O sistema precisa de uma máquina **ligada continuamente** para coletar os dados e gerar os relatórios nos
horários definidos. O iPhone **não** roda o sistema: ele acessa o painel e recebe notificações.

## Pré-requisito na pulseira
1. No app **Garmin Connect**, ative **Pulse Ox durante o sono** e deixe a sincronização em segundo plano ligada.
2. A pulseira sincroniza com o app, o app envia à nuvem Garmin, e o healthmon busca os dados de lá.
   Se o relatório das 08h indicar que o sono não sincronizou, abra o Garmin Connect com a pulseira por perto.

## Chave pessoal da Garmin (fonte oficial, padrão)
A integração padrão usa a **Garmin Connect Personal API**, com uma chave pessoal de leitura. Não há login com
e-mail e senha nesse caminho.

```bash
.venv/bin/healthmon trocar-chave-garmin     # pede a chave (digitação oculta) e a data de expiração
.venv/bin/healthmon validar-garmin          # autenticação, renovação, estrutura, unidades, fuso e histórico (sem valores)
```

O comando `healthmon login` só funciona com `[garmin] fonte = "nao_oficial"`, que está desativada por padrão.
Com a fonte oficial, ele recusa a execução e indica a chave.

Depois de trocar a chave, reinicie o serviço e rode `validar-garmin`. Um alerta avisa 14 e 3 dias antes do
vencimento da chave, e de novo quando ela expira.

## Instalação nativa (macOS / Linux / Raspberry Pi)
`healthmon init` cria `$HEALTHMON_HOME` (padrão `~/.healthmon`) com `config.toml`, copiado de
`healthmon/config.example.toml`, e `secrets.env` com permissão 600. Segredos ficam só no `secrets.env`.
```bash
git clone <este repositório> healthmon && cd healthmon
./deploy/instalar.sh                    # cria .venv, instala e roda "healthmon init"
.venv/bin/healthmon definir-segredo OCI_PAR_URL   # só se for usar a cópia externa (digitação oculta)
.venv/bin/healthmon trocar-chave-garmin
.venv/bin/healthmon validar-garmin
.venv/bin/healthmon senha-dashboard     # mínimo de 10 caracteres
.venv/bin/healthmon historico --dias 90 # importa o histórico (leva alguns minutos)
.venv/bin/healthmon disponibilidade     # mostra o que o seu dispositivo está fornecendo
.venv/bin/healthmon chaves-recuperacao  # gera a chave de recuperação do backup (ver abaixo)
```

Depois instale o serviço permanente:
- **Linux / Raspberry Pi:** edite `deploy/healthmon.service` (usuário e caminhos), copie para
  `/etc/systemd/system/` e rode `sudo systemctl enable --now healthmon`.
- **macOS:** edite `deploy/com.healthmon.plist`, copie para `~/Library/LaunchAgents/` e rode `launchctl load`.
- **Windows:** use `deploy\instalar-windows.ps1`.

O serviço permanente é `healthmon executar` (agendador e painel). `healthmon dashboard` sobe só o painel.

## Instalação com Docker
```bash
mkdir data && sudo chown 10001 data     # no Linux; no Docker Desktop (Mac/Win) não é necessário
docker compose run --rm healthmon healthmon init
# edite data/secrets.env e data/config.toml, se necessário
docker compose run --rm healthmon healthmon trocar-chave-garmin
docker compose run --rm healthmon healthmon validar-garmin
docker compose run --rm healthmon healthmon senha-dashboard
docker compose run --rm healthmon healthmon historico --dias 90
docker compose run --rm healthmon healthmon chaves-recuperacao
docker compose up -d
```

## Acesso pelo iPhone (seguro, gratuito): Tailscale
1. Instale o Tailscale no computador e no iPhone e entre na mesma conta.
2. No computador: `tailscale serve --bg 8765`. Isso publica o painel em
   `https://<nome-do-computador>.<sua-tailnet>.ts.net`, com HTTPS válido e acessível só pelos seus dispositivos.
3. No `config.toml`: `[dashboard] https = true` e `[notificacoes] url_dashboard = "https://…ts.net"`.
   Também é possível usar `healthmon definir-config dashboard https true` e
   `healthmon definir-config notificacoes url_dashboard "https://…ts.net"`.
4. No Safari do iPhone, abra o endereço e toque em **Compartilhar → Adicionar à Tela de Início**.

Não use `tailscale funnel` nem redirecionamento de portas no roteador: isso exporia o painel à internet.

## Notificações (Telegram ou e-mail)
Telegram:
1. Fale com o **@BotFather** no Telegram, use `/newbot` e copie o token.
2. Envie qualquer mensagem ao seu bot e descubra o chat id pelo campo `chat.id` em
   `https://api.telegram.org/bot<TOKEN>/getUpdates`.
3. Grave os dois valores com digitação oculta:
   `healthmon definir-segredo TELEGRAM_BOT_TOKEN` e `healthmon definir-segredo TELEGRAM_CHAT_ID`.
4. Em `config.toml`: `[notificacoes] canais = ["telegram"]`.
5. Teste: `healthmon testar-notificacao`. **Este teste não foi executado neste documento.**

E-mail: `canais = ["email"]`, com os dados SMTP em `config.toml` e `healthmon definir-segredo SMTP_PASSWORD`.

O nível padrão é `nivel_conteudo = "minimo"`: a mensagem só avisa que há relatório ou alerta, sem dados de saúde.
`"completo"` inclui o resumo, e nesse caso os dados passam pelos servidores do canal escolhido.

## Agenda automática (fuso `America/Sao_Paulo`, valores padrão)
| Tarefa | Quando | Observação |
|---|---|---|
| Sincronização | A cada 2 h, às 05:40, 07:40 … 23:40 | `[coleta] intervalo_min` (padrão 120) |
| Relatório diário | 08:00 | Se o sono da noite ainda não sincronizou, tenta de novo a cada 30 min, até 4 vezes; depois gera o relatório mesmo assim |
| Relatório noturno | 22:00 | |
| Relatório semanal | Domingo, 19:00 | |
| Relatório mensal | Dia 1, 09:00 | Referente ao mês anterior |
| Backup diário | 03:30 | Cria, verifica e envia (se houver cópia externa). Mantém 14 diários e 8 semanais |
| Reenvio do backup externo | 09:20, 15:20 e 21:20 | Só quando o último envio está pendente, com erro ou atrasado |
| Retenção | Segunda, 04:10 | Apaga JSON brutos com mais de 400 dias e histórico de execuções com mais de 90 dias |
| Envio de alertas pendentes | A cada 30 min | Fora do horário silencioso (22:30–07:00). Limite de 2 alertas comuns por dia. Um mesmo tipo de alerta não se repete antes de 72 h |

Ao reiniciar, o serviço gera os relatórios diário e noturno cujo horário já passou e não foram gerados. Tarefas
que ficaram "executando" por queda do processo são marcadas como "interrompida".

Os alertas operacionais (falha de sincronização, chave da Garmin e falhas de backup) não entram no limite
diário de notificações.

## Alertas de backup
| Alerta (título) | Quando é gerado | O que fazer |
|---|---|---|
| "Backup diário falhou" | O backup local de hoje não foi criado ou não passou na verificação | Rode `healthmon backup`, depois `healthmon verificar-backup`. Veja o log |
| "Backup externo não configurado" | Não existe o segredo `OCI_PAR_URL` | Configure a cópia externa (`RECUPERACAO_DESASTRES.md`) ou aceite o risco de manter só a cópia local |
| "Backup externo com falha ou atrasado" | Duas falhas seguidas, ou o último envio bem-sucedido tem mais de 36 h | `healthmon backup-externo --status`; depois `healthmon backup-externo` para reenviar |

## Comandos
Lista conferida com `healthmon --help`. Todos os subcomandos abaixo existem nessa versão.

**Dados e coleta**
| Comando | Para quê |
|---|---|
| `healthmon status` | Período no banco, última sincronização, modo de IA e canais de notificação |
| `healthmon sync [--dias N]` | Sincroniza agora |
| `healthmon historico [--dias N]` | Importa histórico (padrão 90 dias). Pode ser repetido |
| `healthmon reprocessar` | Recalcula as métricas a partir dos JSON brutos (depois de atualizar o sistema) |
| `healthmon disponibilidade` | Indicadores que o seu dispositivo está fornecendo |
| `healthmon validar-garmin` | Valida a API oficial, sem imprimir valores de saúde |
| `healthmon conferir-garmin` | Mostra os valores de ontem para você comparar com o app Garmin Connect |
| `healthmon trocar-chave-garmin` | Troca a chave pessoal (digitação oculta) e a data de expiração |
| `healthmon login` | Só para `fonte = "nao_oficial"` |

**Relatórios, painel e notificações**
| Comando | Para quê |
|---|---|
| `healthmon relatorio {diario,noturno,semanal,mensal} [--data AAAA-MM-DD] [--notificar]` | Gera um relatório agora |
| `healthmon dashboard [--host H] [--porta P]` | Sobe só o painel |
| `healthmon executar` | Serviço completo: agendador e painel (uso em produção) |
| `healthmon senha-dashboard` | Define a senha de acesso ao painel |
| `healthmon testar-notificacao` | Envia mensagem de teste pelos canais configurados |
| `healthmon plano` | Mostra o plano do dia |

**Backup e recuperação**
| Comando | Para quê |
|---|---|
| `healthmon backup [--destino DIR]` | Cria um backup criptografado (`.hmbk`) |
| `healthmon verificar-backup [ARQUIVO] [--com-chave]` | Confere a integridade do backup (o mais recente, se omitido). Com `--com-chave`, pede a chave de recuperação e decifra para conferir o banco |
| `healthmon chaves-recuperacao [--forcar]` | Gera o par de chaves do backup. A chave de recuperação (`HMRK-…`) aparece uma única vez |
| `healthmon backup-externo [--status] [--contar]` | Envia o último backup para a cópia externa, ou mostra o estado (`--status`), ou conta os backups externos (`--contar`) |
| `healthmon restaurar [ARQUIVO]` | Restaura um `.hmbk`. Sem arquivo, lista os backups da cópia externa e baixa o escolhido |
| `healthmon exportar DESTINO` | Exporta todos os seus dados em JSON (portabilidade) |
| `healthmon apagar-dados --confirmar` | Apaga definitivamente banco, tokens, relatórios, exames e backups |

**Configuração e segredos**
| Comando | Para quê |
|---|---|
| `healthmon init` | Cria `$HEALTHMON_HOME` com `config.toml` e `secrets.env` |
| `healthmon definir-segredo NOME` | Grava um segredo com digitação oculta. Nomes aceitos: `OCI_PAR_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SMTP_PASSWORD` |
| `healthmon definir-config SECAO CHAVE VALOR` | Ajusta só `dashboard https`, `notificacoes url_dashboard` e `garmin chave_expira_em`. Outros valores: edite `config.toml` |
| `healthmon ia-nuvem {previa,consentir,revogar,status}` | Consentimento para IA em nuvem. `previa` mostra o que sairia |

**Base de conhecimento, alimentos, exames e demonstração**
| Comando | Para quê |
|---|---|
| `healthmon kb {verificar,buscar TERMO,listar}` | `verificar` consulta o Crossref pelo DOI (só o DOI é enviado) e grava `kb_verificacao.json`. `buscar` faz busca local. `listar` mostra as referências |
| `healthmon alimentos {baixar-taco,importar ARQUIVO [--fonte NOME] [--url URL],contar}` | Base de alimentos |
| `healthmon exames ARQUIVO` | Importa exames de um CSV (`data,analito,valor,unidade,ref_min,ref_max,laboratorio`) |
| `healthmon demo [--dias N]` | Popula um banco com dados **simulados**. Use um `HEALTHMON_HOME` separado |

## Recuperação de falhas
| Sintoma | Causa provável | O que fazer |
|---|---|---|
| Alerta de chave da Garmin | Chave perto do vencimento ou expirada | `healthmon trocar-chave-garmin`, reiniciar o serviço e `healthmon validar-garmin` |
| "Dados do Garmin não estão sincronizando" | Sem internet, Garmin fora do ar ou mudança na API | Veja `healthmon status` e o log em `~/.healthmon/healthmon.log`. Se a API mudou, atualize o sistema e rode `healthmon reprocessar` |
| Erro 429 / "Too many requests" | Limite da Garmin | Aguarde algumas horas. Aumente `[coleta] pausa_entre_chamadas_s` |
| Relatório diário com "sono não sincronizado" | A pulseira não enviou ao celular | Abra o Garmin Connect no iPhone com a pulseira por perto |
| Indicador sempre "indisponível" | Recurso desligado ou não fornecido pelo dispositivo | Pulse Ox: ative no app. Outros casos: veja `PESQUISA_INTEGRACOES.md` |
| Esqueci a senha do painel | — | `healthmon senha-dashboard` |
| Alerta de backup (ver tabela acima) | Falha local ou externa | Siga a tabela de alertas de backup |
| Banco corrompido ou troca de computador | — | Pare o serviço (`docker compose stop healthmon`, ou o equivalente nativo). Instale o sistema, copie o `.hmbk` e rode `healthmon restaurar ARQUIVO.hmbk`. O comando pede a chave de recuperação (`HMRK-…`) |
| Perdi a chave de recuperação | — | Os backups já criados **não podem ser abertos por ninguém**, nem pelo servidor. Gere um novo par com `healthmon chaves-recuperacao --forcar`. Os backups antigos continuam exigindo a chave antiga |

A chave de recuperação deve ficar no gerenciador de senhas ou em papel. Ela não é gravada no servidor e não
deve ser enviada por conversa, e-mail ou repositório. Para cópia fora de casa, o próprio arquivo `.hmbk` já
vem cifrado: copie-o para um pendrive ou para a nuvem que preferir, além do bucket externo.

O ensaio de restauração em VM nova não foi executado. O procedimento está em `RECUPERACAO_DESASTRES.md`.

## Atualização
```bash
git pull && .venv/bin/pip install -e . && .venv/bin/healthmon reprocessar
sudo systemctl restart healthmon      # ou: docker compose build && docker compose up -d
```
