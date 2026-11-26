# Validação estatística por SIMULAÇÃO dos detectores do healthmon

> **Atualização:** as seções 1–9 são a medição ORIGINAL (código antes das correções) e foram mantidas sem alteração. A seção 10 traz as correções e a nova medição (conjunto de confirmação, também SIMULAÇÃO).

> **ATENÇÃO — ISTO SÃO SIMULAÇÕES, NÃO VALIDAÇÃO COM DADOS REAIS.**
> Todos os números abaixo vêm de séries **sintéticas** geradas por um simulador escrito para este
> estudo. Nenhum dado de pessoa real foi usado. Os resultados dizem como os detectores se comportam
> **dentro do modelo simulado** (e quanto eles dependem de suposições do simulador); **não comprovam
> utilidade clínica**, não substituem avaliação médica e não podem ser citados como "validado com
> dados reais". O próprio código do `readiness` já declara que a regra "não foi validada em estudo".

Arquivos: `validacao/simulador.py` (gerador), `validacao/avaliar.py` (avaliador),
`validacao/resultados.json` (números brutos, conjunto de avaliação), `tests/test_validacao_estatistica.py`
(versão reduzida para o CI).

## 1. Resumo

Medidas no **conjunto de avaliação** (1.920 séries de 180 dias; rodado uma única vez ao final):

- **Falsos alarmes são altos quando se olha "qualquer dos 7 indicadores".** Em séries realistas sem evento, o padrão
  `persistente` do `assess` ativa em 19,5% (18,5%–20,5%) dos dias e em 93% das janelas de 30 dias há
  pelo menos um dia de falso alarme (`alerts.evaluate`: 18,1% (16,5%–19,8%) dos dias). Só para o HRV, a taxa cai para 3,2% (2,7%–3,7%).
  Em produção o *cooldown* de 72 h reduz a quantidade de notificações; aqui ele foi desligado para medir o detector em si.
- **O `changepoint` (CUSUM) está descalibrado:** reporta "mudança de nível" em 86,5% (84,7%–87,9%) dos dias sem evento (DEFEITO, ver seção 6).
- **A `tendência` (Mann–Kendall) também**: 46,5% (45,0%–48,0%) dos dias sem evento (qualquer dos 7 indicadores).
- O `readiness` é o detector mais conservador: 4,9% (4,2%–5,7%) dos dias fora de "normal" sem evento
  (por isso também erra no sentido contrário: com perturbação de 2 desvios, mantém "normal" em 39,4% (31,9%–47,3%) dos dias do evento; com 1 desvio, 68,6% (61,3%–76,6%)).
- Para perturbações curtas (3–10 dias) o detector `persistente` é **menos sensível que o método simples z ≥ 2 no dia**, com falso alarme praticamente igual (seção 5).
- Os resultados com 0,5 desvio são pouco melhores que o acaso para os detectores de "qualquer indicador" (seção 4.2).

## 2. Método

### 2.1 Simulador (`validacao/simulador.py`, escrito do zero, independente de `healthmon/demo.py`)

Cada série tem 180 dias (a partir de uma segunda-feira) e 7 indicadores: `hrv_last_night`, `resting_hr`, `sleep_score`,
`sleep_seconds`, `sleep_stress_avg`, `stress_avg`, `steps`.

| Elemento | Modelo |
|---|---|
| Médias e dispersões | valores típicos adultos (HRV ~55 ms com desvio de 0,18 em log; FC de repouso ~58 bpm, dp 2,6; escore de sono ~76, dp 8; sono ~7,2 h, dp 0,83 h; estresse do sono ~17, dp 4; estresse ~30, dp 6; passos ~8.500, dp 2.800), sorteados por "pessoa" (HRV: média ×0,65–1,6; demais ±8%; dispersão ×0,8–1,25) |
| Autocorrelação | ruído AR(1) por indicador com φ sorteado em [0,3; 0,7] + fator latente comum de recuperação (também AR(1), φ em [0,3; 0,7]); autocorrelação lag-1 resultante ≈ 0,48 |
| Correlação entre indicadores | cargas no fator comum: HRV +0,65, FC repouso −0,60, escore do sono +0,50, duração do sono +0,40, estresse no sono −0,50, estresse −0,35, passos +0,15 (correlação HRV × FC de repouso ≈ −0,38) |
| Dia da semana | passos (sáb. +0,3 dp, dom. −0,5 dp), sono mais longo sex./sáb., estresse maior em dias úteis; amplitude ×0,6–1,4 por pessoa |
| Ausências | 4% dos valores ausentes ao acaso + 1 bloco de 3–10 dias sem nenhum dado (pulseira fora do pulso) |
| Erros de sensor | 1% dos valores substituídos por absurdos isolados (HRV 4 ou 400, FC 22 ou 200, sono 0 ou 24 h, passos 0 ou 90.000...) + 1 episódio de leitura **travada** (mesmo valor por 3–6 dias) |
| Mudança de rotina | 2 episódios de 10–21 dias com passos ±1,0–1,5 dp e sono −0,5/−0,3 dp (férias/viagem); **não é evento de saúde** |
| Cauda pesada | cenário "extremos": inovações t de Student (3 gl) + 3% de absurdos + 2 leituras travadas |
| Perturbação fisiológica | 3, 5, 7 ou 10 dias começando entre os dias 90 e 130; magnitude M ∈ {0,5; 1; 1,5; 2} desvios marginais **em todos os indicadores ao mesmo tempo** (HRV −M, FC +M, escore −M, duração −0,8 M, estresse do sono +M, estresse +0,8 M, passos −0,5 M) |
| Degrau persistente | mesmo vetor de efeitos, mantido até o fim da série |
| Deriva gradual | rampa linear de 60 dias (iniciando entre os dias 85 e 100) até M desvios, depois mantida |

Os dados entram no sistema por `Database.upsert_daily` em um banco SQLite temporário (um por processo). Os detectores
são chamados como o produto os chama; o avaliador só lê do banco dias ≤ dia avaliado (há teste de ausência de vazamento de futuro).

### 2.2 Detectores avaliados e como viram um estado diário 0/1

Todos usam apenas as configurações padrão do produto (janela de base 28 d, mínimo 10 d); nenhum limiar foi alterado.

| Detector | Estado "ativo" no dia |
|---|---|
| `assess` persistente | `padrao == "persistente"` e `leitura == "desfavoravel"` e base confiável, em **qualquer** dos 7 indicadores (variante "só HRV") |
| `assess` pontual | `padrao == "pontual"` (estritamente: |z| ≥ 1,5 sem ser persistente), desfavorável, qualquer dos 7 |
| `trend` | `direcao` desfavorável (HRV/sono/passos "descendo"; FC/estresse "subindo"), janela 28 d, qualquer dos 7 |
| `stats.changepoint` | retorno não nulo (mudança desfavorável na janela de 60 d), qualquer dos 7 |
| `alerts.evaluate` | gerou ao menos um alerta no dia (cooldown = 0 para cada dia ser independente) |
| `training.readiness` | nível ≠ "normal" (sem check-in; "parar" é inalcançável sem sintoma) |

**Referências simples (implementadas aqui):** (a) z ≥ 2 no dia contra média e desvio-padrão dos 28 dias anteriores; (b) média de 7 dias
contra a média dos 28 dias anteriores a esses 7, ativa se a diferença ≥ 1 desvio-padrão (diário) dos 28 dias, no sentido desfavorável. Ambas
em "qualquer dos 7" e "só HRV" (HRV em log, como o sistema).

