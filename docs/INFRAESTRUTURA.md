# Infraestrutura autônoma e gratuita: avaliação e escolha

Avaliação feita em 26/11/2026. Os sites dos provedores não puderam ser abertos do ambiente de
desenvolvimento; os limites abaixo vêm de buscas na web e estão marcados quando a fonte é de terceiros.
**Confira os números na página oficial de cada provedor no momento da contratação.** Nada foi
contratado e nenhum dado real foi enviado a lugar algum.

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
| Recursos gratuitos | Até 2 VMs AMD (1/8 OCPU, 1 GB) e instâncias Arm Ampere A1. Guias de 2026 relatam que o limite Arm caiu de 4 OCPU/24 GB para **2 OCPU/12 GB** (fonte de terceiros; confirmar) |
| O sistema cabe? | Sim, na menor VM AMD de 1 GB (usa ~100 MB), com 2 GB de swap |
| Compatibilidade Arm | Todas as dependências têm pacotes para Arm64; não testado em Arm aqui |
| Região | Escolha **Brazil East (São Paulo)** ou Brazil Southeast (Vinhedo) para manter os dados no Brasil. Disponibilidade de capacidade Arm gratuita na região: **não verificada**; a VM AMD costuma estar disponível |
| Cartão de crédito | Exigido no cadastro para verificação de identidade |
| **Risco: recuperação de instâncias ociosas** | A Oracle pode recuperar VMs Always Free ociosas (CPU, rede e, em A1, memória abaixo de 20% no percentil 95 por 7 dias). **Este sistema é ocioso por natureza e se enquadra.** |
| Criptografia em repouso | Os volumes da Oracle são criptografados com chaves gerenciadas por ela; os backups do sistema são criptografados com a sua frase-senha |

**Sobre o risco de recuperação por ociosidade**, há duas saídas honestas, e a decisão é sua:
1. **Converter a conta para Pay As You Go.** Segundo os guias, isso elimina a recuperação por ociosidade e os
   recursos Always Free continuam gratuitos. Passa a existir a *possibilidade* de cobrança se algum recurso
   pago for criado. Mitigação: não criar nada além da VM e configurar um alerta de orçamento de R$ 1.
   **Não fiz nem recomendo fazer sem a sua autorização expressa.**
2. **Manter Always Free puro e aceitar o risco.** Se a VM for recuperada, você recria a VM e restaura o
   último backup (procedimento em `IMPLANTACAO.md`, cerca de 20 minutos). Para isso, copie os backups
   criptografados para fora da VM (ver `IMPLANTACAO.md`).

Não recomendo gerar carga artificial para burlar a regra de ociosidade.

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
1. Criar a conta Oracle Cloud (cartão para verificação) e decidir entre as duas saídas do risco de ociosidade.
2. Criar a conta Tailscale e instalar o app no iPhone.
3. Gerar a chave pessoal da Garmin e digitá-la **no terminal do servidor**, nunca no chat.
4. Autorizar o envio dos seus dados reais para a VM. Sem essa autorização, nada é implantado.

## Fontes
- [Guia Oracle Always Free 2026 (cloudpicked)](https://cloudpicked.com/oracle-cloud-free-tier-guide-en.html)
- [Análise Oracle Always Free 2026 (space-node)](https://space-node.net/blog/oracle-vps-free-tier-review-2026)
- [Oracle Free Tier como VPS: regra de ociosidade (bestusavps)](https://bestusavps.com/reviews/oracle-cloud/)
- [Breaking down the free tier of OCI (fullmetalbrackets)](https://fullmetalbrackets.com/blog/oci-free-tier-breakdown)
- [Supabase — preços](https://supabase.com/pricing?utm=airanks.net)
- [Limites do plano gratuito do Supabase em 2026 (dev.to)](https://dev.to/mahdi_benrhouma_fe1c6005/supabase-free-tier-limits-in-2026-what-you-actually-get-and-when-you-will-hit-them-4mhe)
