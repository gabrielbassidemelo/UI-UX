# Implantação, validação real e recuperação de falhas

**Estado atual:** os scripts abaixo foram escritos e tiveram a sintaxe verificada; o contêiner foi
construído e executado no ambiente de desenvolvimento com dados sintéticos. **Nada foi testado em uma VM
da Oracle nem contra a API real da Garmin.** O sistema só deve ser considerado operacional depois do passo 5.

Antes de começar, leia `INFRAESTRUTURA.md` (escolha e riscos). Ao seguir este roteiro você está autorizando
o envio dos seus dados da Garmin para a VM que você criar.

Tudo pode ser feito pelo iPhone (Safari), embora um computador torne os passos 1 e 3 mais confortáveis.

## 1. Criar a VM (Oracle Cloud)
1. Crie a conta em cloud.oracle.com escolhendo a região **Brazil East (São Paulo)**.
2. Compute → Instances → Create instance:
   - Image: **Ubuntu 24.04**. Shape: **VM.Standard.E2.1.Micro** (Always Free). Se quiser IA local no
     futuro, use Ampere A1 dentro do limite gratuito vigente.
   - Em *Advanced options → Initialization script*, cole o conteúdo de `deploy/oracle/cloud-init.yaml`.
   - Guarde a chave SSH oferecida (acesso de emergência).
3. Aguarde ~5 minutos: o script instala Docker, Tailscale e atualizações automáticas.

## 2. Rede privada (Tailscale)
1. Crie a conta em tailscale.com e instale o app Tailscale no iPhone.
2. Na VM (pelo console "Cloud Shell" da Oracle ou por SSH com a chave do passo 1):
   `sudo tailscale up --ssh` e abra o link exibido para autorizar.
3. A partir daí, abra um terminal da VM pelo painel da Tailscale (Machines → SSH) direto do navegador.
4. Opcional e recomendado: `sudo ./deploy/oracle/fechar-ssh-publico.sh` (depois do passo 3) e remova a
   regra da porta 22 na Security List da VCN. A VM fica sem nenhuma porta aberta à internet.

## 3. Obter o código (repositório privado)
No terminal da VM, como usuário `healthmon` (`sudo -iu healthmon`):
```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 && cat ~/.ssh/id_ed25519.pub
```
Copie a chave pública e adicione em GitHub → repositório → Settings → **Deploy keys** (somente leitura). Depois:
```bash
git clone git@github.com:gabrielbassidemelo/UI-UX.git healthmon && cd healthmon
git checkout claude/garmin-cirqa-health-monitoring-2mpc9g
```

## 4. Configurar
```bash
./deploy/oracle/configurar.sh
```
O script constrói a imagem, pede a **chave pessoal da Garmin** (digitação oculta; gere em
connect.garmin.com/app/settings/personalApiToken), a data de expiração da chave, gera a frase-senha dos
backups (anote), pede a senha do painel, valida a integração real, baixa a tabela TACO, confere as
referências científicas e publica o painel apenas dentro da sua rede Tailscale.

## 5. Validação com dados reais (critério de "pronto")
O passo 6/7 do script roda `healthmon validar-garmin`, que:
- troca a chave por um token e chama os 12 endpoints oficiais para o dia anterior;
- mostra, para cada um, o status e **apenas a estrutura** da resposta (nomes de campos e tipos, sem valores);
- lista os indicadores que o leitor de dados reconheceu.

Critérios:
- [ ] Todos os endpoints respondem `ok` ou `vazio` (nenhum `erro`).
- [ ] Aparecem entre os indicadores reconhecidos: `sleep_seconds`, `sleep_score`, `hrv_last_night`,
      `resting_hr`, `steps`, `calories_total`, `stress_avg`, `bb_max`.
- [ ] No painel, Configurações mostra "validada em …" e, no dia seguinte, o relatório das 08h é gerado.

Se algum indicador esperado não for reconhecido, o formato real difere do que supus. Envie o arquivo
`/tmp/validacao-garmin.txt` (não contém valores de saúde) em uma sessão do Claude Code para ajustar o
leitor; depois `git pull` e `docker compose run --rm healthmon healthmon reprocessar`.

Em seguida: `docker compose run --rm healthmon healthmon historico --dias 90` (pode ser repetido; continua
de onde parou se o teto diário de requisições for atingido) e
`docker compose run --rm healthmon healthmon disponibilidade` para ver o que a CIRQA realmente entrega.

## 6. Backups fora da VM
Os backups diários (03h30) ficam em `data/backups/*.hmbk`, criptografados e testados (decifragem e
verificação de integridade) logo após a criação. Para sobreviver à perda da VM, copie-os periodicamente
para outro lugar: são seguros para guardar em qualquer nuvem, porque só abrem com a frase-senha. Exemplo,
de um computador na mesma rede Tailscale: `scp healthmon@<vm>:healthmon/data/backups/*.hmbk .`
A cópia automática para fora da VM **não está implementada**.

## Renovação da chave da Garmin
O sistema avisa 14 e 3 dias antes da data informada (e a Garmin envia e-mail). Gere a nova chave e, na VM:
```bash
cd ~/healthmon && nano data/secrets.env        # troque GARMIN_PERSONAL_API_KEY
nano data/config.toml                          # atualize [garmin] chave_expira_em
docker compose restart healthmon
```

## Atualização do sistema
```bash
cd ~/healthmon && git pull && docker compose build && docker compose up -d
```

## Recuperação de falhas
| Situação | O que acontece sozinho | O que fazer |
|---|---|---|
| VM reinicia | O contêiner volta (`restart: unless-stopped`); ao subir, tarefas interrompidas são marcadas e relatórios do dia que ficaram para trás são gerados | Nada |
| Garmin fora do ar ou erro 5xx | Até 4 novas tentativas com espera crescente; a próxima coleta repete | Nada |
| Limite de requisições (429) | A coleta para e só volta após 60 min | Se repetir, reduza a frequência em `[coleta] intervalo_min` |
| Chave inválida ou expirada | Alerta "Acesso à Garmin recusado" | Renovar a chave (acima) |
| Sono não sincronizou às 08h | 4 novas tentativas a cada 30 min; depois o relatório sai com aviso | Abrir o Garmin Connect no iPhone com a pulseira por perto |
| Formato da resposta mudou | O endpoint é marcado com erro, o resto continua; alerta de sincronização | `validar-garmin` e ajuste do leitor |
| Tarefa falhou | Fica registrada em Configurações → Histórico de execuções | Ver o detalhe; `docker compose logs --tail 100 healthmon` |
| Banco corrompido | — | `docker compose run --rm healthmon healthmon restaurar /data/backups/<arquivo>.hmbk` |
| VM perdida ou recuperada pela Oracle | — | Repetir os passos 1 a 4 sem a etapa de histórico, copiar um `.hmbk` para `data/backups/`, colocar a mesma frase-senha em `data/secrets.env` e restaurar |
| Esqueci a senha do painel | — | `docker compose run --rm healthmon healthmon senha-dashboard` |

## Exportação e exclusão dos dados (LGPD)
- Exportar tudo em JSON: `docker compose run --rm healthmon healthmon exportar /data/exportacao.json`
- Apagar tudo: `docker compose run --rm healthmon healthmon apagar-dados --confirmar` e, para encerrar o
  acesso, revogue a chave no site da Garmin e exclua a VM.
