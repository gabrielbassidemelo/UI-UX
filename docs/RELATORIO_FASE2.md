# Relatório da fase 2: auditoria, testes, entregas e pendências

Data: 26/11/2026. Branch: `claude/garmin-cirqa-health-monitoring-2mpc9g`. Versão estável anterior: commit `39796ea`.

## Resumo honesto
- O código da plataforma está implementado e passa em **131 testes automatizados, todos com dados sintéticos**.
- **A integração real com a Garmin não foi validada.** A rede do ambiente de desenvolvimento bloqueia a
  Garmin e nenhuma chave foi fornecida. Os caminhos da API vêm da biblioteca oficial; o formato das
  respostas é suposição até você rodar `healthmon validar-garmin` no servidor.
- **Nada foi implantado.** Os scripts de implantação tiveram só a sintaxe verificada; o contêiner foi
  construído e executado localmente.
- **As 56 referências científicas não foram conferidas online.** Um revisor independente as considerou
  corretas de memória, com ressalvas incorporadas; a conferência automática (`healthmon kb verificar`)
  roda no servidor.
- Portanto o sistema **não deve ser considerado operacional** até a validação do item 5 de `IMPLANTACAO.md`.

## Fase A — Auditoria do sistema da fase 1
| Item | Resultado |
|---|---|
| Testes originais | 28 de 28 passando antes de qualquer mudança e ao final |
| Reaproveitado | Banco, parser, análise, relatórios, alertas, backups, autenticação, agendador, casca do painel |
| Limitação: integração não oficial | Substituída pela API oficial; a antiga ficou opcional e fora do contêiner |
| Limitação: dependia de computador ligado | Preparada implantação em VM gratuita |
| Limitação: "falsos alarmes de 0–3%" | **Era resultado de simulação com ruído independente.** Um revisor mediu, em simulação com dias correlacionados, 10–14%. Não é evidência de eficácia; a documentação e o código foram corrigidos |
| Limitação: tarefas sem histórico | Histórico de execuções, marcação de tarefas interrompidas e geração de relatórios perdidos ao reiniciar |
| Limitação: backups sem teste | Cada backup é decifrado e verificado ao ser criado |
| Vulnerabilidades | Nenhuma crítica ou alta; ver "Auditoria de segurança" |

## Subagentes
Os seis subagentes pedidos estão em `.claude/agents/` (arquiteto e científico e segurança em Opus;
desenvolvedor e qualidade em Sonnet; operacional em Haiku). **Limitação desta sessão:** definições criadas
durante a sessão não ficam disponíveis nela, então mantive a divisão lógica e usei dois revisores
independentes (científico e segurança, em Opus) sobre o código pronto. Eles estarão disponíveis nas
próximas sessões.