### 2.3 Protocolo e tamanhos

- Dois conjuntos de sementes **disjuntos por construção** (`desenvolvimento`: faixa 10.000+; `avaliacao`: 5.000.000+). O conjunto de
  desenvolvimento serviu para depurar o avaliador e escolher as faixas dos testes; o de avaliação foi rodado **uma única vez**, ao final,
  depois de o código do avaliador estar congelado, e é o único reportado abaixo. (Uma execução anterior em desenvolvimento, em escala
  maior, orientou as faixas do teste de CI; uma pequena adição de agregação — taxa de ativação por janela — e a redução dos tamanhos foram feitas antes da execução de avaliação.)
- Tamanhos (avaliação): 200 séries realistas, 200 "nulo", 120 "rotina", 120 "extremos" (sem evento; 90 dias avaliados por série, dias 90–179);
  perturbação: 16 células (4 magnitudes × 4 durações) × 40 séries = 640; degrau: 4 × 80; deriva: 4 × 80. Detectores caros (`alerts`, `readiness`) rodam só nas
  primeiras 60 (nulos 200), 40 (nulos 120), 20 (por célula de perturbação) ou 30 (degrau/deriva) séries, por custo de tempo.
  Tempo de execução da avaliação: 292 s com 4 processos.
- **Intervalos de confiança:** Wilson (95%) para sensibilidades (séries independentes); bootstrap por série (conglomerado, 500 reamostragens, 95%)
  para taxas por dia, por 30 dias, viradas de estado e frações de dias.

### 2.4 Definições das métricas

- **Falso alarme por dia / especificidade por dia:** fração de dias de séries sem evento em que o detector está ativo / 1 − isso.
- **Falso alarme em 30 dias:** fração de blocos de 30 dias sem evento com ao menos um dia ativo.
- **Sensibilidade (perturbação):** fração de séries em que o detector ficou ativo ao menos um dia **durante** o evento (também reportada até 7 dias após o fim). **Atraso:** dias entre o início do evento e o primeiro dia ativo (mediana entre os detectados; 0 = ativo no primeiro dia).
- **Sensibilidade (degrau/deriva):** ativo ao menos uma vez em 14 d (degrau) ou 30 d (deriva) / até o fim da janela avaliada (45 d / 75 d).
- **Estabilidade:** probabilidade de o estado mudar de um dia para o seguinte em séries sem evento (quanto menor, mais estável).
- **Acaso:** probabilidade de o detector ativar ao menos uma vez em uma janela do mesmo tamanho **sem evento**. Uma "sensibilidade" só significa algo
  quando comparada com isso, sobretudo para detectores com muitos falsos alarmes.
- **Readiness incorreto:** (i) reduzir o treino sem evento (nível ≠ normal em série sem evento); (ii) manter "normal" em dia de perturbação fisiológica.

## 3. Falsos alarmes e estabilidade (séries SEM evento de saúde)

Taxa de dias ativos (IC95% bootstrap). Fonte: `resultados.json › nulos`.

| Detector | nulo (AR+dia da semana) | **realista** | rotina | extremos |
|---|---|---|---|---|
| assess persistente (qualquer dos 7) | 25,0% (23,9%–26,2%) | 19,5% (18,5%–20,5%) | 22,4% (20,9%–24,2%) | 15,8% (14,5%–17,0%) |
| assess persistente (só HRV) | 4,2% (3,7%–4,8%) | 3,2% (2,7%–3,7%) | 3,4% (2,8%–4,1%) | 2,4% (1,8%–2,9%) |
| assess pontual (qualquer dos 7) | 37,4% (36,8%–38,0%) | 32,8% (32,2%–33,4%) | 32,9% (32,1%–33,6%) | 28,9% (28,0%–29,8%) |
| tendência (Theil–Sen+MK, 28 d) | 49,9% (48,4%–51,3%) | 46,5% (45,0%–48,0%) | 53,0% (50,8%–55,4%) | 45,8% (43,7%–47,9%) |
| changepoint (CUSUM) | 87,2% (85,6%–88,6%) | 86,5% (84,7%–87,9%) | 89,4% (87,5%–91,0%) | 93,3% (92,1%–94,6%) |
| alerts.evaluate | 23,7% (21,8%–25,8%) | 18,1% (16,5%–19,8%) | 17,8% (15,8%–19,9%) | 10,9% (9,1%–12,9%) |
| readiness (nível ≠ normal) | 8,4% (7,3%–9,8%) | 4,9% (4,2%–5,7%) | 5,1% (3,8%–6,5%) | 1,5% (1,0%–2,0%) |
| REF a: z ≥ 2 no dia (qualquer dos 7) | 20,0% (19,4%–20,5%) | 18,2% (17,8%–18,8%) | 18,9% (18,2%–19,5%) | 19,8% (19,3%–20,4%) |
| REF a: z ≥ 2 (só HRV) | 3,5% (3,2%–3,7%) | 3,0% (2,8%–3,3%) | 3,1% (2,8%–3,4%) | 3,0% (2,7%–3,3%) |
| REF b: média 7 d vs 28 d ≥ 1 dp (qualquer dos 7) | 38,9% (37,6%–40,2%) | 41,1% (39,8%–42,3%) | 44,7% (42,9%–46,6%) | 45,1% (43,4%–47,0%) |
| REF b: 7 d vs 28 d (só HRV) | 7,5% (6,8%–8,3%) | 8,4% (7,6%–9,2%) | 8,8% (7,9%–9,8%) | 8,0% (7,0%–9,0%) |

Cada cenário: `nulo` = só AR(1)+dia da semana; `realista` = + ausências, absurdos, leituras travadas; `rotina` = realista + 2 mudanças de rotina
(passos/sono); `extremos` = cauda pesada + 3% de absurdos. Os detectores `alerts`/`readiness` rodam em subconjunto (60 ou 40 séries por cenário), com ICs mais largos.

**Falso alarme em janelas de 30 dias, viradas de estado por dia e episódios (série realista):**

| Detector | ≥1 ativação em 30 d | viradas de estado/dia | episódios/30 d |
|---|---|---|---|
| assess persistente (qualquer dos 7) | 93% (91%–95%) | 12,4% (11,8%–13,0%) | 1,91 |
| assess persistente (só HRV) | 31% (27%–34%) | 2,4% (2,1%–2,7%) | 0,36 |
| assess pontual (qualquer dos 7) | 100% (100%–100%) | 36,0% (35,3%–36,7%) | 5,47 |
| tendência (Theil–Sen+MK, 28 d) | 99% (98%–100%) | 13,1% (12,6%–13,7%) | 2,10 |
| changepoint (CUSUM) | 100% (100%–100%) | 4,3% (3,8%–4,8%) | 0,93 |
| alerts.evaluate | 94% (90%–97%) | 12,5% (11,4%–13,5%) | 1,91 |
| readiness (nível ≠ normal) | 63% (56%–71%) | 6,3% (5,4%–7,2%) | 0,96 |
| REF a: z ≥ 2 no dia (qualquer dos 7) | 100% (99%–100%) | 25,6% (25,0%–26,3%) | 3,87 |
| REF a: z ≥ 2 (só HRV) | 58% (54%–62%) | 5,1% (4,7%–5,5%) | 0,76 |
| REF b: média 7 d vs 28 d ≥ 1 dp (qualquer dos 7) | 99% (98%–100%) | 14,0% (13,5%–14,4%) | 2,20 |
| REF b: 7 d vs 28 d (só HRV) | 54% (50%–58%) | 3,9% (3,6%–4,2%) | 0,60 |

