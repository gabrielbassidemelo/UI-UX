# Arquitetura (fases 2 e 3)

Esta versão inclui a fase 3: triagem de saúde obrigatória, estágios de calibração, classes de verificação
das referências, backup HMBK2 com cópia externa e a tela de conferência com o app Garmin. A fase 3 acrescentou
as regras de decisão e a trava de evidências descritas em "Fluxo de decisão" abaixo.

```
CIRQA ─BT─► Garmin Connect (iPhone) ─► nuvem Garmin
                                           │  Personal API oficial (chave pessoal → token de 24 h)
                                           ▼
┌───────────────────────── VM (contêiner healthmon) ─────────────────────────┐
│ garmin_official ► raw_payloads ► parse ► daily_metrics                     │
│                                              │                             │
│  você informa ► perfil, peso, refeições,     ▼                             │
│  (painel)       check-in, treinos, exames ► analysis + stats (estatística) │
│                                              │                             │
│        metabolism ◄──────────────────────────┤                             │
│        nutrition ◄── foods (TACO)            ├► training (plano adaptativo)│
│        supplements ◄── kb (evidências, BM25) ├► learning (experimentos)    │
│        labs                                  ▼                             │
│                          planner (plano do dia) ► reports ► notify (push)  │
│  service (agendador) · backup (HMBK2) · offsite (cópia externa)           │
│  web (FastAPI + PWA) ◄──── HTTPS só na rede privada Tailscale ──── iPhone  │
└────────────────────────────────────────────────────────────────────────────┘
```

## Módulos
| Módulo | Função | Origem |
|---|---|---|
| `garmin_official.py` | Fonte oficial: autenticação, cache de token, orçamento diário, backoff, disjuntor após 429 | novo |
| `garmin.py` | Fonte não oficial (`garminconnect`), **desativada por padrão** (`pip install "healthmon[nao-oficial]"` + `[garmin] fonte = "nao_oficial"`) | fase 1 |
| `parse.py`, `sync.py`, `db.py` | Normalização defensiva, sincronização incremental sem duplicatas, SQLite | fase 1, ampliados |
| `analysis.py`, `insights.py`, `alerts.py`, `reports.py` | Linha de base, persistência, tendências, alertas, relatórios | fase 1 |
| `stats.py` | EWMA, bases de 7/28/90 dias, dia da semana, qualidade dos dados, CUSUM, backtesting | novo |
| `profile.py` | Perfil, pesagens e peso de referência (média das pesagens dos últimos 7 dias; sem pesagem real, as metas não são liberadas) | novo |
| `metabolism.py` | TMB, três estimativas de gasto total, recalibração, sinal de possível adaptação | novo |
| `training.py` | Biblioteca de exercícios, plano periodizado, progressão, zonas, decisão diária, limites | novo |
| `nutrition.py` | Metas, base de alimentos (busca FTS5), registro por texto, programação linear | novo |
| `supplements.py` | Fichas e regras de suplementação, com a trava de evidências | novo |
| `kb.py`, `kb/evidencias.json`, `kb/verificacao.json` | Base científica com busca BM25; classes de verificação (verificada, parcialmente verificada, não verificada, inadequada); `healthmon kb verificar` consulta o Crossref pelo DOI | novo; classes na fase 3 |
| `labs.py` | Exames com valor, unidade e referência do laboratório | novo |
| `learning.py` | Experimentos n-de-1, padrões observacionais, histórico de decisões | novo |
| `planner.py` | Plano do dia e objetivos | novo |
| `push.py`, `notify.py` | Web Push (PWA), Telegram, e-mail | push novo |
| `service.py` | Agendador, histórico de execuções, recuperação ao reiniciar, retenção | ampliado |
| `safety.py` | Triagem obrigatória (15 perguntas e campos básicos), sintomas do dia, bloqueios por condição, filtro do texto gerado por IA | fase 3 |
| `calibration.py` | Estágios de calibração (observação, linha de base, personalizado, longitudinal) e o que cada estágio permite | fase 3 |
| `feedback.py` | Cruzamento entre o que você relata e o que a pulseira mede, e avaliação das recomendações (só após 90 dias). **Nesta versão só os testes o usam**: não há tela nem endpoint | fase 3 |
| `garmin_validation.py` | Validação da API oficial (relatório sem valores de saúde) e registro de conferência igual/diferente com o app Garmin Connect | fase 3 |
| `backup.py` | Backup HMBK2: criptografia assimétrica (X25519 + ChaCha20-Poly1305). O servidor cria o backup, mas não o decifra. O formato HMBK1 (frase-senha) só é lido para backups antigos | fase 3 |
| `offsite.py` | Cópia externa (Oracle Object Storage, por PAR): envio, verificação por SHA-256, estado e alertas de atraso | fase 3 |
| `validacao/` | Medição por simulação, com dados sintéticos, dos detectores estatísticos. Não é validação com dados reais: ver `VALIDACAO_ESTATISTICA.md` | fase 3 |
| `web/` | API e PWA de cinco abas | ampliado |