## Revisão científica independente (achados e tratamento)
| Gravidade | Achado | Tratamento |
|---|---|---|
| Crítico | Déficit calórico proposto para pessoa com baixo peso | **Corrigido:** nenhum déficit com IMC < 18,5; déficit limitado a 10% com IMC < 20; metas de peso abaixo de IMC 18,5 recusadas |
| Alto | Sem triagem de idade, gestação e lactação | **Corrigido:** só adultos; pergunta obrigatória para mulheres; gestantes e lactantes sem déficit e sem avaliação de suplementos |
| Alto | Doença renal não reconhecida em termos comuns; falso positivo em "primária" | **Corrigido:** sinônimos casados no início da palavra |
| Alto | Vitamina D sem referência tratada como normal | **Corrigido:** sem referência, antigo (> 12 meses) ou acima da faixa têm tratamento próprio |
| Alto | Hemoglobina glicada confundida com hemoglobina | **Corrigido** |
| Alto | Gasto recalibrado confiava em registro alimentar implausível | **Corrigido:** se ficar muito abaixo da fórmula, as metas usam fórmula e Garmin, com aviso de provável sub-registro |
| Médio | Intervalo da tendência de peso ignorava autocorrelação | **Corrigido** (correção de tamanho efetivo) |
| Médio | Sinais de prontidão não independentes; ruído reduzia treino em 20–27% dos dias simulados | **Corrigido:** 4 grupos independentes, limiar de 1,5 desvio; em simulação caiu para menos de 1% |
| Médio | Teste de experimentos ignorava os blocos | **Corrigido:** análise por bloco, blocos de até 3 dias, mínimo de 5 por condição |
| Médio | Comparações múltiplas sem correção | **Corrigido** em padrões, associações (Bonferroni) e dia da semana (permutação) |
| Médio | Até 7 dias de treino por semana; limites declarados e não aplicados | **Corrigido:** no máximo 6 sessões; constante sem uso removida |
| Médio | Plano "sem liberação" não era moderado nas semanas 2–3 | **Corrigido** |
| Médio | Texto fraco para dor no peito | **Corrigido:** orienta atendimento de urgência |
| Médio | Alergia a leite só nas condições; dose de cafeína não individualizada; eletrólitos por dado do Garmin | **Corrigidos** |
| Médio | Zona 2 em 60–70% da reserva (vigoroso pela ACSM) | **Corrigido:** 40–60%; aviso para betabloqueadores |
| Baixo | Macros incoerentes após ajuste limitado; textos de Cunningham, água e cafeína | **Corrigidos** |
| Baixo | Disponibilidade energética usa a meta, não a ingestão real, e um fator arbitrário | **Pendente** (o valor é só informativo) |
| Baixo | Tendência de Mann–Kendall com falsos positivos em séries autocorrelacionadas | **Pendente:** as tendências dos relatórios devem ser lidas como indício |

## Auditoria de segurança independente (achados e tratamento)
Nenhum achado crítico ou alto para uso individual em rede privada.
| Gravidade | Achado | Tratamento |
|---|---|---|
| Médio | Limite de tentativas de login burlável com requisições paralelas; scrypt travava o servidor | **Corrigido:** tentativa reservada antes da verificação, verificação serializada e fora do laço de eventos |
| Médio | Limite de tamanho não valia para envio em blocos | **Corrigido:** contagem real de bytes |
| Médio | Inscrição de push permitia o servidor chamar endereços arbitrários | **Corrigido:** só serviços de push conhecidos; erro genérico |
| Médio | Valores infinitos gravados quebravam telas | **Corrigido:** validação de número finito e faixa em todas as entradas |
| Médio | Trocar a senha não encerrava sessões | **Corrigido** |
| Baixo | Corpo não-objeto no login; hora da refeição sem escape; colisão de nomes de laudos; `apagar-dados` incompleto; data sem validação no script; scrypt N=2^15; Ollama remoto | **Corrigidos** (scrypt N=2^17) |
| Baixo | Tabela TACO baixada sem conferência | **Corrigido:** soma SHA-256 fixada |
| Baixo | Imagens Docker, pacotes e ações do GitHub sem versão fixada por hash | **Pendente** |
| Baixo | Nível "completo" de notificações envia resumo por Telegram/e-mail sem etapa de consentimento | **Pendente** (padrão é o nível mínimo) |
| Não verificado | Regra de firewall pré-instalada da Oracle para a porta 22 | Remover a regra na Security List da VCN, como indicado em `IMPLANTACAO.md` |

## Testes
| Arquivo | Testes | Cobre |
|---|---|---|
| Fase 1 (5 arquivos) | 28 | Parser, linha de base, relatórios, alertas, serviço, painel, backup |
| `test_garmin_official.py` | 13 | Autenticação, cache e renovação de token, 401/403/404/429/5xx, resposta inválida, disjuntor, orçamento, duplicatas, sincronização incremental, vencimento da chave, chave fora de banco e logs |
| `test_fase2_ciencia.py` | 66 | Fórmulas, recalibração com resposta conhecida, limites de nutrição e treino, métrica isolada, sintomas de alerta, suplementos e interações, exames, experimentos, backtesting sem vazamento, base científica |
| `test_fase2_web_infra.py` | 24 | Todas as rotas exigem sessão, validação, CSRF, tamanho de corpo, upload, push, agendamento, histórico, recuperação, backup corrompido, retenção, exportação e exclusão, consumo de recursos |
| **Total** | **131** | Todos com dados sintéticos |