Leitura: o `alerts.evaluate` produziria, sem o *cooldown*, cerca de 1,9 episódios de alerta por 30 dias por pessoa sem nenhum evento
(com o cooldown real de 72 h seriam no máximo ~10 notificações/30 d por tipo; ainda assim muitas). O `changepoint` quase não vira de estado
porque, uma vez disparado, o resultado persiste por toda a janela de 60 dias (ver seção 6). O padrão `pontual` e a referência (a) viram de estado em 26–36% dos
dias: são "ruído de um dia", como esperado de um limiar diário.

Níveis do `readiness` em série realista sem evento: normal 95,1%, reduzido 4,9%, recuperação 0,04%.

## 4. Sensibilidade a eventos

### 4.1 Perturbação fisiológica temporária (3–10 dias), por magnitude

Sensibilidade durante o evento (IC95% Wilson; todas as durações juntas, 160 séries por magnitude; `alerts`/`readiness`: 80). Atraso = mediana em dias.

| Detector | M = 0,5 | M = 1 | M = 1,5 | M = 2 | atraso (M=1 / M=2) |
|---|---|---|---|---|---|
| assess persistente (qualquer dos 7) | 59% (51%–66%) | 64% (56%–71%) | 82% (76%–88%) | 89% (83%–93%) | 1 / 1 |
| assess persistente (só HRV) | 19% (13%–26%) | 24% (18%–31%) | 40% (33%–48%) | 46% (38%–53%) | 3 / 3 |
| assess pontual (qualquer dos 7) | 87% (81%–91%) | 96% (92%–98%) | 99% (97%–100%) | 96% (92%–98%) | 0 / 0 |
| tendência (Theil–Sen+MK, 28 d) | 82% (75%–87%) | 82% (76%–88%) | 91% (85%–94%) | 91% (86%–95%) | 0 / 0 |
| changepoint (CUSUM) | 97% (93%–99%) | 96% (92%–98%) | 99% (97%–100%) | 99% (97%–100%) | 0 / 0 |
| alerts.evaluate | 60% (49%–70%) | 65% (54%–75%) | 82% (73%–89%) | 90% (81%–95%) | 1,5 / 1 |
| readiness (nível ≠ normal) | 39% (29%–50%) | 62% (52%–72%) | 80% (70%–87%) | 88% (78%–93%) | 1 / 0 |
| REF a: z ≥ 2 no dia (qualquer dos 7) | 79% (72%–84%) | 89% (83%–93%) | 98% (94%–99%) | 96% (92%–98%) | 0 / 0 |
| REF a: z ≥ 2 (só HRV) | 28% (21%–35%) | 39% (32%–47%) | 57% (49%–64%) | 59% (52%–67%) | 1 / 1 |
| REF b: média 7 d vs 28 d ≥ 1 dp (qualquer dos 7) | 79% (72%–84%) | 79% (72%–84%) | 94% (89%–97%) | 94% (89%–97%) | 1 / 0 |
| REF b: 7 d vs 28 d (só HRV) | 28% (21%–35%) | 40% (33%–48%) | 49% (42%–57%) | 62% (55%–70%) | 2,5 / 2 |

### 4.2 Contexto: chance de ativar numa janela sem evento (acaso), série realista

Se um detector ativa com frequência sem evento, sua "sensibilidade" é inflada. Probabilidade de ≥1 ativação em uma janela sem evento de D dias:

| Detector | 3 d | 5 d | 7 d | 10 d |
|---|---|---|---|---|
| assess persistente (qualquer dos 7) | 31% | 40% | 48% | 58% |
| assess persistente (só HRV) | 5% | 8% | 10% | 13% |
| assess pontual (qualquer dos 7) | 63% | 80% | 89% | 96% |
| tendência (Theil–Sen+MK, 28 d) | 58% | 67% | 74% | 82% |
| changepoint (CUSUM) | 90% | 93% | 94% | 96% |
| alerts.evaluate | 29% | 38% | 46% | 56% |
| readiness (nível ≠ normal) | 11% | 16% | 20% | 26% |
| REF a: z ≥ 2 no dia (qualquer dos 7) | 42% | 58% | 69% | 81% |
| REF a: z ≥ 2 (só HRV) | 8% | 13% | 17% | 23% |
| REF b: média 7 d vs 28 d ≥ 1 dp (qualquer dos 7) | 54% | 64% | 72% | 81% |
| REF b: 7 d vs 28 d (só HRV) | 12% | 16% | 19% | 25% |

Exemplo: o `assess` persistente (qualquer dos 7) detecta 59% das perturbações de 0,5 desvio, mas ativaria ao acaso em ~31–58% das janelas
de 3–10 dias sem evento; a sensibilidade "real" a esse evento fraco é pouco maior que o acaso. O `readiness` (acaso 11–26%) e o `persistente` só HRV (5–13%) são mais específicos.

### 4.3 Sensibilidade por magnitude e duração (células, 40 séries cada; `alerts`/`readiness`: 20)

Sensibilidade durante o evento. IC95% largos (±15–20 pontos) em cada célula.

| M × duração | persistente (qualquer) | persistente (só HRV) | alerts | readiness ≠ normal | readiness "normal" nos dias do evento |
|---|---|---|---|---|---|
| M=0,5 × 3 d | 28% | 8% | 30% | 25% | 85% |
| M=0,5 × 5 d | 55% | 12% | 55% | 35% | 91% |
| M=0,5 × 7 d | 70% | 28% | 60% | 55% | 82% |
| M=0,5 × 10 d | 82% | 28% | 95% | 40% | 91% |
| M=1,0 × 3 d | 35% | 5% | 45% | 45% | 75% |
| M=1,0 × 5 d | 57% | 15% | 60% | 50% | 81% |
| M=1,0 × 7 d | 72% | 40% | 65% | 70% | 64% |
| M=1,0 × 10 d | 90% | 35% | 90% | 85% | 64% |
| M=1,5 × 3 d | 62% | 18% | 65% | 65% | 70% |
| M=1,5 × 5 d | 85% | 30% | 85% | 80% | 43% |
| M=1,5 × 7 d | 82% | 42% | 85% | 85% | 53% |
| M=1,5 × 10 d | 100% | 70% | 95% | 90% | 40% |
| M=2,0 × 3 d | 68% | 18% | 70% | 80% | 47% |
| M=2,0 × 5 d | 98% | 42% | 100% | 90% | 31% |
| M=2,0 × 7 d | 92% | 52% | 95% | 85% | 39% |
| M=2,0 × 10 d | 98% | 70% | 95% | 95% | 42% |

Perturbações de 3 dias ativam o padrão **persistente** (que por construção exige ≥ 4 dos últimos 7 dias fora da faixa e média de 7 dias ≥ 1,25 desvio) em só 28–68% das séries
(qualquer indicador) e 5–18% (só HRV), mesmo com até 2 desvios simultâneos em todos os indicadores — e esse nível inclui o acaso de ~31% da seção 4.2. Isso é coerente com o desenho ("persistente"), mas significa que perturbações curtas dificilmente geram alerta específico.