Reaproveitado da fase 1: banco, parser, análise estatística, relatórios, alertas, autenticação e a casca do
painel. Backups da fase 1 (HMBK1, com frase-senha) continuam legíveis, mas os novos backups usam HMBK2.

Testes: `pytest -q`, com dados sintéticos. Na execução de 26/11/2026 a suíte passou inteira (318 testes).
Isso não é validação com dados reais.

## Fluxo de decisão: triagem → trava de evidências → estágio → recomendação
1. **Triagem (`safety.screen`).** Sem sexo, nascimento, altura ou alguma das 15 perguntas, as áreas
   personalizadas (déficit, metas, treino e suplementos) ficam suspensas, com a lista do que falta. Menores
   de 18 anos ficam bloqueados. Respostas "sim" geram bloqueios por área, com o motivo. Sem pesagem real não
   há metas. Os sintomas do dia (`symptom_flags`) também entram aqui: qualquer sintoma marcado define o
   nível "parar" do dia, e texto livre só reforça.
2. **Trava de evidências (`kb.failing`).** Cada regra cita referências. Referência abaixo de
   `[evidencias] nivel_minimo` (padrão: `parcialmente_verificada`) desabilita a regra que depende dela.
3. **Estágio de calibração (`calibration.stage`).** Conta dias válidos (pelo menos 2 de 4 indicadores
   centrais por dia). Alertas de tendência e redução de treino por recuperação entram a partir da linha de
   base. Progressão de carga e calibração metabólica entram a partir de 28 dias válidos. Comparações de longo
   prazo e avaliação das recomendações entram a partir de 90 dias. Alertas, treino, metabolismo e relatórios
   consultam essa decisão.
4. **Recomendação.** O motor de regras (`training.readiness`, `nutrition`, `planner`) produz a recomendação.
   Com IA ligada, o texto gerado passa por `safety.llm_text_allowed` antes de aparecer. O texto que mencione
   medicamento, dose, doença, restrição alimentar ou esforço intenso é descartado. Plano e meta só valem depois
   da sua confirmação.

## Camadas de inteligência
1. **Modelos matemáticos:** equações metabólicas, balanço energético, zonas de frequência cardíaca, 1RM
   estimado, programação linear de refeições.
2. **Estatística:** medianas e MAD, EWMA, regressão com intervalo de confiança, Theil–Sen, Mann–Kendall,
   Spearman, CUSUM, teste de permutação.
3. **Aprendizado individual:** linhas de base móveis, recalibração do gasto pelos seus registros,
   progressão de carga pelos treinos registrados, experimentos A/B em você mesmo.
4. **Regras verificáveis:** limites de segurança, contraindicações e interações, decisão de treino por
   pontos. Tudo em código testado, com referência na base científica.
5. **IA generativa opcional:** só redige comentários sobre números já calculados. Não calcula, não define
   limites e não decide.

