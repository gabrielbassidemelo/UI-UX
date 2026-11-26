# Relatório da fase 3 — validação científica, confiabilidade operacional e preparação para implantação

Data: 26/11/2026. Branch `claude/garmin-cirqa-health-monitoring-2mpc9g`.

**Situação:** o núcleo está pronto para a primeira implantação, mas **o sistema não está operacional**. Nada foi
executado numa VM da Oracle, num bucket real, contra a API real da Garmin ou num iPhone real. Tudo o que este
relatório chama de "testado" foi testado com dados sintéticos no ambiente de desenvolvimento. Nenhum dado real
de saúde foi usado, e nenhuma senha ou chave passou pela conversa, pelo código, por logs ou pelo GitHub.

As revisões citadas aqui foram feitas por **modelos de IA**. Não são auditorias humanas independentes.

## 1. O que foi concluído

| Item da fase 3 | Feito | Limite |
|---|---|---|
| 2. Verificação das 56 referências | Todas conferidas no Crossref; 51 com registro no PubMed; conteúdo lido nos resumos e em 17 textos completos abertos. Classes: 20 verificadas, 29 parciais, 6 não verificadas, 1 inadequada. Nenhuma retratação. As 7 que falharam **desabilitam** as regras que dependiam delas | O site do PubMed, a Cochrane e os portais IOM, EFSA e Anvisa não abriram neste ambiente. Verificação por IA, sem leitura integral de todos os artigos. Detalhe em `EVIDENCIAS.md` |
| 3. Revisão de segurança fisiológica | Triagem estruturada obrigatória (15 perguntas), sintomas do dia, risco nutricional além do IMC, regras para idosos, gestantes e lactantes, doença cardiovascular, renal, hepática, diabetes, transtorno alimentar, cirurgia recente, medicamentos e alergias. Sem resposta, a recomendação fica suspensa. A triagem é reavaliada a cada uso | Regras conservadoras escritas a partir da literatura por IA; não substituem avaliação clínica |
| 3. A IA não sobrescreve regras | IA desligada por padrão; não é chamada em dia de pausa; o texto gerado é descartado se mencionar medicamento, dose, doença, jejum ou esforço intenso | O filtro é uma defesa adicional, não uma garantia |
| 4. Revalidação estatística | Simulador independente (autocorrelação, sazonalidade, mudança de rotina, ausências, erros de sensor, degrau, deriva, pulsos); três conjuntos de sementes separados; comparação com dois métodos simples. Defeitos encontrados e corrigidos em mudança de nível e tendência | **Simulação**, não validação com dados reais. Resultados na seção 4 |
| 5. Infraestrutura | Limites do Always Free conferidos na documentação oficial da Oracle; dependências conferidas para Arm (69 de 70 com pacote pronto; a restante é Python puro) | Disponibilidade de máquinas gratuitas na região só é visível no console da sua conta. Não executado em Arm |
| 6. Backup e recuperação | Cópia consistente com o banco em uso, verificação de integridade, cifragem antes de sair da VM com chave que só você tem, envio diário ao Object Storage com conferência, reenvio, retenção, alerta em qualquer falha, cópia independente pelo painel, modo de recuperação em VM nova, ensaio simulado de perda total | Armazenamento **simulado** (servidor falso local). Formato da URL do bucket e listagem não confirmados na Oracle real |
| 7. Validação da Garmin | Leitor reescrito conforme a especificação oficial da API; `validar-garmin` confere autenticação, renovação do token, estrutura, unidades, fuso e histórico sem expor valores; tela de conferência com o app Garmin Connect. Ausência nunca vira zero | Nenhuma chamada real foi feita. O que a CIRQA realmente entrega só se sabe com a sua chave |
| 8. Calibração | Estágios de observação (<7 dias), linha de base (7–27), personalizado (28–89) e longitudinal (≥90), contados em dias com dados válidos; histórico importado acelera. Nos primeiros 7 dias, déficit calórico e treino intenso ficam suspensos | Os limites são parâmetros operacionais iniciais |
| 9. Feedback diário | Check-in com fadiga, estresse, sono, dor, esforço, adesão e sintomas; cruzamento com os dados da pulseira; avaliação observacional das recomendações após 90 dias. Só leitura: não altera limites | Observacional; não prova efeito |
| 10. Privacidade e acesso | Apresentação "Antes de usar dados reais" (`SEGURANCA_LGPD.md`, seção 0); autorização explícita no script antes da chave da Garmin; painel só na rede privada, com HTTPS | Termos da Oracle e da Tailscale não puderam ser abertos aqui |
| 11. Instalação pelo iPhone | Roteiro em `IMPLANTACAO.md`; script guiado único, repetível, com modo de recuperação | Executado só em contêiner local com serviços falsos |