### 4.4 Mudança abrupta persistente (degrau) e deriva gradual

Sensibilidade em 14 d (degrau) ou 30 d (deriva) e atraso mediano até o fim da janela (dias).

| Detector | degrau M=0,5 | degrau M=1 | degrau M=2 | atraso M=1 | deriva M=0,5 | deriva M=1 | deriva M=2 | atraso M=1 |
|---|---|---|---|---|---|---|---|---|
| assess persistente (qualquer dos 7) | 95% (88%–98%) | 95% (88%–98%) | 99% (93%–100%) | 3 | 96% (90%–99%) | 98% (91%–99%) | 100% (95%–100%) | 6 |
| assess persistente (só HRV) | 32% (23%–43%) | 57% (47%–68%) | 81% (71%–88%) | 7 | 34% (24%–45%) | 51% (40%–62%) | 62% (52%–72%) | 24 |
| assess pontual (qualquer dos 7) | 100% (95%–100%) | 100% (95%–100%) | 100% (95%–100%) | 0 | 100% (95%–100%) | 100% (95%–100%) | 100% (95%–100%) | 2 |
| tendência (Theil–Sen+MK, 28 d) | 96% (90%–99%) | 100% (95%–100%) | 100% (95%–100%) | 0 | 98% (91%–99%) | 100% (95%–100%) | 100% (95%–100%) | 0 |
| changepoint (CUSUM) | 100% (95%–100%) | 100% (95%–100%) | 100% (95%–100%) | 0 | 100% (95%–100%) | 100% (95%–100%) | 100% (95%–100%) | 0 |
| alerts.evaluate | 73% (56%–86%) | 90% (74%–97%) | 100% (89%–100%) | 2 | 93% (79%–98%) | 97% (83%–99%) | 100% (89%–100%) | 8,5 |
| readiness (nível ≠ normal) | 47% (30%–64%) | 80% (63%–90%) | 97% (83%–99%) | 3 | 73% (56%–86%) | 80% (63%–90%) | 93% (79%–98%) | 14 |
| REF a: z ≥ 2 no dia (qualquer dos 7) | 98% (91%–99%) | 99% (93%–100%) | 100% (95%–100%) | 1 | 99% (93%–100%) | 100% (95%–100%) | 100% (95%–100%) | 3 |
| REF a: z ≥ 2 (só HRV) | 46% (36%–57%) | 59% (48%–69%) | 82% (73%–89%) | 7 | 61% (50%–71%) | 70% (59%–79%) | 75% (65%–83%) | 18,5 |
| REF b: média 7 d vs 28 d ≥ 1 dp (qualquer dos 7) | 98% (91%–99%) | 100% (95%–100%) | 100% (95%–100%) | 0 | 100% (95%–100%) | 100% (95%–100%) | 100% (95%–100%) | 0 |
| REF b: 7 d vs 28 d (só HRV) | 52% (42%–63%) | 68% (57%–77%) | 94% (86%–97%) | 7 | 64% (53%–73%) | 71% (61%–80%) | 78% (67%–85%) | 18,5 |

Com mudanças persistentes **de todos os indicadores juntos**, quase todo detector dispara; o que diferencia é o falso alarme (seção 3) e o atraso. O `changepoint` e a `tendência` ativam em
praticamente toda série (inclusive antes do evento), então a "sensibilidade" é pouco informativa. O `readiness` demora 3 dias (degrau 1 dp) a 14 dias (deriva) para sair de "normal".
O padrão persistente só HRV detecta, em 30 dias, 51% das derivas de 1 desvio, contra 71% da referência (b) só HRV.

### 4.5 `readiness`: recomendação incorreta

| Situação | Medida | Resultado |
|---|---|---|
| Sem evento (série realista, 60 séries × 90 d) | dias com nível ≠ normal (reduzir sem motivo) | 4,9% (4,2%–5,7%) |
| Sem evento | janelas de 30 d com ≥1 dia reduzido | 63% (56%–71%) |
| Perturbação M=0,5 | dias do evento com "normal" | 88% (83%–92%) |
| Perturbação M=1 | idem | 69% (61%–77%) |
| Perturbação M=1,5 | idem | 48% (39%–56%) |
| Perturbação M=2 | idem | 39% (32%–47%) |
| Perturbação M=2 | séries em que **nenhum** dia do evento saiu de "normal" | 12% (7%–22%) |

Sem check-in subjetivo, e sem sintomas (o que seria o caso mais frequente em leve perturbação), o nível "parar" é inalcançável e "recuperação" é raríssimo; o `readiness` age como filtro conservador.
Esta simulação **não** diz se reduzir o treino nesses dias seria benéfico, só se a regra do código reage ao padrão simulado.

## 5. Comparação com os métodos simples (onde o sistema é pior)

Mesma definição de evento e mesmos dados; "FA/dia" é a taxa na série realista sem evento. Critério: **sistema pior** = a referência tem sensibilidade ≥ e falso alarme ≤ (com ao menos
um estritamente melhor); "distinguível" = os ICs95% não se sobrepõem na dimensão em que a referência ganha. Lista completa em `resultados.json › comparacoes` (84 linhas; 53 são "compromisso").

**Casos em que o sistema é pior que a referência simples e a diferença é distinguível (ICs não se sobrepõem):**

