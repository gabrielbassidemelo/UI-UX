# Arquitetura (fase 2)

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
│  service (agendador, histórico, recuperação) · backup (cifrado, testado)   │
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
| `profile.py` | Perfil, pesagens, peso de referência | novo |
| `metabolism.py` | TMB, três estimativas de gasto total, recalibração, sinal de possível adaptação | novo |
| `training.py` | Biblioteca de exercícios, plano periodizado, progressão, zonas, decisão diária, limites | novo |
| `nutrition.py` | Metas, base de alimentos (busca FTS5), registro por texto, programação linear | novo |
| `supplements.py`, `kb.py` | Fichas e regras de suplementação; base científica com busca BM25 e verificação de DOI | novo |
| `labs.py` | Exames com valor, unidade e referência do laboratório | novo |
| `learning.py` | Experimentos n-de-1, padrões observacionais, histórico de decisões | novo |
| `planner.py` | Plano do dia e objetivos | novo |
| `push.py`, `notify.py` | Web Push (PWA), Telegram, e-mail | push novo |
| `service.py` | Agendador, histórico de execuções, recuperação ao reiniciar, retenção | ampliado |
| `web/` | API e PWA de cinco abas | ampliado |

Reaproveitado da fase 1 sem reescrita: banco, parser, análise estatística, relatórios, alertas, backups,
autenticação e a casca do painel. Os 28 testes originais continuam passando sem alteração de comportamento
(um deles teve só a assinatura de um *mock* atualizada).

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
| Efeito em experimentos | Diferença de medianas + teste de permutação, mínimo de 6 dias por condição | Não assume distribuição; serve para amostras pequenas |

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
- **Pontos de prontidão para treino:** soma de sinais (indicadores objetivos, percepção subjetiva, sono
  curto, pico de carga), cada um valendo 1 ou 2; 0–1 treino normal, 2–3 reduzido, 4 ou mais recuperação.
  Os pesos são uma regra de decisão transparente, **não validada em estudo**, e ficam visíveis no painel.
