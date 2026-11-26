# Infraestrutura autônoma e gratuita: avaliação e escolha

Avaliação feita em 26/11/2026 e revista na fase 3. **Os limites da Oracle abaixo foram conferidos na
documentação oficial** (docs.oracle.com, "Always Free Resources", lida em 26/11/2026). Os dados do Supabase
continuam vindo de fontes de terceiros. Nada foi contratado e nenhum dado real foi enviado a lugar algum.

## Requisitos
Funcionar sem o seu computador ligado e sem uma sessão do Claude Code aberta; custo recorrente de R$ 0;
acesso pelo iPhone; dados de saúde protegidos; reaproveitar o código existente.

## Consumo medido do sistema (dados sintéticos, 365 dias)
| Recurso | Medido |
|---|---|
| Memória do serviço (agendador + dashboard) | ~70 MB em repouso, pico de ~100 MB |
| CPU | Ocioso quase o tempo todo; cada sincronização e relatório leva segundos |
| Tempo de resposta da API | 10–350 ms |
| Banco com 1 ano de dados sintéticos | 1,3 MB (com respostas reais da Garmin, estimo dezenas de MB por ano; não medido) |
| Imagem Docker | 293 MB |
| Requisições à Garmin | 12 por dia sincronizado; ~290/dia no ritmo de 2 em 2 horas (teto configurável de 600) |

## Alternativa A — Oracle Cloud Always Free (escolhida)
Uma máquina virtual que roda o sistema exatamente como está: Python, Docker, SQLite, agendador, backups e
dashboard, sem reescrever nada.

| Ponto | Situação |
|---|---|
| Recursos gratuitos (documentação oficial) | Até **2 VMs AMD** `VM.Standard.E2.1.Micro` (1/8 OCPU, 1 GB) e, em Arm, **1.500 horas-OCPU e 9.000 GB-hora por mês** de `VM.Standard.A1.Flex`, o que equivale a 2 OCPU e 12 GB; 200 GB de volume de blocos no total (disco de inicialização mínimo de 47 GB); **20 GB de Object Storage e 50.000 requisições por mês**; 10 TB de tráfego de saída |
| Onde valem | Só na **região de origem** da conta, escolhida no cadastro e imutável |
| Gratuidade permanente? | Sim para os itens com o rótulo *Always Free-eligible*. O crédito de avaliação dos primeiros 30 dias **não** é permanente e não é usado aqui |
| O sistema cabe? | Sim, na VM AMD de 1 GB (usa ~100 MB), com 2 GB de swap. Backups: ~1 MB cada com dados sintéticos; 14 diários + 8 semanais ficam muito abaixo de 20 GB. Requisições ao bucket: cerca de 5 por dia, contra 50.000 por mês |
| Compatibilidade Arm (verificado na fase 3) | Das 70 dependências instaladas, 69 têm pacote pronto para `aarch64` no Python 3.12 e a restante (`http_ece`) é Python puro; `numpy` e `scipy` também têm. O script de instalação detecta a arquitetura. **Não executado em uma máquina Arm** |
| Disponibilidade na região | **Não verificável daqui** (exige o console da sua conta). A documentação avisa que o erro *out of host capacity* indica falta temporária de máquinas gratuitas e orienta tentar outro domínio de disponibilidade ou esperar. A VM AMD só pode ser criada em um domínio |
| Cartão de crédito | Exigido no cadastro para verificação de identidade |
| **Risco: recuperação de instâncias ociosas** (documentação oficial) | A Oracle pode recuperar VMs Always Free se, por 7 dias, o percentil 95 de CPU ficar abaixo de 20%, a rede abaixo de 20% e, em A1, a memória abaixo de 20%. **Este sistema é ocioso por natureza e se enquadra.** |
| Criptografia em repouso | Volumes cifrados pela Oracle com chaves dela; backups cifrados com chave que só você tem |

**Decisão registrada (fase 3):** manter a conta Always Free pura, **sem** conversão para Pay As You Go e sem
gerar carga artificial para burlar a regra de ociosidade. O risco de a VM ser recuperada é aceito; a perda
de histórico não é. Por isso o backup externo diário, a cópia independente no iPhone e o roteiro de
recuperação em VM nova (`RECUPERACAO_DESASTRES.md`) são obrigatórios antes de qualquer dado real.
Recriar a VM e restaurar leva cerca de 30 minutos; a perda máxima é de 24 horas de dados, que a própria
Garmin ainda guarda e o comando `historico` reimporta.