| Evento | Detector do sistema | Referência | Sens. sistema | Sens. ref. | FA/dia sistema | FA/dia ref. |
|---|---|---|---|---|---|---|
| perturbação M=0.5 (durações 3-10 d) | assess persistente (qualquer dos 7) | REF a: z ≥ 2 no dia (qualquer dos 7) | 59% | 79% | 19,5% | 18,2% |
| perturbação M=1.0 (durações 3-10 d) | assess persistente (qualquer dos 7) | REF a: z ≥ 2 no dia (qualquer dos 7) | 64% | 89% | 19,5% | 18,2% |
| perturbação M=1.0 (durações 3-10 d) | assess persistente (só HRV) | REF a: z ≥ 2 (só HRV) | 24% | 39% | 3,2% | 3,0% |
| perturbação M=1.0 (durações 3-10 d) | tendência (Theil–Sen+MK, 28 d) | REF a: z ≥ 2 no dia (qualquer dos 7) | 82% | 89% | 46,5% | 18,2% |
| perturbação M=1.5 (durações 3-10 d) | assess persistente (qualquer dos 7) | REF a: z ≥ 2 no dia (qualquer dos 7) | 82% | 98% | 19,5% | 18,2% |
| perturbação M=1.5 (durações 3-10 d) | assess persistente (só HRV) | REF a: z ≥ 2 (só HRV) | 40% | 57% | 3,2% | 3,0% |
| perturbação M=1.5 (durações 3-10 d) | tendência (Theil–Sen+MK, 28 d) | REF a: z ≥ 2 no dia (qualquer dos 7) | 91% | 98% | 46,5% | 18,2% |
| perturbação M=1.5 (durações 3-10 d) | tendência (Theil–Sen+MK, 28 d) | REF b: média 7 d vs 28 d ≥ 1 dp (qualquer dos 7) | 91% | 94% | 46,5% | 41,1% |
| perturbação M=2.0 (durações 3-10 d) | assess pontual (qualquer dos 7) | REF a: z ≥ 2 no dia (qualquer dos 7) | 96% | 96% | 32,8% | 18,2% |
| perturbação M=2.0 (durações 3-10 d) | tendência (Theil–Sen+MK, 28 d) | REF a: z ≥ 2 no dia (qualquer dos 7) | 91% | 96% | 46,5% | 18,2% |
| perturbação M=2.0 (durações 3-10 d) | tendência (Theil–Sen+MK, 28 d) | REF b: média 7 d vs 28 d ≥ 1 dp (qualquer dos 7) | 91% | 94% | 46,5% | 41,1% |
| degrau abrupto M=1.0 (detectado em 14 d) | tendência (Theil–Sen+MK, 28 d) | REF b: média 7 d vs 28 d ≥ 1 dp (qualquer dos 7) | 100% | 100% | 46,5% | 41,1% |
| degrau abrupto M=1.0 (detectado em 14 d) | changepoint (CUSUM) | REF b: média 7 d vs 28 d ≥ 1 dp (qualquer dos 7) | 100% | 100% | 86,5% | 41,1% |
| deriva 60 d M=1.0 (detectada em 30 d) | assess pontual (qualquer dos 7) | REF a: z ≥ 2 no dia (qualquer dos 7) | 100% | 100% | 32,8% | 18,2% |
| deriva 60 d M=1.0 (detectada em 30 d) | tendência (Theil–Sen+MK, 28 d) | REF a: z ≥ 2 no dia (qualquer dos 7) | 100% | 100% | 46,5% | 18,2% |
| deriva 60 d M=1.0 (detectada em 30 d) | tendência (Theil–Sen+MK, 28 d) | REF b: média 7 d vs 28 d ≥ 1 dp (qualquer dos 7) | 100% | 100% | 46,5% | 41,1% |
| deriva 60 d M=1.0 (detectada em 30 d) | changepoint (CUSUM) | REF a: z ≥ 2 no dia (qualquer dos 7) | 100% | 100% | 86,5% | 18,2% |
| deriva 60 d M=1.0 (detectada em 30 d) | changepoint (CUSUM) | REF b: média 7 d vs 28 d ≥ 1 dp (qualquer dos 7) | 100% | 100% | 86,5% | 41,1% |

Resumo dos achados da comparação:

1. **`assess` persistente vs z ≥ 2 no dia:** o persistente é menos sensível (M=1: 64% vs 89%; M=2: 89% vs 96%) com falso alarme por dia
   semelhante (19,5% vs 18,2%). Em "só HRV", idem (40% vs 57% em M=1,5, FA 3,2% vs 3,0%).
   O que o persistente compra é **estabilidade** (virada de estado 12%/dia vs 26%/dia) e a semântica "padrão mantido", mas **não** menos falsos alarmes por dia.
   Em mudanças persistentes (degrau/deriva) o persistente é mais lento que a referência (b) em qualquer-dos-7 (atraso mediano 3 d vs 0 d no degrau de 1 dp).
2. **`tendência` (Theil–Sen + Mann–Kendall):** falso alarme de 46% dos dias, mais que o dobro do z ≥ 2 (18%) e acima da referência (b) (41%); a sensibilidade é igual ou **menor** (M ≥ 1 vs z ≥ 2 e M ≥ 1,5 vs (b), diferença distinguível): é pior que as duas referências nas perturbações, e no degrau/deriva (b) tem atraso igual ou menor com menos falsos alarmes.
3. **`changepoint`:** 86% de falso alarme por dia. Nas perturbações é um "compromisso" apenas formal (sensibilidade 96–99%, mas a chance de ativar ao acaso numa janela de 3 dias já é 90%: não discrimina). No degrau e na deriva é **dominado** pelas duas referências (mesma sensibilidade, falso alarme 2–5 vezes maior; diferença distinguível).
4. **`alerts.evaluate`** e **`readiness`** não são dominados no critério estrito, mas: o `alerts` tem falso alarme por dia praticamente **igual** ao z ≥ 2 em qualquer-dos-7 (18,1% vs 18,2%) com sensibilidade claramente **menor** (M=1: 65% vs 89%; M=2: 90% vs 96%) — na prática pior que a referência (a) em eventos curtos (a vantagem é a persistência/estabilidade: 12% vs 26% de viradas por dia). O `readiness` troca sensibilidade por especificidade (FA 5% vs 18%; sensibilidade M=1 62% vs 89%): legítimo, mas a comparação não é feita no mesmo ponto de operação.
5. **`assess` pontual:** melhor que a referência (b) (mais sensível e menos falso alarme), pior que z ≥ 2 (FA 33% vs 18% com a mesma sensibilidade): o limiar de 1,5 desvio em 7 indicadores ativa quase um dia em três.

Aviso: comparar detectores em pontos de operação diferentes (FA/dia de 5% a 87%) é limitado; uma comparação justa exigiria curvas ROC por detector, que não foram feitas.

## 6. Defeitos e achados

1. **DEFEITO — `healthmon.stats.changepoint` descalibrado sob autocorrelação.** CUSUM com k = 0,5 e h = 5 supõe ruído independente. Em ruído i.i.d. já reporta mudança em ~20% dos dias para **um** indicador
   (medido à parte, 40 séries i.i.d.; φ = 0: 20%, φ = 0,3: 46%, φ = 0,5: 64%, φ = 0,7: 75%); com 7 indicadores e séries realistas, 86% dos dias. A janela móvel de 60 dias com referência fixa nos 28 primeiros dias
   e o retorno "última mudança encontrada" fazem o estado persistir por semanas. O teste `test_changepoint_calibrado_sob_ruido_independente` documenta isso como `xfail(strict=True)`; não foi ajustado.
2. **`trend`: Mann–Kendall sem correção de autocorrelação** (falso alarme 46%/dia em qualquer dos 7 indicadores).
3. **Agregação "qualquer dos 7":** o padrão persistente/alertas fica ativo em ~19% dos dias sem evento. Nesta simulação (AR(1) com φ 0,3–0,7 e dia da semana) o limiar Z_PERSIST = 1,25 do `analysis.py` não mantém o falso alarme em "0–3%" citado no comentário do código para ruído independente; o comentário já adverte que com autocorrelação sobe para ~10–14% por indicador. Aqui medimos 3,2% só para o HRV, e a taxa agregada cresce com o número de indicadores olhados (comparações múltiplas).
4. **Perturbações curtas (3–5 dias) geram alerta persistente com baixa probabilidade** (seção 4.3; só HRV: 5–42%). É um compromisso do desenho, mas deve ser comunicado.
5. **Leituras travadas e valores absurdos isolados:** não elevam o falso alarme persistente (cenário "extremos" tem FA menor que o realista: 16% vs 19%); absurdos podem inflar o desvio da linha de base e reduzir a sensibilidade (hipótese; não medida). Nenhuma validação de plausibilidade fisiológica é feita antes de `assess` (um HRV = 400 ms entra no cálculo). 
6. **Mudanças de rotina** (cenário "rotina": passos/sono por 10–21 dias) elevam o persistente (qualquer) de 19% para 22% dos dias, e a `tendência` de 46% para 53%; o `alerts` não muda (os alertas olham HRV, FC, estresse, sono, mas passos não entram).

## 7. Recomendações (na medição original não foram aplicadas; ver seção 10 para o que foi feito depois)

