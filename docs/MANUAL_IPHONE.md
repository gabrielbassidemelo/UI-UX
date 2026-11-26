# Manual de uso pelo iPhone

Este manual descreve o que o painel faz hoje, com os nomes que aparecem nas telas. Nada aqui foi
testado num iPhone real neste ambiente de desenvolvimento: os passos de instalação e de notificação
estão **pendentes de execução pelo usuário**.

## Instalar
1. Ligue o app **Tailscale** no iPhone.
2. No Safari, abra o endereço do painel (termina em `.ts.net`) e entre com a senha do dashboard.
3. Toque em **Compartilhar → Adicionar à Tela de Início**. O painel passa a abrir como um app.
4. Abra o app pela Tela de Início, vá em **Mais → Configurações**, na seção **Notificações no iPhone**,
   toque em **Ativar neste aparelho** e permita. Exige iOS 16.4 ou mais recente e acesso por HTTPS.

## Primeiro uso
1. **Mais → Configurações → Perfil.** Preencha **sexo biológico**, **nascimento** e **altura**. Esses três
   campos são obrigatórios.
2. Na mesma tela, responda a **Triagem de saúde (obrigatória)**: 15 perguntas, cada uma com **Sim** ou
   **Não**. Pergunta sem resposta não conta como "Não": o treino, as metas e os suplementos personalizados
   continuam suspensos. Uma resposta "Sim" pode bloquear áreas específicas (o motivo aparece na tela).
3. Em **Lesões ou limitações físicas**, marque a opção ou "Nenhuma limitação". Em **Condições de saúde**,
   **Medicamentos** e **Restrições alimentares**, escreva "nenhuma" (ou "nenhum") quando for o caso; campo
   em branco conta como não informado.
4. **Mais → Composição corporal → Registrar medida.** Registre o **peso real** (kg). Sem pesagem registrada,
   o sistema não calcula metas de calorias nem de peso. Ele não usa peso presumido.
5. **Treino → Gerar proposta de plano**, leia a proposta e toque em **Confirmar e ativar este plano**.
6. **Nutrição**, leia a meta proposta e toque em **Confirmar esta meta**.

Nenhum plano ou meta entra em vigor sem a sua confirmação. Enquanto a triagem estiver incompleta, a tela
**Hoje** mostra o cartão **Triagem de saúde pendente**, com a lista do que falta.

Uso restrito a adultos (18 anos ou mais). Para menores de 18, o sistema não gera recomendações.

## Rotina diária
| Quando | O que fazer | Onde |
|---|---|---|
| Ao acordar | Abra o Garmin Connect para a pulseira sincronizar. O relatório diário sai às 08h | Notificação ou **Hoje** |
| Manhã | Preencha o check-in (campos abaixo) e toque em **Salvar check-in** | **Hoje → Como você está?** |
| Treino | Siga a sessão do dia e registre cada série. Ao final, toque em **Salvar sessão** | **Treino** |
| Refeições | Escreva o que comeu com quantidades (ex.: "150 g frango grelhado, 200 g arroz cozido") e toque em **Registrar** | **Hoje** ou **Nutrição** |
| Noite | Confira o relatório noturno das 22h | Notificação ou **Relatórios** |
| Sempre que pesar | Registre o peso real em **Composição corporal → Registrar medida** (a referência é a média das pesagens dos últimos 7 dias) | **Mais** |

### Check-in
Campos da tela atual (todos opcionais). A triagem de sintomas só é registrada se você marcar algum item ou
marcar "Não tenho nenhum destes sintomas hoje":
- **Recuperação percebida (1–10)**
- **Dor muscular (1–5)**, **Fadiga (1–5)**, **Estresse percebido (1–5)**, **Qualidade do sono (1–5)**
- **Seguiu a recomendação de ontem?**: não informar, Sim, Em parte ou Não
- **Cafeína hoje (mg)**, **Último café (hora)**, **Álcool (doses)**, **Água (ml)**
- **Sintomas ou observações** (texto livre, opcional, até 300 caracteres)
- **Sintomas de hoje**: seis itens (ver abaixo) ou "Não tenho nenhum destes sintomas hoje"

A tela atual **não tem o campo "Disposição"**. A API aceita um campo `energy` (1–5), mas o painel não o
envia. Quem quiser registrar disposição precisa de uma mudança de tela, que não existe hoje.

### Sintomas de hoje
Os seis itens são a triagem do dia. Quatro deles são marcados como **(alerta)** na tela:
- Dor ou pressão no peito
- Desmaio ou quase desmaio / tontura forte
- Falta de ar fora do normal
- Palpitações ou coração disparado em repouso

Os outros dois são febre ou sensação de doença, e dor aguda ou inchaço em articulação / lesão nova.

O que acontece:
- **Marcar qualquer sintoma** suspende o treino do dia. O painel mostra "Não treine hoje" com a
  orientação correspondente.