## Modelos estatísticos: o que foi adotado e por quê
| Necessidade | Método | Por quê |
|---|---|---|
| Faixa usual | Mediana ± desvio robusto (MAD com piso de 0,85 × DP), 28 dias; 7 e 90 como apoio | Resiste a noites atípicas |
| Tendência de peso | EWMA de 7 dias + regressão linear com IC95% | Simples, transparente, com incerteza |
| Mudança persistente | Regra de 7 dias (fase 1) + CUSUM robusto | Dois métodos independentes |
| Previsão de 1 dia | Comparação entre último valor, sazonal semanal, mediana de 7 dias, suavização exponencial e Holt amortecido | Escolha em 60% das previsões e medição nos 40% restantes; só troca o método ingênuo com ganho ≥ 5% |
| Efeito em experimentos | Médias por bloco + teste de permutação, mínimo de 5 blocos por condição | Não assume distribuição; respeita a dependência entre dias seguidos |

**Não adotados:** ETS completo, ARIMA, aprendizado de máquina e modelos de fundação para séries temporais
(como o TimesFM). Com uma série diária por indicador e poucos meses de histórico, não há base para esperar
ganho sobre os métodos simples, e o custo computacional não cabe numa VM de 1 GB. O `backtest` já está
pronto para comparar qualquer modelo novo: basta acrescentá-lo a `stats.MODELS`.

Em dados **sintéticos**, a suavização venceu o método ingênuo em séries estacionárias ruidosas e o método
ingênuo venceu num passeio aleatório, como esperado. Isso valida o procedimento, não a utilidade com os
seus dados, que só poderá ser medida após alguns meses de coleta real.

## IA generativa em operação: três modalidades
| Modalidade | Estado | O que usa | Dados que saem |
|---|---|---|---|
| 1. Automática gratuita | **Padrão** | Estatística, regras, modelos e textos por modelo | Nenhum |
| 2. Assistente com IA local | Opcional, desligada | Ollama na própria VM | Nenhum |
| 3. IA externa | **Desligada**; exige `healthmon ia-nuvem consentir` | API da Anthropic, com sua chave | Só o resumo agregado mostrado em `ia-nuvem previa` |

O Claude Code serve para construir e manter o sistema; a operação diária não depende dele, nem usa a sua
assinatura do Claude como se fosse uma API.

**Modalidade 2, avaliação por requisitos (nada foi medido aqui):** na VM AMD de 1 GB nenhum modelo útil
cabe. Numa Ampere A1 de 12 GB cabem modelos de 3 a 8 bilhões de parâmetros quantizados em 4 bits (cerca de
2 a 5 GB de memória), como Qwen 2.5 (licença Apache 2.0 na maioria dos tamanhos), Mistral 7B (Apache 2.0),
Llama 3.x (licença comunitária da Meta) e Gemma (termos de uso do Google). Em 2 núcleos Arm a geração tende
a ser lenta (poucas palavras por segundo), aceitável para um comentário diário em segundo plano e ruim para
conversa. Não recomendo instalar agora: primeiro meça velocidade e qualidade em português na sua VM com
`ollama run` e alguns relatórios sintéticos. Sem isso, a modalidade 1 já entrega todos os relatórios.

**Modalidade 3, o que falta:** orçamento máximo configurável, bloqueio automático de gastos e trilha de
auditoria das chamadas **não estão implementados**. Por isso ela permanece desligada e não deve ser ativada
ainda.

## Pontuações próprias
O sistema tem duas e nenhuma é indicador clínico:
- **Qualidade dos dados (0–100):** média da cobertura, nos últimos 14 dias, dos indicadores principais que
  o dispositivo fornece. Mede completude, não saúde.
- **Pontos de prontidão para treino:** soma de sinais (quatro grupos objetivos independentes — HRV, FC de
  repouso, sono e estresse no sono —, percepção subjetiva, sono curto, pico de carga), cada um valendo 1
  ou 2; 0–1 treino normal, 2–3 reduzido, 4 ou mais recuperação. Body Battery e prontidão do Garmin ficam
  de fora por serem índices compostos de HRV e sono. Qualquer sintoma marcado no check-in define o nível
  "parar", independentemente da pontuação.
  Os pesos são uma regra de decisão transparente, **não validada em estudo**, e ficam visíveis no painel.