Qualquer mudança abaixo deve ser reavaliada com este mesmo simulador (e depois com dados reais do próprio usuário, sob supervisão) antes de adotada.

1. `changepoint`: recalibrar h/k para a autocorrelação (ou aplicar o CUSUM a médias semanais / resíduos pré-branqueados), calcular o desvio de referência com correção de autocorrelação, e retornar só mudança **recente** (por ex., detectada nos últimos 7 dias) em vez da última da janela. Meta de verificação: FA/dia < 5% em séries sem evento.
2. `trend`: usar Mann–Kendall com correção de autocorrelação (Hamed–Rao/pré-branqueamento) ou testar em médias semanais; exigir `variacao_pct` maior que 3%; reduzir a taxa de 46% de dias com "tendência desfavorável".
3. Persistente "qualquer indicador": avaliar exigir concordância de ≥ 2 grupos independentes (como o `readiness` já faz: 5% de FA) ou controle de comparações múltiplas antes de alertar; hoje 94% das janelas de 30 d teriam algum alerta sem evento (sem cooldown).
4. Persistente só HRV: como z ≥ 2 e média 7 d vs 28 d têm FA igual ou menor e sensibilidade maior, vale testar um critério de persistência mais curto (por exemplo ≥ 3 de 5 dias) em simulação e medir a troca falso alarme × sensibilidade; não recomendo mudar sem essa medição, nem otimizar só o falso alarme.
5. `readiness`: documentar que, sem check-in, mantém "normal" em ~40% dos dias de perturbação de 2 desvios e ~70% com 1 desvio (e que isso é o preço dos 5% de falso alarme). A UI deve evitar sugerir que "normal" significa "recuperado".
6. Implausibilidade: descartar/marcar valores fisiologicamente impossíveis antes das estatísticas (ou usar o piso/teto de plausibilidade) — medir o efeito em eventos.

## 8. Limitações

- **Simulação ≠ realidade.** A estrutura do simulador (AR(1), efeitos aditivos, fator comum, eventos retangulares que movem todos os indicadores juntos) foi escolhida por nós; o sistema pode se comportar diferente com dados reais (sazonalidade, treino, ciclo menstrual, álcool, calibração do sensor, mudanças de firmware).
- **Os eventos movem todos os indicadores juntos e na direção "esperada":** isso favorece detectores do tipo "qualquer indicador" e o `readiness`, que exige convergência. Com perturbação em um só indicador, os resultados seriam piores (veja "só HRV").
- **Rótulo de "evento" é nosso.** Não sabemos se doença leve real tem esse perfil, nem se detectá-la ou reduzir o treino traz benefício. Sensibilidade/especificidade aqui não são desempenho diagnóstico e não comprovam utilidade clínica.
- Os falsos alarmes "por dia" dentro de uma série são correlacionados; por isso os ICs usam bootstrap por série. Sensibilidades usam Wilson por série (independentes).
- `alerts`/`readiness` rodam em subconjuntos (20–60 séries por cenário/célula) por custo de tempo; ICs mais largos. Células de magnitude × duração têm só 40 séries.
- O `alerts.evaluate` foi avaliado com *cooldown* = 0 e sem `max_alertas_dia`; contagens reais de notificação serão menores.
- Nenhum limiar de produção foi alterado e nada foi "ajustado para melhorar números".
- Os dois conjuntos de sementes são independentes, mas o mesmo código de simulador foi usado em ambos; não mede generalização para outro gerador.

## 9. Reprodução

```
. .venv/bin/activate
python -m validacao.avaliar --conjunto avaliacao --saida validacao/resultados.json   # ~5 min (4 processos)
python -m pytest tests/test_validacao_estatistica.py                                  # < 20 s, versão reduzida
HEALTHMON_VAL_CONJUNTO=desenvolvimento python -m pytest tests/test_validacao_estatistica.py   # usa o conjunto de desenvolvimento
```

As faixas do teste de CI (falso alarme persistente ≤ 33%, só HRV ≤ 12%, alerts ≤ 30%; `readiness` fora de normal sem evento ≤ 18%; sensibilidade 2 dp × 10 d ≥ 75% (qualquer), ≥ 30% (só HRV),
≥ 70% (alerts); `readiness` "normal" ≤ 50% dos dias do evento; atraso mediano ≤ 3 d) foram escolhidas a partir do conjunto de **desenvolvimento** com folga larga: são travas de regressão,
**não** metas de qualidade — vários desses valores são altos de propósito para refletir o comportamento atual.

## 10. Correções e nova medição (SIMULAÇÃO com dados sintéticos; não é validação com dados reais)

Tudo abaixo vem do mesmo simulador, com séries sintéticas. Não prova utilidade clínica nem comportamento com dados reais.
Os números das seções 1–9 são a medição original e não foram alterados.

### 10.1 O que foi corrigido (e o que não foi)

| Defeito (seção 6) | Correção | Situação |
|---|---|---|
| 1. `changepoint` descalibrado | `healthmon/stats.py`: z robusto pré-branqueado por AR(1) (φ estimado na referência, limitado a [0,3; 0,8], só pares de dias consecutivos); z winsorizado em ±3; k = 0,35, h = 8 (antes 0,5 e 5); CUSUM limitado a 1,5·h e resultado = mudança **em curso** (antes: "a última encontrada na janela", que ficava por semanas) | tratado; k/h escolhidos no conjunto de desenvolvimento |
| 2. `trend` sem correção de autocorrelação | `healthmon/analysis.py`: variância de Mann–Kendall com correção de empates e fator de autocorrelação (Hamed–Rao só com lag 1, com correção de viés, nunca < 1); limiar p < 0,01 (antes 0,05); `variacao_pct` ≥ 3% mantido | tratado |
| 3. Agregação "qualquer dos 7" | só no **alerta** (`healthmon/alerts.py`): indicador isolado exige ≥ 5 de 7 dias fora da faixa (`persistente_isolado`); o alerta composto de recuperação exige sinais de ≥ 2 grupos fisiológicos (autonômico, sono, energia, estresse) em 3 dos últimos 4 dias | tratado parcialmente (ver abaixo) |
| 5/recomendação 6. Implausibilidade | `Store.value` descarta valores fora de limites fisiológicos amplos (os de `parse.PLAUSIBLE` + estresse do sono 1–100); tratados como ausentes | tratado |
| 4. Perturbações curtas | não alterado | é um compromisso de desenho; deve ser comunicado |

**Não alterado de propósito:** (a) o padrão `persistente` de `assess` (4 de 7 dias, média 7 d ≥ 1,25 dp) continua como estava: é o estado exibido ao usuário e mudar seu significado
sem nova rodada de medição não é conservador; seu falso alarme "qualquer dos 7" segue ~21–23% por dia (abaixo). (b) `readiness`: sem alteração. (c) Nada foi ajustado depois de olhar o conjunto de confirmação.

### 10.2 Protocolo

