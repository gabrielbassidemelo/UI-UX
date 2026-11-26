# Relatório de segurança, privacidade e LGPD

> A seção 0 é a apresentação exigida **antes de usar dados reais** (fase 3). As seções 1 a 4 descrevem a fase 2
> e continuam válidas, exceto onde a seção 0 as atualiza (backups). O texto da fase 1 segue ao final.

## 0. Antes de usar dados reais (fase 3)
Leia esta seção antes de digitar `AUTORIZO` no script de configuração. Até esse ponto nenhum dado real de
saúde existe em lugar algum além da Garmin.

### Onde os dados ficam
| Dado | Onde | Protegido como |
|---|---|---|
| Banco com tudo (indicadores, perfil, triagem de saúde, refeições, exames, check-ins) | Arquivo `data/healthmon.db` no disco da sua VM na Oracle, na região da conta (São Paulo ou Vinhedo) | Disco cifrado pela Oracle com chaves dela; pasta 700; contêiner sem root; nenhuma porta pública |
| Backups diários | `data/backups/` na VM e cópia no seu bucket do Oracle Object Storage | **Cifrados antes de sair da VM** (formato HMBK2). Só abrem com a chave de recuperação, que **não está no servidor nem no bucket** |
| Cópia independente | App Arquivos/iCloud do iPhone, quando você baixa pelo painel | O mesmo arquivo cifrado |
| Chave de recuperação `HMRK-…` | Só com você (app Senhas). Não é gravada no servidor | Aparece uma vez no terminal; você a digita de novo (tela oculta, sem log) para conferi-la, nos ensaios e numa restauração |
| Chave da Garmin, URL do bucket, senha do painel (hash) | `data/secrets.env` e `data/dashboard_auth.json` na VM (600) | Não entram em backup, log, relatório ou repositório |

### Quem tem acesso técnico
| Provedor | O que consegue ver |
|---|---|
| **Garmin** | Tudo o que a pulseira envia (é a origem). O sistema só lê |
| **Oracle** | Opera a VM e o bucket. Com acesso de administrador à infraestrutura, o disco da VM é legível (cifragem com chaves da Oracle). No bucket só há arquivo cifrado com chave que a Oracle não tem |
| **Tailscale** | Não vê conteúdo (cifrado de ponta a ponta). Vê metadados: seus aparelhos, endereços e horários. O nome `.ts.net` do painel aparece em registros públicos de certificados |
| **Apple** | Entrega as notificações; vê que houve uma notificação e quando. O conteúdo é cifrado e genérico |
| **GitHub** | Só código. Nenhum dado de saúde, nenhum backup, nenhum segredo |
| **Anthropic / Claude** | Nada. IA externa desabilitada; nenhum dado real foi usado no desenvolvimento |

### O que sai da VM
1. Pedidos de leitura à API da Garmin (com a sua chave).
2. O backup do dia, já cifrado, para o seu bucket.
3. Notificações curtas para o iPhone ("há um novo relatório"), sem valores de saúde por padrão.
4. Na instalação e em atualizações: download de pacotes e da tabela de alimentos (sem dados pessoais).

Nada mais. Não há telemetria. O teste automatizado de privacidade confirma que os relatórios de validação
da Garmin e o estado do backup não carregam valores de saúde, chaves ou URLs.

### O que chega ao iPhone
Páginas do painel, pela rede privada da Tailscale com HTTPS, somente depois do login. O painel não é
publicado na internet (o script confere que o Tailscale Funnel está desligado). O navegador guarda em cache
só os arquivos da interface, não as respostas com dados.

### Seus controles
| Você quer | Como |
|---|---|
| Exportar tudo | `healthmon exportar /data/exportacao.json` |
| Apagar tudo no servidor | `healthmon apagar-dados --confirmar` |
| Revogar o acesso à Garmin | connect.garmin.com/app/settings/personalApiToken → revogar a chave. O sistema para de coletar na hora |
| Desativar completamente | Sequência de 6 passos em `IMPLANTACAO.md`, "Exportar, apagar e desativar tudo" |

### Riscos que permanecem e que você aceita ao autorizar
- O usuário que opera a VM (`ubuntu`) é administrador dela. Quem obtiver acesso a esse usuário, ou à sua conta
  Tailscale ou Oracle, lê o banco. Proteja as três contas com senha forte e segundo fator.
- `apagar-dados` remove banco, segredos, configuração, relatórios, exames e backups locais. Não alcança o bucket,
  as cópias no iPhone nem os dados que continuam na Garmin.
- O banco em uso não tem cifragem própria: depende da cifragem de disco da Oracle e do isolamento da VM.
- A Oracle pode recuperar a VM gratuita por ociosidade ou encerrar a conta. Mitigação: backup externo diário
  (perda máxima de 24 h) e cópia independente no iPhone. **Se os dois estiverem na Oracle e a conta for
  encerrada, só a cópia do iPhone resta.**
- Perder a chave de recuperação torna os backups ilegíveis. Perder a chave **e** a VM significa perder o histórico.
- Não consegui abrir os termos de uso da Oracle e da Tailscale neste ambiente; leia-os quanto a dados sensíveis.

