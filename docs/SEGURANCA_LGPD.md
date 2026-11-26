# Segurança, privacidade e LGPD

Dados de saúde são **dados pessoais sensíveis** (LGPD, art. 5º, II, e art. 11). O sistema foi desenhado
para que eles fiquem no seu computador.

## O que fica onde
| Item | Local | Proteção |
|---|---|---|
| Banco de dados (SQLite) | `~/.healthmon/healthmon.db` | Permissão 600, `secure_delete` ligado, diretório 700 |
| Tokens Garmin | `~/.healthmon/tokens/` | Permissão 600 (gravação atômica pela biblioteca) |
| Senha Garmin | **Não é armazenada** | Digitada uma vez, descartada da memória após o login |
| Senha do dashboard | `dashboard_auth.json` | Só o hash scrypt (N=2^15) |
| Segredos (Telegram, SMTP, backup, API) | `secrets.env` | Permissão 600, verificada a cada leitura (o sistema se recusa a rodar se estiver acessível a outros usuários) |
| Backups | `~/.healthmon/backups/*.hmbk` | Criptografados (Fernet: AES-128-CBC + HMAC-SHA256; chave derivada da frase-senha por scrypt) |
| Relatórios em Markdown | `~/.healthmon/relatorios/` | Permissão 600 |

Nada disso vai para o Git (`.gitignore`).

## Fluxos de dados para fora do computador
| Fluxo | Quando | Conteúdo |
|---|---|---|
| Garmin Connect | Sempre (coleta) | Requisições de leitura dos **seus** dados à Garmin, que já os possui |
| Telegram / e-mail | Só se você ativar | Nível **mínimo** (padrão): só "há um relatório/alerta", sem números de saúde. Nível **completo**: resumo do relatório (passa pelos servidores do Telegram ou do e-mail) |
| IA em nuvem (Anthropic) | Só com consentimento explícito | JSON agregado do relatório (veja abaixo) |
| Chart.js, fontes, CDNs | **Nunca** | Tudo é servido localmente; a CSP bloqueia recursos externos |

### IA em nuvem: consentimento
1. `healthmon ia-nuvem previa` mostra **exatamente** o JSON que seria enviado: datas, valores e médias
   dos indicadores, comparações com a base, tendências e sinais. Não vão nome, e-mail, tokens, IDs de
   dispositivo ou atividade, GPS nem JSON bruto (há também um filtro que remove esses campos).
2. `healthmon ia-nuvem consentir` exige digitar `AUTORIZO` e registra data, provedor e escopo.
3. Ative com `[ia] modo = "nuvem"`. Sem o consentimento gravado, nada é enviado, mesmo com o modo configurado.
4. `healthmon ia-nuvem revogar` desliga imediatamente.

O padrão é `modo = "regras"`. A alternativa sem envio é `modo = "local"`, com Ollama na sua máquina.

## Dashboard
- Autenticação por senha (scrypt), sessão aleatória de 256 bits guardada como hash, cookie `HttpOnly` +
  `SameSite=Strict` (+ `Secure` com HTTPS).
- Bloqueio de 15 min após 5 tentativas erradas por IP.
- Proteção CSRF (cabeçalho obrigatório em requisições de escrita) e CSP restritiva (`default-src 'self'`,
  `frame-ancestors 'none'`), além de `nosniff`, `no-referrer` e HSTS quando em HTTPS.
- Respostas da API com `Cache-Control: no-store`; o service worker do app nunca guarda dados.
- Escuta só em `127.0.0.1`. O acesso pelo iPhone é via **Tailscale** (VPN WireGuard, gratuita para uso
  pessoal): a porta nunca fica exposta à internet. Veja `OPERACAO.md`.

## Telemetria
- O healthmon não tem telemetria nem analytics.
- Uvicorn roda sem cabeçalho `Server`; a documentação automática do FastAPI (/docs) está desligada.
- Ollama não envia telemetria. No Tailscale, é possível iniciar o `tailscaled` com `--no-logs-no-support`
  para desligar o envio de logs (o suporte da Tailscale perde acesso a eles).
- O app Garmin Connect no iPhone tem as próprias configurações de privacidade: revise em
  Configurações > Privacidade no app.

## Princípios da LGPD aplicados
- **Finalidade e necessidade:** só são coletados indicadores de saúde, sono e atividade. Não há GPS,
  contatos, nome nem dados de terceiros.
- **Livre acesso e qualidade:** tudo pode ser visto no dashboard, e o JSON bruto fica guardado para auditoria.
- **Segurança e prevenção:** criptografia em backups, permissões restritas, sem exposição pública.
- **Transparência e consentimento:** transferência a terceiros (IA em nuvem) só com consentimento
  específico, informado e revogável.
- **Eliminação:** apagar `~/.healthmon` (e os backups) remove todos os dados. Para revogar o acesso à
  Garmin, apague `~/.healthmon/tokens` e altere a senha da conta Garmin.
