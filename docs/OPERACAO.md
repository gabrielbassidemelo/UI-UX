# Instalação, operação e recuperação de falhas

## Onde rodar
O sistema precisa de uma máquina **ligada continuamente** para coletar os dados e gerar os relatórios nos
horários definidos. Opções sem assinatura:
- Um Mac ou PC que fique ligado (desative a suspensão automática).
- Um Raspberry Pi 4/5 (baixo consumo; ideal para ficar 24 h ligado).
- Um mini-PC ou NAS com Docker.

O iPhone **não** roda o sistema: ele acessa o dashboard e recebe notificações.

## Pré-requisito no iPhone
1. No app **Garmin Connect**: ative **Pulse Ox durante o sono** (vem desligado na CIRQA) e deixe a
   sincronização em segundo plano ligada.
2. A pulseira sincroniza com o app, o app envia à nuvem Garmin, e o healthmon busca os dados de lá.
   Se o relatório das 08h indicar que o sono não sincronizou, abra o Garmin Connect com a pulseira por perto.

## Instalação nativa (macOS / Linux / Raspberry Pi)
```bash
git clone <este repositório> healthmon && cd healthmon
./deploy/instalar.sh                    # cria .venv, instala e roda "healthmon init"
nano ~/.healthmon/secrets.env           # HEALTHMON_BACKUP_PASSPHRASE (obrigatória, ≥12 caracteres)
.venv/bin/healthmon login               # e-mail, senha e MFA Garmin — uma única vez
.venv/bin/healthmon senha-dashboard
.venv/bin/healthmon historico --dias 90 # importa o histórico (leva alguns minutos)
.venv/bin/healthmon disponibilidade     # mostra o que a CIRQA está fornecendo
```
Depois instale o serviço permanente:
- **Linux / Raspberry Pi:** edite `deploy/healthmon.service` (usuário/caminhos), copie para
  `/etc/systemd/system/`, depois `sudo systemctl enable --now healthmon`.
- **macOS:** edite `deploy/com.healthmon.plist`, copie para `~/Library/LaunchAgents/`, depois `launchctl load …`.
- **Windows:** `py -m venv .venv`, `.venv\Scripts\pip install -e .`, os mesmos comandos acima, e por fim
  `deploy\instalar-windows.ps1`.

## Instalação com Docker
```bash
mkdir data && sudo chown 10001 data     # no Linux; no Docker Desktop (Mac/Win) não é necessário
docker compose run --rm healthmon healthmon init
# edite data/secrets.env
docker compose run --rm healthmon healthmon login
docker compose run --rm healthmon healthmon senha-dashboard
docker compose run --rm healthmon healthmon historico --dias 90
docker compose up -d
```

## Acesso pelo iPhone (seguro, gratuito): Tailscale
1. Instale o Tailscale no computador e no iPhone e entre na mesma conta.
2. No computador: `tailscale serve --bg 8765`. Isso publica o dashboard em
   `https://<nome-do-computador>.<sua-tailnet>.ts.net`, com HTTPS válido e acessível **só** pelos seus
   dispositivos.
3. No `config.toml`: `[dashboard] https = true` e `[notificacoes] url_dashboard = "https://…ts.net"`.
4. No Safari do iPhone, abra o endereço, toque em **Compartilhar → Adicionar à Tela de Início**. O
   dashboard passa a abrir como um app, com o modo escuro do iOS.

Não use `tailscale funnel` nem redirecionamento de portas no roteador: isso exporia o dashboard à internet.

## Notificações no iPhone (Telegram: gratuito e com push)
1. No Telegram, fale com **@BotFather** → `/newbot` e copie o token.
2. Envie qualquer mensagem ao seu bot e obtenha seu chat id em
   `https://api.telegram.org/bot<TOKEN>/getUpdates` (campo `chat.id`).
3. Em `secrets.env`: `TELEGRAM_BOT_TOKEN=…` e `TELEGRAM_CHAT_ID=…`.
4. Em `config.toml`: `[notificacoes] canais = ["telegram"]`.
5. Teste: `healthmon testar-notificacao`.