## 2. O que foi realmente testado

- **Suíte automatizada:** 326 testes passando (131 ao fim da fase 2). Somente dados sintéticos.
- **Segurança por população:** cada população exigida tem teste próprio, incluindo bloqueio informado *depois* de um plano ou meta já confirmados.
- **Backup:** ida e volta do formato cifrado, chave errada, arquivo adulterado, backup durante escrita contínua, perda total simulada e restauração, recusa de restauração com o serviço ativo, nomes remotos maliciosos.
- **Contêiner:** a imagem foi construída e os comandos do script foram executados na ordem, com URL de bucket falsa. O serviço subiu com cerca de 47 MB e registrou as tarefas agendadas.
- **Painel:** todas as telas abertas num navegador com dimensões de iPhone, sem erro de console e sem estouro de largura (um defeito encontrado e corrigido).
- **CI:** ver seção 6.

**Não testado:** VM real, cloud-init, Tailscale, firewall, bucket real, API real da Garmin, pulseira CIRQA, iPhone real, notificação com tela bloqueada, operação por vários dias, máquina Arm, fatura.

## 3. Modelos usados em cada etapa

| Etapa | Modelo |
|---|---|
| Orquestração, integração, correções, documentação de implantação e este relatório | Claude Fable 5.2 (a fase 1 foi feita em Claude Opus 5.5) |
| Revisão de segurança fisiológica e leitura do conteúdo das referências (`cientifico`) | Claude Opus |
| Auditoria de segurança do backup, implantação e filtro de IA (`seguranca`) | Claude Opus |
| Revisão final de arquitetura, com execução do roteiro em contêiner (`arquiteto`) | Claude Opus |
| Backup cifrado e envio externo; API e painel da fase 3; correção dos detectores (`desenvolvedor`) | Claude Sonnet |
| Simulador, medições e conclusão da medição de confirmação (`qualidade`) | Claude Sonnet |
| Manual do iPhone, arquitetura, operação e metodologia (`operacional`) | Claude Haiku |

Dois agentes foram interrompidos no meio do trabalho; o que deixaram foi revisado, completado e testado antes de entrar no repositório.

## 4. Achados que mudaram o sistema

**Segurança fisiológica (revisão Opus):** sintomas escritos em texto livre não eram reconhecidos; o peso de 88 kg da configuração substituía a pesagem real; transtorno alimentar era ignorado. Corrigidos, com testes.

**Arquitetura (revisão Opus), bloqueador:** plano de treino e meta calórica confirmados antes de um bloqueio continuavam aparecendo no plano do dia, na página inicial e no relatório. Corrigido: a triagem é reavaliada a cada uso. Também corrigido: nenhuma notificação automática seria entregue, porque a lista de canais vinha vazia.

**Segurança (auditoria Opus):** a recuperação em VM nova restauraria um backup vazio; restaurar com o serviço ligado perdia dados; o formato de backup podia regredir em silêncio; segredos apareciam em linha de comando; o filtro de texto da IA deixava passar 18 frases de teste. Corrigidos, com testes.

**Estatística (simulação):**