- **Marcar um dos quatro sintomas de alerta** mostra o aviso **Procure atendimento**, com o texto
  "procure atendimento de urgência agora (SAMU 192). Não treine". O aviso aparece na hora, na tela, e
  depois de salvar aparece também uma mensagem curta de alerta.
- **Texto livre** em "Sintomas ou observações" também é lido. Expressões como "dor no peito" podem marcar
  um sintoma, mas o texto livre **só reforça**: nunca libera treino nem meta.
- **Sem marcar nada**, a triagem do dia fica como **não respondida**. Nesse caso o sistema não sugere
  aumento de carga nem treino intenso.

O aviso de urgência não substitui atendimento. Em caso de sintoma, procure atendimento.

## Estágios de calibração
O sistema conta **dias válidos**: dias com pelo menos dois dos quatro indicadores centrais (sono, FC de
repouso, HRV da noite e passos). Histórico importado também conta. Com base nesse número, a tela **Hoje**
mostra o card **Calibração**, com o estágio, os dias válidos, quantos faltam para o próximo estágio e a
lista **Ainda suspenso**.

| Estágio (nome na tela) | Dias válidos | O que o sistema faz |
|---|---|---|
| Observação | Menos de 7 | Relatórios descritivos e orientações gerais de baixo risco. Sem alertas de tendência e sem redução de treino por recuperação |
| Construção da linha de base | 7 a 27 | Passa a emitir alertas de tendência e pode **reduzir** o treino por recuperação. Não aumenta carga. Sem progressão de carga |
| Recomendações personalizadas | 28 a 89 | Liberado: progressão de carga, calibração metabólica e análise de tendências |
| Análise longitudinal | 90 ou mais | Liberado: comparações de longo prazo e avaliação das recomendações |

Regras que valem em todos os estágios:
- Se o sistema já está em "personalizado" ou "longitudinal", mas teve menos de 8 dias válidos nas últimas
  duas semanas, volta ao modo conservador de "construção da linha de base" até a coleta se regularizar.
- Os limites de 7, 28 e 90 dias são **parâmetros operacionais iniciais**, não garantias de validade
  científica. Eles dependem da quantidade, da qualidade e da representatividade dos seus dados.
- A calibração metabólica depende de peso e de alimentação registrados.

## Como ler as recomendações e os selos de verificação
Cada recomendação mostra o que fazer e um selo de **confiança** (alta, média ou baixa). Confiança baixa
geralmente significa falta de dados: sem check-in, poucas pesagens ou registro alimentar incompleto. O
painel indica o que registrar em **Próximas ações**.

Para ver a justificativa, os dados usados e as referências, toque em **Por quê, dados usados e evidências**
(ou em **Ver justificativas e evidências**, na tela Recomendações). Cada referência mostra um selo de
verificação:

| Selo | Significado |
|---|---|
| **Verificada** | A publicação foi confirmada e o texto lido sustenta a regra que a cita |
| **Parcialmente verificada** | A publicação foi confirmada, mas parte do conteúdo usado (em geral um número específico) não apareceu no texto lido |
| **Não verificada** | O conteúdo relevante não pôde ser lido em nenhuma fonte acessível |
| **Inadequada para a regra** | O texto lido contradiz a afirmação ou não sustenta a regra |

Por padrão, uma regra só é aplicada se a referência que a sustenta estiver pelo menos em **Parcialmente
verificada**. Regras que dependem de referências "Não verificada" ou "Inadequada" ficam **desabilitadas**.
Em alguns casos, a tela mostra "sem meta (evidência não verificada)" no lugar do número.

A verificação foi feita por modelos de IA e não é auditoria humana independente. O link **DOI** leva ao
registro da publicação.

## Conferência Garmin
Tela: **Mais → Conferência Garmin** ("Compare com o app Garmin Connect").

1. Escolha o dia (não é possível escolher data futura).
2. Abra o app Garmin Connect, na mesma data, e compare cada valor listado.
3. Toque em **Igual** ou **Diferente** para cada indicador. A tela mostra o valor que o sistema guardou
   ("no sistema: …") ou "sem valor no sistema".

Cada resposta fica registrada no servidor. Ela serve para checar se a integração lê os valores como o
aplicativo. A comparação com o app é feita por você. **Ainda não foi feita por este documento.** Enquanto
não houver registro de validação com os seus dados, a tela **Configurações → Fonte de dados e sistema**
mostra "ainda não validada".

## Backup
Tela: **Mais → Configurações → Backup externo**.

O painel mostra:
- **Envio externo**: não configurado, ativo ou atenção
- **Último envio bem-sucedido** e **Tempo desde o último envio**
- **Falhas seguidas** e, se houver, o último erro
- **Envio atrasado** quando o último envio externo tem mais de 36 horas, ou nunca foi feito
- **Último backup local**, com formato, tamanho e quantidade de dias de indicadores

Automatismo (horário padrão): o backup local é criado todo dia às 03h30 e verificado antes de ser considerado
válido. O
envio externo é tentado logo depois e repetido às 09h20, 15h20 e 21h20, quando está pendente ou falhou. As
falhas viram alerta (ver `docs/OPERACAO.md`).