O nível padrão é `"minimo"`: as mensagens só avisam que existe relatório ou alerta, sem dados de saúde.
`"completo"` inclui o resumo (os dados passam pelos servidores do Telegram). E-mail funciona da mesma forma
(`canais = ["email"]` + dados SMTP; use senha de app).

## Agenda automática (fuso America/Sao_Paulo)
| Tarefa | Quando |
|---|---|
| Sincronização | A cada 2 h, das 05h40 às 23h40 |
| Relatório diário | 08:00. Se o sono não sincronizou, tenta de novo a cada 30 min (até 4×) e depois gera com o aviso de "dados ausentes" |
| Relatório noturno | 22:00 |
| Relatório semanal | Domingo, 19:00 |
| Relatório mensal | Dia 1, 09:00 (mês anterior) |
| Backup criptografado | 03:30 (mantém 14 diários + 8 semanais) |
| Envio de alertas pendentes | A cada 30 min, fora do horário silencioso (22h30–07h00), no máximo 2 por dia, cada tipo no máximo 1 a cada 72 h |

Tudo é configurável em `~/.healthmon/config.toml`.

## IA local opcional (Ollama)
```bash
# instale o Ollama (ollama.com), depois:
ollama pull qwen2.5:7b-instruct      # ~5 GB; precisa de ~8 GB de RAM livre
```
Em `config.toml`: `[ia] modo = "local"`. Se o Ollama estiver indisponível, o relatório sai normalmente,
só com o motor de regras. Em um Raspberry Pi, prefira manter `modo = "regras"`.

## Comandos úteis
| Comando | Para quê |
|---|---|
| `healthmon status` | Período no banco, última sincronização, modo de IA |
| `healthmon sync` | Sincroniza agora |
| `healthmon relatorio diario` (`noturno`, `semanal`, `mensal`) | Gera um relatório na hora |
| `healthmon disponibilidade` | Indicadores efetivamente fornecidos |
| `healthmon reprocessar` | Recalcula as métricas a partir do JSON bruto (após atualizar o sistema) |
| `healthmon backup` / `healthmon restaurar ARQUIVO.hmbk` | Backup e restauração |

## Recuperação de falhas
| Sintoma | Causa provável | O que fazer |
|---|---|---|
| Alerta "Sessão com o Garmin expirou" | Tokens expirados ou revogados | `healthmon login` (no Docker: `docker compose run --rm healthmon healthmon login`) |
| "Dados do Garmin não estão sincronizando" | Sem internet, Garmin fora do ar, ou mudança na API | Veja `healthmon status` e `~/.healthmon/healthmon.log`. Se for mudança da Garmin: `pip install -U garminconnect`, depois `healthmon reprocessar` |
| Erro 429 / "Too many requests" | Limite da Garmin | Aguarde algumas horas; aumente `pausa_entre_chamadas_s` |
| Relatório diário com "sono não sincronizado" | A pulseira não enviou ao celular | Abra o Garmin Connect no iPhone com a pulseira por perto |
| Indicador sempre "indisponível" | Recurso desligado ou não suportado | Pulse Ox: ative no app. Outros: veja `PESQUISA_INTEGRACOES.md` |
| Esqueci a senha do dashboard | — | `healthmon senha-dashboard` |
| Banco corrompido / troca de computador | — | Instale, copie o `.hmbk` e rode `healthmon restaurar ARQUIVO.hmbk` com a mesma frase-senha |
| Perdi a frase-senha de backup | — | Os backups ficam irrecuperáveis (é o que garante a segurança). Defina uma nova; os backups seguintes usarão ela |

Guarde a frase-senha de backup num gerenciador de senhas. Para ter cópia fora de casa, copie os arquivos
`.hmbk` (já criptografados) para um pendrive ou nuvem de sua escolha.

## Atualização
```bash
git pull && .venv/bin/pip install -e . && .venv/bin/healthmon reprocessar
sudo systemctl restart healthmon      # ou: docker compose build && docker compose up -d
```