| Detector | Falso alarme por dia | Sensibilidade a evento de 1 desvio |
|---|---|---|
| Mudança de nível | 87% → 12% | 97% → 43% |
| Tendência | 48% → 13% | 88% → 49% |
| Alertas enviados | 19% → 15% | 66% → 69% |
| Prontidão diferente de "normal" (não alterado) | 5% → 7% | 54% → 65% |

A correção trocou sensibilidade por especificidade; não houve ganho dos dois lados. Detalhe em `VALIDACAO_ESTATISTICA.md`.

## 5. Riscos remanescentes

1. **Nada foi validado com dados reais.** O leitor da Garmin segue a especificação, mas a resposta real pode diferir.
2. **Alertas ainda disparam demais.** Em simulação, cerca de 87% dos meses teriam ao menos um alerta sem evento real, antes do intervalo mínimo entre notificações. O estado "persistente" do painel fica ativo em 23% dos dias sem evento quando se olha qualquer um dos 7 indicadores (4% só para HRV). Não foi corrigido: exige um conjunto de simulação novo.
3. **Eventos pequenos ou curtos passam despercebidos** com mais frequência depois da correção. "Normal" não significa "recuperado".
4. **A VM gratuita pode ser recuperada pela Oracle por ociosidade.** Risco aceito por decisão sua; mitigado por backup externo diário e cópia no iPhone. Perda máxima de 24 horas.
5. **Perder a chave de recuperação torna os backups ilegíveis.** Perder a chave e a VM significa perder o histórico.
6. **O banco em uso não tem cifragem própria.** Quem tiver acesso de administrador à VM, ou à sua conta Oracle ou Tailscale, lê os dados.
7. **Sete regras estão desabilitadas** por falta de verificação da fonte (cafeína, magnésio, meta de água, fibra por caloria, disponibilidade energética, texto da Anvisa).
8. **Pendências conhecidas, não bloqueadoras:** sem aviso antecipado do vencimento da URL do bucket; relatórios semanal e mensal perdidos se a VM estiver fora do ar no horário; imagens Docker sem versão fixada; alguns campos ausentes ainda aparecem como "0" em relatórios (minutos de intensidade, duração e calorias de atividade).

## 6. Critérios de aprovação

| Critério | Estado |
|---|---|
| CI verde no commit final | Conferir no GitHub Actions para o commit que contém este relatório |
| Testes científicos e de segurança passando | ✅ 326 testes (dados sintéticos) |
| Referências verificadas ou recomendações desabilitadas | ✅ por IA; 7 desabilitadas |
| Implantação concluída | 🔶 pendente: depende de você |
| Garmin validada com dados reais autorizados | 🔶 pendente |
| Backup automático funcionando | 🔶 pendente (testado só com armazenamento simulado) |
| Restauração comprovada | 🔶 pendente (comprovada só em simulação) |
| Painel acessível pelo iPhone | 🔶 pendente |
| Notificações funcionando | 🔶 pendente |
| Coleta automática sem intervenção | 🔶 pendente |
| Custo de R$ 0 por mês | 🔶 pendente: conferir a fatura ao fim do primeiro mês |

## 7. Próximo procedimento

1. Seguir `IMPLANTACAO.md` na ordem: contas, VM, rede privada, código, bucket, `./deploy/oracle/configurar.sh`.
2. O script para antes da chave da Garmin e pede `AUTORIZO`. Até ali nenhum dado real existe fora da Garmin.
3. Depois: ativar notificações, responder a triagem, importar o histórico, conferir os valores com o app Garmin Connect, fazer o ensaio de recuperação e fechar o SSH público.
4. Se algo falhar, trazer para uma sessão a mensagem de erro e o arquivo `data/validacao_garmin.json`. Ele não contém valores de saúde, mas contém o fuso da conta e a data do primeiro dia com dados.
5. Só depois de uma semana de operação real estável vale retomar recursos adicionais de IA.
