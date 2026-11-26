# healthmon — monitoramento pessoal de saúde e recuperação (Garmin CIRQA)

Sistema local e automatizado que coleta os dados da sua conta Garmin Connect, guarda o histórico num
banco SQLite no seu computador, constrói uma **linha de base individual**, identifica tendências e
padrões persistentes, gera relatórios diário/noturno/semanal/mensal em português e mostra tudo num
dashboard para iPhone, com modo escuro.

```
CIRQA ──BT──► app Garmin Connect (iPhone) ──► nuvem Garmin
                                                  │  (garminconnect, só leitura, tokens locais)
                                                  ▼
             ┌──────────── seu computador (healthmon) ─────────────┐
             │ coleta ► SQLite ► análise estatística ► relatórios  │
             │            ▲             │             (regras / IA │
             │            │             ▼              local)      │
             │      backups .hmbk   alertas ► Telegram/e-mail      │
             │      (criptografados)  dashboard (FastAPI) ◄────────┼── iPhone via Tailscale (HTTPS privado)
             └──────────────────────────────────────────────────────┘
```

## O que faz
- **Coleta automática** a cada 2 h: FC (repouso/mín/máx), HRV, sono (fases, pontuação, despertares,
  respiração, SpO2, estresse no sono, temperatura da pele), estresse, Body Battery, prontidão para treino,
  passos, calorias, minutos de intensidade, atividades e VO2 máx., **quando fornecidos pela sua conta**.
  `healthmon disponibilidade` lista o que a CIRQA de fato entrega e o que está ausente.
- **Análise local:** linha de base robusta (mediana/MAD, 28 dias), separação entre desvio pontual e
  padrão persistente, tendências (Theil–Sen + Mann–Kendall), associações de Spearman com defasagem,
  avaliação de recuperação. Veja [docs/METODOLOGIA.md](docs/METODOLOGIA.md).
- **Relatórios:** diário (08h, com novas tentativas se o sono não sincronizou), noturno (22h), semanal
  (domingo) e mensal (com gráficos comparativos).
- **Alertas sem excesso:** queda persistente de HRV, aumento da FC de repouso, piora do sono, recuperação
  insuficiente, estresse elevado e falhas de sincronização, sempre em relação ao seu histórico, com cooldown,
  limite diário e horário silencioso.
- **Dashboard (PWA):** indicadores do dia, gráficos interativos com faixa usual e média móvel, filtros de
  7/30/90/365 dias, comparação entre períodos, relatórios, alertas e status. Pode ser instalado na tela
  inicial do iPhone.
- **IA opcional:** motor de regras local (padrão), LLM local via Ollama, ou Claude na nuvem **somente
  com consentimento explícito** e prévia exata do que seria enviado.

## Início rápido
```bash
./deploy/instalar.sh
healthmon login            # Garmin: e-mail/senha/MFA uma única vez (a senha não é salva)
healthmon senha-dashboard
healthmon historico --dias 90
healthmon executar         # agendador + dashboard em http://127.0.0.1:8765
```
O passo a passo completo (serviço permanente, Docker, acesso pelo iPhone com Tailscale, Telegram,
Ollama e solução de problemas) está em **[docs/OPERACAO.md](docs/OPERACAO.md)**.

Para conhecer o sistema sem a sua conta: `HEALTHMON_HOME=/tmp/hm-demo healthmon demo` gera dados
**simulados**, sempre marcados como tal no dashboard e nos relatórios.

## Documentação
- [docs/PESQUISA_INTEGRACOES.md](docs/PESQUISA_INTEGRACOES.md): APIs avaliadas, compatibilidade com a CIRQA, riscos e fontes
- [docs/METODOLOGIA.md](docs/METODOLOGIA.md): métodos estatísticos e referências científicas
- [docs/SEGURANCA_LGPD.md](docs/SEGURANCA_LGPD.md): o que fica onde, o que sai da máquina, consentimento
- [docs/OPERACAO.md](docs/OPERACAO.md): instalação, manutenção e recuperação de falhas

## Testes
```bash
pip install -e ".[dev]" && pytest
```

## Aviso
Ferramenta de acompanhamento pessoal. Compara você com o seu próprio histórico a partir de medições de
um vestível. **Não faz diagnóstico, não substitui avaliação profissional e não trata medições como exames
clínicos.** Associações estatísticas não implicam causalidade.

A coleta usa a biblioteca não oficial `garminconnect`, porque a API oficial de saúde da Garmin é restrita
a empresas. Os riscos e a alternativa oficial (Personal API) estão descritos em
[docs/PESQUISA_INTEGRACOES.md](docs/PESQUISA_INTEGRACOES.md).