Além disso: teste de fumaça em iPhone emulado (13 telas, sem erros de console nem rolagem lateral),
contêiner executado em modo somente leitura (45 MB de memória), `pip-audit` sem vulnerabilidades.

**Não testado:** API real da Garmin; VM Oracle e Arm; Tailscale; push em iPhone real; restauração em outra
máquina; desempenho com respostas reais da Garmin.

## Funcionalidades concluídas
Integração oficial com renovação de token, limites, incremental e alerta de vencimento · motor estatístico ·
metabolismo com três estimativas e recalibração · composição corporal · treinador adaptativo com registro
de cargas · nutrição com metas, registro por texto, TACO e programação linear · suplementação (8 fichas) ·
base científica com busca e verificação de DOI · exames com referência do laboratório · experimentos
individuais e padrões · objetivos · plano diário com justificativa, dados, evidência e confiança ·
histórico de decisões · painel de cinco abas com 13 áreas e períodos de 7 a 365 dias · push no iPhone ·
histórico de execuções e recuperação · retenção, exportação e exclusão · scripts de implantação · CI.

## Funcionalidades pendentes
| Item | Situação |
|---|---|
| Validação com dados reais e implantação | Depende de você (ver abaixo) |
| Leitura dos registros alimentares do Garmin Connect | Guardados em bruto; leitura depende do formato real |
| Registro de refeição por fotografia | Não implementado (exigiria modelo de visão, com autorização própria) |
| Leitura automática de laudos em PDF ou imagem | Não implementado; o original é guardado e os valores são digitados |
| TBCA e USDA | Só o importador genérico de CSV; a TACO tem comando próprio |
| IA externa (modalidade 3): orçamento, bloqueio de gastos e auditoria | Não implementado; a modalidade segue desligada |
| IA local (modalidade 2) | Código pronto; nenhum modelo avaliado na prática |
| Cópia automática dos backups para fora da VM | Não implementada (procedimento manual documentado) |
| Cifragem do banco em nível de aplicação | Não implementada (ver `SEGURANCA_LGPD.md`) |
| Treino por horário e por tipo de estímulo ("quais treinos funcionam melhor") | Só comparações simples de horário e de dia com treino |
| Avaliação de TimesFM e outros modelos | Adiada até haver meses de dados reais |

## Limitações conhecidas
- Todos os números do vestível e os calculados aqui são estimativas, não medições clínicas.
- A recalibração do gasto exige semanas de pesagens e registro alimentar consistente; antes disso a
  confiança é baixa e o painel diz isso.
- As regras de prontidão e os pisos de segurança são convenções transparentes, não protocolos validados.
- A CIRQA pode não fornecer todos os indicadores; prontidão para treino e respiração não existem na API oficial.
- Lista de interações medicamentosas é parcial e baseada em busca por texto.
- Risco de a Oracle recuperar a VM por ociosidade (ver `INFRAESTRUTURA.md`).

## Custos e consumo
| Item | Valor |
|---|---|
| Custo mensal previsto | **R$ 0** (VM Always Free, Tailscale pessoal, GitHub, push) |
| Serviços pagos contratados | Nenhum |
| APIs de IA pagas | Nenhuma em uso |
| Memória | 45–100 MB (VM de 1 GB é suficiente) |
| Disco | Imagem de 533 MB; banco de poucos MB a dezenas de MB por ano |
| Garmin | ~290 requisições por dia, teto de 600 |

## O que depende de você
1. Ler `INFRAESTRUTURA.md` e decidir sobre a conta Oracle (e o risco de ociosidade).
2. Autorizar o envio dos seus dados reais para a VM.
3. Seguir `IMPLANTACAO.md`, digitando a chave da Garmin no terminal do servidor.
4. Enviar o resultado de `validar-garmin` (sem valores de saúde) se algum indicador não for reconhecido.
5. Preencher o perfil no painel e confirmar plano de treino e meta nutricional.