## 1. Onde os dados ficam e quem pode vê-los (fase 2)
| Provedor | O que recebe | Onde fica | Observação |
|---|---|---|---|
| **Garmin** | Nada de novo: só requisições de leitura dos dados que ela já tem | Nuvem Garmin | Chave pessoal revogável a qualquer momento |
| **Oracle Cloud (VM)** | **Todos os dados de saúde**, perfil, refeições, exames e backups | Disco da VM, na região que você escolher (São Paulo recomendada) | Disco cifrado pela Oracle com chaves dela. Quem tiver acesso de administrador à VM ou à conta Oracle consegue ler o banco. Os backups têm cifragem própria, com a sua frase-senha |
| **Tailscale** | Nenhum conteúdo: o tráfego é cifrado de ponta a ponta (WireGuard) | — | Vê metadados: seus aparelhos, endereços e horários de conexão. Emite o certificado HTTPS do endereço `.ts.net`, cujo nome fica em registros públicos de certificados |
| **Apple (Web Push)** | Notificação cifrada, com texto genérico ("há um novo relatório") | Em trânsito | Vê que uma notificação foi enviada ao seu aparelho e quando |
| **GitHub** | Só código e documentação | Repositório privado | `.gitignore` bloqueia `data/`, bancos, backups e segredos; a CI usa dados sintéticos |
| **Crossref** | DOIs das referências científicas (`kb verificar`) | — | Nenhum dado pessoal |
| **Espelho da TACO no GitHub** | Um download anônimo da tabela de alimentos | — | Nenhum dado pessoal |
| **Telegram / e-mail** | Só se você ativar esses canais | — | Texto genérico por padrão |
| **Anthropic** | **Nada**, a menos que você ative a modalidade 3 com consentimento registrado | — | Desligada |

**Risco residual que você precisa aceitar para ter operação autônoma:** os dados passam a ficar num
servidor de terceiro. O banco SQLite não tem cifragem própria em nível de aplicação; a proteção em repouso
é a cifragem de disco do provedor, e o controle de acesso é o da VM (usuário sem privilégios, nenhuma porta
pública, acesso só pela sua rede privada). Cifrar o banco com chave que só você conhece exigiria digitar a
chave a cada reinício da VM, o que contraria a operação autônoma; não foi implementado.

**Termos dos provedores:** não consegui abrir os contratos da Oracle e da Tailscale neste ambiente. Antes
de enviar dados reais, leia as políticas de privacidade e os termos de ambos quanto a dados sensíveis.
O sistema é de uso pessoal: você é ao mesmo tempo titular e controlador dos dados.

## 2. Controles implementados na fase 2
- **Chave da Garmin:** digitada no terminal do servidor com eco desligado; gravada só em `secrets.env`
  (600); nunca em código, banco, relatórios ou logs (há teste que procura a chave em todos os arquivos
  gerados e nos logs).
- **Integração não oficial desativada:** não é instalada no contêiner; `healthmon login` com senha recusa.
- **API:** todas as rotas novas exigem sessão (teste percorre todas as rotas registradas); cabeçalho
  anti-CSRF nas escritas; limite de 64 KB por requisição (16 MB só para laudos); validação de faixas
  plausíveis; erros de validação retornam 400 sem detalhes internos.
- **Arquivos de exames:** só PDF e imagem, nome sanitizado, pasta 700 e arquivo 600, sem processamento.
- **Push:** chaves VAPID geradas localmente (600); só aceita destino `https://`; conteúdo genérico.
- **Contêiner:** usuário sem privilégios, sistema de arquivos somente leitura, sem *capabilities*, limite
  de memória e de processos, porta publicada só em `127.0.0.1`.
- **VM:** atualizações de segurança automáticas; script opcional que fecha o SSH público depois que o
  acesso pela rede privada funciona.
- **Backups (atualizado na fase 3):** cópia consistente pela API de backup do SQLite, `integrity_check`, cifragem
  assimétrica (X25519 + ChaCha20-Poly1305), checksum, envio diário ao bucket com conferência do SHA-256 e
  alerta em qualquer falha. Ver `RECUPERACAO_DESASTRES.md`.
- **Retenção:** respostas brutas da Garmin apagadas após 400 dias e histórico de execuções após 90
  (configurável); os indicadores diários ficam até você apagar.
- **Seus direitos:** `healthmon exportar` (portabilidade) e `healthmon apagar-dados --confirmar` (exclusão).
- **Dependências:** `pip-audit` sem vulnerabilidades conhecidas em 26/11/2026; roda a cada alteração na CI.
- **Desenvolvimento:** nenhum dado real foi usado em testes, commits, capturas de tela ou no contexto dos
  agentes; todos os testes usam dados sintéticos identificados como tais.

## 3. Sobre o Claude Code e os seus dados
Durante este desenvolvimento, o Claude Code não teve acesso a nenhum dado seu de saúde: a rede do ambiente
bloqueia a Garmin e nenhuma chave foi fornecida. Em sessões futuras, se você colar saídas de comandos do
servidor, elas passam pela Anthropic. Por isso `healthmon validar-garmin` mostra apenas nomes de campos e
tipos, sem valores. Não cole relatórios, exportações ou o conteúdo do banco em conversas.

## 4. Pendências de segurança conhecidas
Ver a lista em `RELATORIO_FASE2.md`.

---

# Fase 1: texto original

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