- Ajustes de k, h, φ, p-limite e do critério 5/7 foram feitos **só** no conjunto `desenvolvimento` (sementes 10.000+). O conjunto de `avaliacao` (seções 1–9) já tinha sido usado e não foi reutilizado para decidir.
- Novo conjunto `confirmacao` (sementes a partir de 20.000.000, disjunto dos anteriores; há teste de disjunção). Mesmos tamanhos da avaliação (1.920 séries de 180 dias; detectores caros em subconjuntos de 20–60 séries).
- Os arquivos de saída em disco mostram **uma execução por versão do código** no conjunto de confirmação (antes: código de HEAD para `stats.py`, `analysis.py`, `alerts.py`; depois: código corrigido), cada uma com ~5 min,
  executadas antes desta etapa de documentação, com os três arquivos de detectores sem alteração desde então (data de modificação anterior às execuções). Esta etapa **não** executou o conjunto de novo nem alterou detectores depois de ver os números.
  Limite: a prova é circunstancial (datas de arquivos e meta do JSON), não um registro assinado.
- "Antes" = código original medido **no mesmo conjunto de confirmação** (comparação justa; os números originais da seção 3, no conjunto de avaliação, são parecidos: changepoint 86,5%, tendência 46,5%, alerts 18,1%).
- Números brutos: `validacao/resultados.json › correcoes_confirmacao` (chave nova; as antigas não foram tocadas).
- Os mesmos IC: Wilson para sensibilidades, bootstrap por série para taxas por dia. "Antes" e "depois" são **conjuntos de sementes iguais**, então a diferença entre as colunas não é ruído de sorteio das séries.

### 10.3 Falso alarme e estabilidade (série realista sem evento, confirmação)

| Detector | FA/dia antes | FA/dia depois | ≥1 ativação em 30 d (antes → depois) | viradas de estado/dia (antes → depois) |
|---|---|---|---|---|
| changepoint (qualquer dos 7) | 87,3% (85,7%–88,7%) | **12,0% (10,6%–13,4%)** | 100% → 60% | 4,7% → 7,3% |
| changepoint (só HRV) | 27,8% (25,7%–29,8%) | 1,8% (1,3%–2,4%) | 67% → 14% | 4,4% → 1,3% |
| tendência (qualquer dos 7) | 48,0% (46,5%–49,4%) | **13,4% (12,3%–14,5%)** | 99% → 72% | 12,8% → 6,7% |
| tendência (só HRV) | 11,2% (10,1%–12,4%) | 2,4% (1,8%–2,9%) | 58% → 20% | 4,3% → 1,4% |
| alerts.evaluate (qualquer) | 18,8% (16,9%–20,8%) | **15,4% (13,7%–17,1%)** | 94% → 87% | 12,4% → 11,0% |
| alerts: só indicadores isolados | 16,1% (14,3%–17,9%) | 13,1% (11,7%–14,8%) | 88% → 81% | 10,7% → 9,1% |
| alerts: só composto (recuperação) | 5,9% (5,1%–6,9%) | 4,1% (3,4%–5,0%) | 60% → 53% | 6,1% → 4,6% |
| assess persistente (qualquer dos 7), não alterado | 20,9% (19,9%–22,1%) | 23,1% (22,1%–24,2%) | 94% → 94% | 12,6% → 13,2% |
| assess persistente (só HRV), não alterado | 3,3% (2,8%–3,7%) | 3,9% (3,4%–4,3%) | 34% → 38% | 2,8% → 3,2% |
| readiness (nível ≠ normal), não alterado | 5,3% (4,4%–6,2%) | 6,7% (5,9%–7,7%) | 63% → 75% | 7,0% → 8,9% |
| REF a: z ≥ 2 no dia (qualquer) | 18,7% (18,1%–19,3%) | 19,5% (18,9%–20,1%) | 100% → 100% | 26,0% → 26,3% |
| REF a: z ≥ 2 (só HRV) | 3,1% (2,9%–3,4%) | 3,6% (3,3%–3,8%) | 59% → 65% | 5,3% → 5,9% |
| REF b: 7 d vs 28 d (qualquer) | 41,9% (40,7%–43,3%) | 41,7% (40,6%–43,0%) | 100% → 100% | 14,1% → 14,2% |
| REF b: 7 d vs 28 d (só HRV) | 8,6% (7,8%–9,4%) | 9,7% (8,9%–10,5%) | 56% → 62% | 4,3% → 4,8% |

Em ruído i.i.d. de um indicador, o `changepoint` agora dispara em < 5% dos dias (teste automatizado; antes ~20%).

**Leitura honesta:** `changepoint` e `tendência` saíram de "ruído quase permanente" para ~12–13% por dia em qualquer dos 7 (e 2% só HRV), abaixo da referência z ≥ 2 (19,5%) e muito abaixo da referência (b) (41,7%). Isso **ainda não** atinge a meta de < 5%/dia da recomendação 1
para "qualquer dos 7" (a meta é atingida só HRV e em um indicador i.i.d.), e em 30 dias 60–72% das janelas ainda têm algum dia ativo. O `alerts` melhorou pouco (18,8% → 15,4%): seguem ~87% das janelas de 30 dias com algum alerta (sem cooldown).

**Efeito colateral do filtro de plausibilidade (piora):** nos detectores que não foram alterados (`assess` persistente, `readiness`, e também as referências z ≥ 2/(b), que leem o mesmo `Store`), o falso alarme **subiu** um pouco com o descarte de valores absurdos:
persistente qualquer 20,9% → 23,1% (ICs quase encostam), readiness 5,3% → 6,7% (ICs se sobrepõem em parte; janelas de 30 d com ≥1 dia reduzido 63% → 75%), referência z ≥ 2 18,7% → 19,5%. Hipótese **não testada**: sem os absurdos a base fica mais estreita e dias normais passam a parecer mais extremos.
O filtro continua correto (um HRV de 400 ms não pode entrar no cálculo), mas o custo existe e não foi investigado.

### 10.4 Sensibilidade e atraso (confirmação)

Perturbação de 3–10 dias em todos os indicadores, durante o evento (IC95% Wilson; 160 séries por magnitude; `alerts`/`readiness`: 80). Atraso = mediana em dias (antes → depois).

| Detector | 1 dp antes | 1 dp depois | 2 dp antes | 2 dp depois | atraso 1 dp | atraso 2 dp |
|---|---|---|---|---|---|---|
| changepoint (qualquer dos 7) | 97% (93%–99%) | **43% (36%–51%)** | 100% (98%–100%) | **73% (66%–79%)** | 0 → 1 | 0 → 1 |
| changepoint (só HRV) | 56% (48%–63%) | **16% (11%–22%)** | 69% (62%–76%) | **26% (19%–33%)** | 0 → 2 | 1 → 5 |
| tendência (qualquer dos 7) | 88% (81%–92%) | **49% (41%–56%)** | 92% (87%–96%) | **63% (55%–70%)** | 0 → 1 | 0 → 1 |
| tendência (só HRV) | 36% (29%–44%) | **16% (11%–22%)** | 57% (49%–64%) | **23% (17%–30%)** | 2 → 3 | 2 → 5 |
| alerts.evaluate (qualquer) | 66% (55%–76%) | 69% (58%–78%) | 94% (86%–97%) | 91% (83%–96%) | 2 → 2 | 1 → 1 |
| alerts: só composto | 50% (39%–61%) | 45% (35%–56%) | 92% (85%–97%) | 90% (81%–95%) | 2 → 2,5 | 2 → 2 |
| assess persistente (qualquer), não alterado | 75% (68%–81%) | 79% (72%–85%) | 89% (83%–93%) | 90% (84%–94%) | 1 → 1 | 1 → 1 |
| assess persistente (só HRV), não alterado | 29% (22%–36%) | 31% (24%–38%) | 54% (46%–61%) | 57% (50%–65%) | 2 → 2 | 3 → 3 |
| readiness (≠ normal), não alterado | 54% (43%–64%) | 65% (54%–75%) | 94% (86%–97%) | 94% (86%–97%) | 1 → 1 | 0 → 0 |
| REF a: z ≥ 2 (qualquer) | 90% (84%–94%) | 92% (87%–95%) | 98% (94%–99%) | 98% (95%–99%) | 0 → 0 | 0 → 0 |
| REF a: z ≥ 2 (só HRV) | 39% (32%–47%) | 41% (33%–48%) | 70% (62%–77%) | 76% (69%–82%) | 1 → 1 | 0 → 0 |
| REF b: 7 d vs 28 d (qualquer) | 82% (76%–88%) | 88% (81%–92%) | 95% (90%–97%) | 96% (92%–98%) | 0 → 0 | 0 → 0 |
| REF b: 7 d vs 28 d (só HRV) | 46% (38%–53%) | 49% (41%–56%) | 65% (57%–72%) | 69% (62%–76%) | 2 → 2 | 2 → 2 |