## Alternativa B — Supabase + Cloudflare (não escolhida)
| Ponto | Situação |
|---|---|
| Código reaproveitado | Quase nenhum. Edge Functions rodam TypeScript/Deno: coleta, parser, estatística, treino, nutrição, relatórios e alertas (~7.000 linhas de Python e 108 testes) teriam de ser reescritos em TypeScript e SQL |
| Banco | PostgreSQL de 500 MB no plano gratuito (fontes de terceiros); suficiente |
| Pausa por inatividade | Projetos gratuitos pausam após 7 dias sem atividade; o agendador manteria o projeto ativo, mas a regra existe |
| Agendamento | Supabase Cron (pg_cron) chamando Edge Functions; limites de CPU e memória por execução **não confirmados** |
| Backups | Plano gratuito sem backups gerenciados confiáveis para este fim (a confirmar); exigiria exportação própria |
| Segurança | Exigiria Row Level Security em todas as tabelas, autenticação do Supabase e revisão do contrato para dados de saúde |
| Front-end | PWA estático no Cloudflare Pages funcionaria; o painel ficaria exposto à internet, protegido só pelo login |
| Dados de saúde | Ficariam em banco gerenciado por terceiro, com mais um provedor (Cloudflare) no caminho |

## Comparação
| Critério | A — Oracle VM | B — Supabase + Cloudflare |
|---|---|---|
| Custo mensal | R$ 0 | R$ 0 |
| Código preservado | ~100% | ~10% (só o front-end) |
| Esforço | Implantar (1–2 horas suas) | Reescrever e revalidar tudo |
| Exposição à internet | Nenhuma (só rede privada Tailscale) | Painel e API públicos |
| Provedores com acesso aos dados | 1 (Oracle, infraestrutura) | 2 (Supabase e Cloudflare) |
| Confiabilidade | Boa; risco de recuperação por ociosidade | Boa; risco de pausa por inatividade e limites de execução |
| Manutenção | Atualizações automáticas do sistema; `git pull` para o app | Deploys de funções, migrações SQL, políticas RLS |
| Migração futura | Fácil: é um contêiner com um arquivo SQLite | Difícil: acoplado aos serviços |
| IA local opcional | Possível numa A1 de 12 GB (ver `ARQUITETURA.md`) | Impossível |

**Escolha: Alternativa A.** Preserva o código e os testes, mantém os dados em um único lugar sem exposição
pública e é a mais simples de migrar.

## Serviços de apoio
| Serviço | Uso | Custo | Recebe dados de saúde? |
|---|---|---|---|
| Tailscale (plano pessoal) | Rede privada entre o iPhone e a VM, HTTPS do painel e terminal pelo navegador | Gratuito | Não. O tráfego é cifrado de ponta a ponta; a Tailscale coordena conexões e vê metadados (aparelhos, horários) |
| Apple Push (Web Push) | Entrega das notificações do PWA | Gratuito | Não. Conteúdo cifrado e genérico |
| GitHub (repositório privado) | Código, histórico, testes automatizados | Gratuito | **Não**: `.gitignore` e testes impedem; os testes usam só dados sintéticos |
| GitHub Actions | Roda a suíte de testes a cada alteração | Gratuito dentro da franquia | Não. Não é usado como servidor de monitoramento |

## O que depende de você
1. Criar a conta Oracle Cloud (cartão para verificação), sem converter para Pay As You Go.
2. Criar a conta Tailscale e instalar o app no iPhone.
3. Criar o bucket e a URL de acesso para os backups e guardar a chave de recuperação.
4. Gerar a chave pessoal da Garmin e digitá-la **no terminal do servidor**, nunca no chat.
5. Autorizar o envio dos seus dados reais para a VM. Sem essa autorização, nada é implantado.

## Fontes
- [Oracle — Always Free Resources (documentação oficial)](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- [Guia Oracle Always Free 2026 (cloudpicked)](https://cloudpicked.com/oracle-cloud-free-tier-guide-en.html)
- [Análise Oracle Always Free 2026 (space-node)](https://space-node.net/blog/oracle-vps-free-tier-review-2026)
- [Oracle Free Tier como VPS: regra de ociosidade (bestusavps)](https://bestusavps.com/reviews/oracle-cloud/)
- [Breaking down the free tier of OCI (fullmetalbrackets)](https://fullmetalbrackets.com/blog/oci-free-tier-breakdown)
- [Supabase — preços](https://supabase.com/pricing?utm=airanks.net)
- [Limites do plano gratuito do Supabase em 2026 (dev.to)](https://dev.to/mahdi_benrhouma_fe1c6005/supabase-free-tier-limits-in-2026-what-you-actually-get-and-when-you-will-hit-them-4mhe)
