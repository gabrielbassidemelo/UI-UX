# Metodologia de análise

Todas as análises comparam você **com você mesmo**. Nenhum limite clínico fixo é usado para gerar
alertas. Medições de vestíveis são estimativas: não são exames clínicos e não servem para diagnóstico.

## Estágios de calibração (parâmetros operacionais)
O sistema conta **dias válidos**: dias com pelo menos dois dos quatro indicadores centrais (sono, FC de repouso,
HRV da noite e passos). Histórico importado também conta. O estágio depende dessa quantidade e da cobertura
recente, não só do calendário.

| Estágio | Dias válidos | O que o sistema pode fazer |
|---|---|---|
| Observação | Menos de 7 | Relatórios descritivos e orientações gerais de baixo risco |
| Linha de base | 7 a 27 | Alertas de tendência; redução de treino por recuperação. Não aumenta carga |
| Personalizado | 28 a 89 | Progressão de carga, calibração metabólica e análise de tendências |
| Longitudinal | 90 ou mais | Comparações de longo prazo e avaliação das recomendações |

- Os limites de 7, 28 e 90 dias são **parâmetros operacionais iniciais**, não garantias de validade científica.
  Eles dependem da quantidade, da qualidade e da representatividade dos dados.
- Com menos de 8 dias válidos nas últimas duas semanas, o sistema volta ao estágio de linha de base até a coleta
  se regularizar.
- A regra de dias válidos e os limites estão em `healthmon/calibration.py`. O painel mostra o estágio no
  cartão **Calibração** da tela Hoje.

## Linha de base individual
- Janela: os 28 dias **anteriores** ao dia analisado (configurável), sem incluir o próprio dia.
- Centro: mediana. Dispersão: desvio robusto = máx(1,4826 × MAD; 0,85 × desvio-padrão).
  A MAD resiste a valores extremos (Leys et al., 2013); o piso de 0,85 × DP evita subestimar a dispersão
  com amostras pequenas ou valores inteiros (FC, pontuações).
- HRV é analisada em escala logarítmica, prática usual no monitoramento de HRV (Plews et al., 2013).
- A base só é considerada confiável com pelo menos `[analise] minimo_dias_base` dias de dados (padrão: 10).
  Esse parâmetro é separado dos estágios de calibração acima. Antes disso o sistema avisa "construindo linha
  de base" e não emite alertas.

## Pontual × persistente
- **Faixa usual:** mediana ± 1 desvio robusto.
- **Pontual:** só o dia atual está fora (|z| ≥ 1,5).
- **Persistente:** a média dos últimos 7 dias está a ≥ 1,25 desvio da linha de base **anterior** a esses
  7 dias **e** pelo menos 4 dos 7 dias estão fora da faixa no mesmo sentido. Médias móveis de 7 dias contra
  a base individual são a abordagem recomendada para HRV (Plews et al., 2013; Buchheit, 2014).
- **Medição por simulação, não validação com dados reais.** O comportamento dos detectores foi medido com
  séries sintéticas, descritas em `docs/VALIDACAO_ESTATISTICA.md`, com os resultados e as suposições do
  simulador. Esses números não são taxa de falso alarme em uso real nem evidência de eficácia clínica. A taxa
  real só poderá ser estimada depois de meses de dados seus.

## Recuperação
Sete sinais comparados à base: HRV, FC de repouso, pontuação e duração do sono, Body Battery máximo,
prontidão para treino e estresse durante o sono.
- **Boa:** nenhum sinal desfavorável. **Moderada:** 1–2. **Atenção:** ≥ 3, ou HRV baixa junto com FC
  de repouso alta.
- **Persistente:** ≥ 2 sinais em pelo menos 3 dos últimos 4 dias. É o critério do alerta
  "recuperação insuficiente".

## Tendências
Inclinação de Theil–Sen (mediana das inclinações entre pares) e teste de Mann–Kendall, ambos robustos a
outliers. Uma tendência só é relatada com p < 0,05 e variação ≥ 3% no período.

## Associações (nunca causalidade)
Correlação de Spearman com defasagem de 1 dia quando faz sentido (ex.: carga de treino de ontem e HRV
desta noite). Exige ≥ 14 pares e p < 0,05 (aproximação de Fisher); só associações moderadas (|ρ| ≥ 0,3) ou
fortes (≥ 0,5) aparecem nos textos, sempre com o tamanho da amostra e a ressalva de que associação não é causa.

## Recomendações
Geradas por regras transparentes (`healthmon/insights.py`), limitadas a rotina, sono, descanso e
intensidade de treino. Quando há sinais persistentes, ou se houver sintomas, a orientação é procurar um
profissional de saúde. A referência de "7 h ou mais de sono para adultos" vem do consenso AASM/SRS
(Watson et al., 2015) e da NSF (Hirshkowitz et al., 2015), e é usada só como contexto, nunca como alerta.

## IA opcional
O modelo de linguagem (local ou nuvem) **não calcula nada**: só redige um comentário a partir dos números
já calculados pelo motor local, com instruções explícitas para não diagnosticar, não inventar valores e não
afirmar causalidade.

## Referências
- Plews DJ, Laursen PB, Stanley J, Kilding AE, Buchheit M. Training adaptation and heart rate variability in elite endurance athletes: opening the door to effective monitoring. *Sports Med*. 2013;43(9):773-781.
- Buchheit M. Monitoring training status with HR measures: do all roads lead to Rome? *Front Physiol*. 2014;5:73.
- Kiviniemi AM et al. Endurance training guided individually by daily heart rate variability measurements. *Eur J Appl Physiol*. 2007;101(6):743-751.
- Leys C et al. Detecting outliers: Do not use standard deviation around the mean, use absolute deviation around the median. *J Exp Soc Psychol*. 2013;49(4):764-766.
- Watson NF et al. Recommended amount of sleep for a healthy adult: a joint consensus statement of the AASM and SRS. *Sleep*. 2015;38(6):843-844.
- Hirshkowitz M et al. National Sleep Foundation's sleep time duration recommendations. *Sleep Health*. 2015;1(1):40-43.
- Chinoy ED et al. Performance of seven consumer sleep-tracking devices compared with polysomnography. *Sleep*. 2021;44(5):zsaa291.

## Triagem e trava de evidências
As áreas personalizadas (treino, metas e suplementos) ficam suspensas enquanto a triagem de saúde não estiver
respondida, e as metas também dependem de pesagem real. Regras que dependem de referência abaixo do nível
mínimo de verificação (por padrão, "não verificada" ou "inadequada") ficam desabilitadas. A verificação das referências foi feita por modelos de IA e
não é auditoria humana independente. Detalhes em `MANUAL_IPHONE.md` e `ARQUITETURA.md`.

## Fase 2
Os métodos acrescentados (EWMA, bases de 7/28/90 dias, CUSUM, backtesting, balanço energético, regras de
treino, nutrição e suplementação, experimentos individuais) estão descritos em `ARQUITETURA.md`, e as
referências, em `EVIDENCIAS.md`.