Degrau persistente e deriva gradual de 1 dp (sensibilidade em 14 d / 30 d; antes → depois):

| Detector | degrau 1 dp | deriva 1 dp | atraso mediano do degrau |
|---|---|---|---|
| changepoint (qualquer) | 100% → **71%** | 100% → **80%** | 0 → 4 |
| changepoint (só HRV) | 74% → **15%** | 79% → **22%** | 1 → 10,5 |
| tendência (qualquer) | 100% → **86%** | 100% → **71%** | 0,5 → 4 |
| tendência (só HRV) | 78% → **38%** | 62% → **12%** | 5 → 6,5 |
| alerts.evaluate | 100% → 100% | 93% → 93% | 2 → 2 |
| alerts: só composto | 83% → 83% | 80% → **67%** | 3 → 4 |
| assess persistente (qualquer) / só HRV | 100% → 100% / 49% → 56% | 91% → 96% / 42% → 51% | 2 → 2 / 5 → 5 |
| readiness | 80% → 90% | 77% → 83% | 3 → 2 |
| REF a z ≥ 2 / REF b (qualquer) | 99% → 99% / 100% → 100% | 100% → 100% / 100% → 99% | 1 → 0 / 0 → 0 |

**Onde a sensibilidade piorou (com diferença distinguível, ICs que não se sobrepõem):**
- `changepoint`: 97% → 43% (1 dp) e 100% → 73% (2 dp) em perturbações curtas; só HRV 56% → 16%. No degrau persistente de 1 dp: 100% → 71% (só HRV 74% → 15%) e atraso mediano de 0 → 4 dias (só HRV 1 → 10,5).
- `tendência`: 88% → 49% (1 dp) e 92% → 63% (2 dp); só HRV 36% → 16% e 57% → 23%. Deriva de 1 dp: 100% → 71% (só HRV 62% → 12%).
- `alerts.evaluate`: sem piora distinguível nas perturbações (66% → 69% em 1 dp; 94% → 91% em 2 dp); o alerta composto perdeu sensibilidade na deriva (80% → 67%, ICs largos, 30 séries) e levou 1 dia a mais no degrau (3 → 4).
- Boa parte da queda do `changepoint`/`tendência` é o preço da calibração: parte da "sensibilidade" original era acaso (a chance de ativar em 3 dias sem evento era 91% no changepoint e 60% na tendência; agora 18% e 20%). Ainda assim, a sensibilidade corrigida é **bem menor** que a das referências simples (z ≥ 2: 92% em 1 dp; (b): 88%), com falso alarme menor (12–13% vs 19,5%/41,7%): troca de sensibilidade por especificidade, não ganho nos dois lados.

### 10.5 Comparação com as duas referências simples (confirmação)

Dos 156 pares evento × detector × referência: antes, 46 "sistema pior" (39 com diferença distinguível), 99 compromisso, 11 "sistema melhor"; depois, 13 "sistema pior" (9 distinguíveis), 132 compromisso, 11 "melhor".
O `changepoint` e a `tendência` deixaram de ser **dominados** pelas referências (antes: mesma sensibilidade com 2–5 vezes mais falso alarme no degrau/deriva), mas passaram a "compromisso": menos falso alarme e muito menos sensibilidade.
Os "sistema pior" restantes são do `assess` (persistente qualquer e só HRV, e pontual) contra z ≥ 2, **sem mudança** em relação à seção 5: persistente com sensibilidade menor (ex.: 1 dp: 79% vs 92%) e falso alarme igual ou maior (23,1% vs 19,5%).
A tendência e o changepoint "só HRV" têm, em 1 dp, sensibilidade de 16% contra 41% (z ≥ 2 só HRV) com falso alarme 2% vs 4%.

### 10.6 `readiness`: recomendação incorreta (confirmação, antes → depois; código do readiness não alterado)

| Situação | antes | depois |
|---|---|---|
| Sem evento: dias com nível ≠ normal (reduzir sem motivo) | 5,3% (4,4%–6,2%) | 6,7% (5,9%–7,7%) |
| Sem evento: janelas de 30 d com ≥ 1 dia reduzido | 63% | 75% |
| M = 0,5: dias do evento mantidos em "normal" | 86,4% (81,0%–91,1%) | 83,6% (77,0%–89,5%) |
| M = 1: idem | 72,4% (65,5%–79,0%) | 66,8% (59,7%–73,1%) |
| M = 1,5: idem | 41,8% (34,3%–49,2%) | 34,4% (27,2%–41,4%) |
| M = 2: idem | 24,6% (19,4%–30,6%) | 20,0% (14,7%–25,9%) |
| M = 2: séries sem nenhum dia reduzido | 6% | 6% |

A mudança do `readiness` vem só do filtro de plausibilidade (entrada): mais recomendações de reduzir sem evento (piora) e menos "normal" nos dias de evento (melhora), efeitos moderados e ICs que se sobrepõem em parte.
A recomendação 5 da seção 7 continua válida: "normal" não significa "recuperado".

### 10.7 Limitações desta rodada

- Detectores caros (`alerts`, `readiness`) em 20–60 séries por cenário; ICs largos, sobretudo nas células de degrau/deriva (30 séries).
- A calibração (k, h, p, 5/7) foi feita num só gerador; não mede generalização para outro gerador nem para dados reais.
- Nenhuma meta foi "atingida" por ajuste fino: ao contrário, o falso alarme de `changepoint`/`tendência` em "qualquer dos 7" (12–13%) e do `alerts` (15%) continua alto, e o preço foi sensibilidade.
- O assess persistente "qualquer dos 7" (23%) não foi corrigido; uma nova rodada precisa de um conjunto de sementes novo (o de confirmação já foi consumido).
- O ganho de falso alarme e a perda de sensibilidade aqui descritos valem para o simulador (eventos que movem todos os indicadores ao mesmo tempo); com perturbações reais pode ser diferente.

### 10.8 Reprodução

```
. .venv/bin/activate
python -m validacao.avaliar --conjunto confirmacao --saida /caminho/confirmacao.json   # ~5 min (4 processos); gerar o "antes" em cópia do repositório com stats/analysis/alerts de HEAD
python -m pytest tests/test_validacao_estatistica.py                                   # < 20 s, versão reduzida (inclui negativos e cálculo à mão de Mann–Kendall)
```
