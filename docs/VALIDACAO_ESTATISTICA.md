# Validação estatística por SIMULAÇÃO dos detectores do healthmon

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

## 7. Recomendações (NÃO aplicadas; esta tarefa só mede)

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