**Baixar backup cifrado.** O botão baixa o arquivo `.hmbk` mais recente. Guarde uma cópia no app **Arquivos**
(iCloud) **uma vez por semana**, como a própria tela sugere. O arquivo só abre com a **chave de recuperação**
(`HMRK-…`), que fica com você e não é gravada no servidor. Sem ela, nem o servidor abre o backup.

O envio para o armazenamento externo (Oracle Object Storage) e a restauração em VM nova **não foram testados
contra um bucket real**. Veja `docs/RECUPERACAO_DESASTRES.md`.

## As áreas
- **Hoje:** condição atual, tendência de recuperação dos últimos 7 dias, qualidade dos dados, calibração,
  treino recomendado, metas nutricionais, próximas ações, alertas e objetivos.
- **Saúde:** Recuperação, Sono e Coração. Gráficos com períodos de 7, 30, 90, 180 e 365 dias, com faixa usual
  e análise de cada indicador.
- **Treino:** sessão do dia (séries, repetições, RIR, descanso, técnica e sugestão de carga), a decisão do
  dia e os sinais que a justificaram, plano completo, zonas de frequência cardíaca e evolução por exercício.
  Botões: **Registrar série**, **Salvar sessão**, **Ver evolução**.
- **Nutrição:** metas do dia, refeições registradas, registro por texto com **Pré-visualizar** antes de
  **Registrar**, registro manual e qualidade do registro.
- **Mais:** Metabolismo, Composição corporal, Suplementação, Exames laboratoriais, Evolução (objetivos,
  comparação de períodos e experimentos), Recomendações (plano do dia, justificativas e histórico de
  decisões), Relatórios, Conferência Garmin e Configurações.

**RIR** (repetições em reserva) é quantas repetições você ainda conseguiria fazer ao terminar a série.
RIR 2 significa parar duas repetições antes da falha.

## Limites que o sistema impõe
Os limites estão no código, não são escolhidos por você no painel:
- **Emagrecimento:** o déficit padrão é de 20% do gasto estimado. O déficit nunca passa de 25% e o piso
  calórico é de 1500 kcal (homens) e 1200 kcal (mulheres).
- **Perda de peso acima de 1% por semana:** o déficit é reduzido para 10%.
- **IMC estimado entre 18,5 e 20:** o déficit é limitado a 10%, com aviso para conversar com um profissional.
- **IMC abaixo de 18,5:** nenhum déficit é proposto.
- **A partir de 65 anos:** déficit de 10%, regras de treino mais conservadoras e treino de equilíbrio.
- **Gestação, amamentação ou pós-parto:** o sistema não propõe déficit, metas, suplementos nem treino
  nessa fase. Eles devem ser orientados pelo obstetra e pelo nutricionista.
- **Transtorno alimentar atual ou passado:** o sistema não calcula metas de calorias ou peso e não mostra
  contagem calórica.
- **Doença renal ou hepática:** o sistema não calcula metas de proteína, líquidos nem suplementos.
- **Doença cardiovascular:** o sistema não propõe HIIT nem séries perto da falha.
- **Medicamento que altera a frequência cardíaca:** zonas de frequência por batimentos não se aplicam, e a
  intensidade deve ser combinada com o médico.
- **Insulina ou medicamento que pode causar hipoglicemia:** o sistema não propõe déficit. Os avisos
  orientam a combinar o monitoramento com o médico, sem orientar doses.
- **Cirurgia recente ou reabilitação:** o sistema não gera plano de treino nessa fase.
- **Sem liberação médica registrada:** o plano fica em intensidade moderada, sem HIIT.
- **Treino:** número de dias de força, séries por grupo muscular, HIIT por semana, aumento de carga por
  progressão e duração de sessão têm teto por nível de experiência.
- **Suplementos:** nenhuma sugestão a partir de dados do relógio. Hormônios e estimulantes perigosos são
  recusados.

## Notificações
Com o canal de push ativo, as mensagens são genéricas, por padrão: avisam que há um relatório ou alerta,
sem números de saúde. Cada aparelho precisa ser ativado em **Configurações**. O envio real para o iPhone
**não foi testado neste documento**.

## O que o sistema NÃO faz
- Não diagnostica doenças e não interpreta exames como diagnóstico.
- Não prescreve treino, dieta, suplemento ou tratamento sem as regras e a confirmação descritas acima.
- Não sugere a troca de medicamento. Quando há medicamento que exige cuidado, os bloqueios e avisos
  encaminham ao médico.
- Não substitui médico, nutricionista ou professor de educação física.
- Não trata as medições da pulseira como exames clínicos. São estimativas.
- Não envia dados a IA externa. Essa modalidade fica desligada até você dar consentimento com
  `healthmon ia-nuvem consentir` (ver `docs/OPERACAO.md`).

Em caso de sintomas, procure atendimento. Em emergência, ligue 192 (SAMU).
