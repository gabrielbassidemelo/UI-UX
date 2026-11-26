# healthmon — inteligência fisiológica pessoal (Garmin CIRQA)

Sistema pessoal que coleta os dados da sua conta Garmin pela **API oficial**, guarda o histórico num
banco SQLite, constrói linhas de base individuais e produz, todos os dias, um plano com treino, metas
nutricionais e pontos de atenção, cada item com justificativa, dados usados, evidência científica e grau
de confiança. Roda sozinho numa VM gratuita e é usado pelo iPhone como um app (PWA).

> **Estado em 26/11/2026:** o código está completo e passa em 131 testes automatizados com **dados
> sintéticos**. **A integração real com a Garmin ainda não foi validada** e nada foi implantado: isso
> depende de você criar a VM e digitar a sua chave no servidor. Veja `docs/RELATORIO_FASE2.md`.

## O que faz
- **Coleta oficial:** Garmin Connect Personal API (chave pessoal, somente leitura), a cada 2 horas, com
  renovação de token, respeito a limites, sincronização incremental e alerta de vencimento da chave.
- **Estatística individual:** faixa usual por mediana e MAD, EWMA, tendências, detecção de mudança,
  efeito do dia da semana, qualidade dos dados e backtesting de previsões.
- **Metabolismo:** taxa basal (Mifflin-St Jeor; Cunningham com percentual de gordura), três estimativas de
  gasto diário com intervalo e recalibração pela sua ingestão e evolução de peso.
- **Treino adaptativo:** plano periodizado conforme objetivo, nível, equipamento e limitações; progressão
  de carga; ajuste diário por vários sinais mais a sua percepção; limites de segurança fixos.
- **Nutrição:** metas de calorias, macros, fibra e água; registro de refeições por texto com a tabela
  TACO; sugestão de cardápio por programação linear.
- **Suplementação e base científica:** fichas de 8 suplementos, regras que podem concluir "desnecessário",
  56 referências com busca local.
- **Exames, experimentos e evolução:** exames com a referência do laboratório; experimentos A/B em você
  mesmo; objetivos; histórico de decisões e resultados.
- **Relatórios e notificações:** diário (08h), noturno (22h), semanal e mensal; push no iPhone com texto
  genérico.
- **Segurança:** painel só em rede privada (Tailscale), senha com scrypt, backups cifrados e testados,
  exportação e exclusão dos dados.

## Documentação
| Documento | Conteúdo |
|---|---|
| [docs/RELATORIO_FASE2.md](docs/RELATORIO_FASE2.md) | Auditoria, testes, o que está pronto, o que falta, limitações |
| [docs/INFRAESTRUTURA.md](docs/INFRAESTRUTURA.md) | Comparação Oracle × Supabase/Cloudflare, custos, riscos |
| [docs/IMPLANTACAO.md](docs/IMPLANTACAO.md) | Passo a passo, validação com dados reais, recuperação de falhas |
| [docs/MANUAL_IPHONE.md](docs/MANUAL_IPHONE.md) | Como usar no dia a dia |
| [docs/ARQUITETURA.md](docs/ARQUITETURA.md) | Módulos, modelos estatísticos, modalidades de IA |
| [docs/EVIDENCIAS.md](docs/EVIDENCIAS.md) | Referências, regras e limites de segurança |
| [docs/SEGURANCA_LGPD.md](docs/SEGURANCA_LGPD.md) | Relatório de segurança e privacidade |
| [docs/METODOLOGIA.md](docs/METODOLOGIA.md) | Métodos da fase 1 (linha de base, persistência, tendências) |
| [docs/PESQUISA_INTEGRACOES.md](docs/PESQUISA_INTEGRACOES.md) | Integrações avaliadas e compatibilidade com a CIRQA |

## Desenvolvimento
```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
pytest -q                                   # 131 testes, só dados sintéticos
HEALTHMON_HOME=/tmp/hm-demo healthmon init && HEALTHMON_HOME=/tmp/hm-demo healthmon demo   # banco de demonstração
```
Subagentes do Claude Code para manutenção estão em `.claude/agents/` (arquiteto, desenvolvedor,
científico, segurança, qualidade e operacional). A versão estável da fase 1 é o commit `39796ea`.

## Aviso
Ferramenta informativa de acompanhamento pessoal. Não faz diagnóstico, não prescreve tratamento nem
suplementos e não substitui médico, nutricionista ou professor de educação física. Medições de vestíveis
e os valores calculados aqui são estimativas, não exames clínicos.
