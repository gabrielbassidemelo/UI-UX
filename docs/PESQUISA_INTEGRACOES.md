# Pesquisa de integrações e compatibilidade com o Garmin CIRQA

Pesquisa feita em 26/11/2026. Os sites da Garmin (developer.garmin.com, connectapi.garmin.com) estavam
bloqueados pela rede do ambiente de desenvolvimento, então as afirmações sobre eles vêm de resultados de
busca e de análises de terceiros, com as fontes listadas. O código da biblioteca `garminconnect` foi
inspecionado diretamente (versão 0.3.20, publicada no PyPI em 23/10/2026).

## 1. O dispositivo: Garmin CIRQA

Pulseira sem tela, lançada em 2026 (US$ 199,99, sem assinatura). Segundo análises publicadas:

| Recurso | Situação | Observação |
|---|---|---|
| Frequência cardíaca contínua / repouso | Sim | Sensor óptico Elevate Gen 4 |
| HRV (Status de HRV noturno) | Sim | |
| Sono (fases, pontuação, coach, cochilos) | Sim | |
| SpO2 (Pulse Ox) | Sim, **desligado por padrão** | Ative "Pulse Ox durante o sono" no Garmin Connect (gasta mais bateria) |
| Temperatura da pele | Sim | Camada extra sobre o sensor Gen 4; exposta como variação noturna |
| Respiração | Sim | "Variações respiratórias" no sono |
| Estresse, Body Battery | Sim | |
| Passos, calorias, atividades | Sim | Sem GPS próprio (usa o do celular) |
| Métricas de treino | Sim (citado) | Prontidão para treino e VO2 máx. **a confirmar** com seus dados |
| ECG / detecção de FA | **Não** | Sem hardware para isso |
| GPS próprio | **Não** | |

Como as listas de recursos variam entre análises, o sistema não presume nada: o comando
`healthmon disponibilidade` (e a aba **Sistema** do dashboard) mostra quais indicadores **a sua conta
realmente entregou** nos últimos 30 dias. Ausência de dados é marcada como ausência, nunca estimada.

## 2. Alternativas de integração avaliadas

| Opção | Oficial? | Uso pessoal | Avaliação |
|---|---|---|---|
| **Garmin Health API** (Connect Developer Program) | Sim | **Não**: programa para empresas, com aprovação | Descartada para uso pessoal |
| **Garmin Personal API** | Sim | Sim, segundo o FAQ da Garmin (chave gerada pelo próprio usuário, validade de 30 dias a 1 ano, só leitura dos próprios dados) | **Promissora, mas documentação técnica não verificada** (host bloqueado aqui). Ver §3 |
| **`garminconnect` (Python, não oficial)** | Não | Sim | **Adotada agora.** Ativa (v0.3.20, out/2026), reescreveu o login após a mudança de autenticação da Garmin em mar/2026; suporta MFA; tokens em arquivo 600 |
| `garth` | Não | — | **Descontinuada** (última versão em mar/2026); não usar |
| Apple Health / HealthKit | Sim (Apple) | Sim | Exige app iOS nativo (Xcode e conta de desenvolvedor paga para instalação permanente); o Garmin Connect compartilha com o Apple Health apenas um subconjunto de dados (métricas proprietárias como Body Battery e a pontuação de sono não fazem parte dele). Descartada |
| Servidores MCP existentes para Garmin | Não | Sim | Usam a mesma biblioteca não oficial e foram feitos para mandar dados a um LLM na nuvem, o que vai contra o requisito de privacidade. Não adotados |
| MCP próprio | — | — | Desnecessário: a análise roda localmente. Pode ser acrescentado depois como interface só de leitura ao banco local |

### Riscos da biblioteca não oficial (leia)

- **Termos de uso:** o acesso automatizado ao Garmin Connect fora das APIs oficiais pode não ser
  permitido pelos termos da Garmin. Para uso pessoal e com volume baixo (≈8 chamadas por dia sincronizado,
  com pausas), o risco prático é baixo, mas existe.
- **Quebras:** a Garmin pode mudar endpoints ou autenticação sem aviso, como fez em março/2026. Mitigações
  implementadas: JSON bruto sempre guardado (`healthmon reprocessar` recalcula tudo), alerta de "falha
  de sincronização", e o coletor isolado atrás de uma interface (`GarminSource`), que permite trocar de fonte
  sem mexer no restante.
- **Credenciais:** a senha é digitada uma única vez, só fica na memória e é descartada; guardam-se apenas
  os tokens de sessão (arquivo com permissão 600). Se os tokens expirarem, você recebe um alerta pedindo `healthmon login`.

## 3. Próximo passo recomendado: Personal API oficial

O FAQ da Garmin descreve uma **Personal API** para pessoas físicas acessarem os próprios dados (chave
gerada pelo usuário, só leitura). É a opção mais alinhada ao requisito "oficial e estável". Não a implementei
porque **não consegui ler a documentação técnica** (endpoints, cabeçalhos, limites), e o requisito proíbe
inventar endpoints. Quando a documentação for verificada, basta criar um `PersonalApiSource` com o mesmo
método `fetch_day()` e trocar a fonte; banco, análises, relatórios e dashboard continuam iguais.

## Fontes

- [DC Rainmaker — Garmin Cirqa Hands-On](https://www.dcrainmaker.com/2026/07/garmin-cirqa-everything-you-need-to-know.html)
- [Garmin Rumors — Garmin lança a CIRQA](https://garminrumors.com/garmin-launches-cirqa-smart-band-screenless-tracking-no-subscription-required-199-99/)
- [the5krunner — CIRQA (vazamento/primeiras imagens)](https://the5krunner.com/2026/01/26/garmin-cirqa-smart-band-leak-whoop-competitor/)
- [Lifestack — análise da Garmin Cirqa](https://lifestack.ai/blog/garmin-cirqa)
- [Garmin — Personal API FAQ](https://developer.garmin.com/gc-developer-program/personal/personal-faq/)
- [Garmin — Program FAQ (programa para empresas)](https://developer.garmin.com/gc-developer-program/program-faq)
- [the5krunner — Garmin personal API](https://the5krunner.com/2026/10/22/garmin-personal-api/)
- [Documentação do Garth (aviso de descontinuação)](https://garth.readthedocs.io/)
- [python-garminconnect — issue #332 (mudança de autenticação)](https://github.com/cyberjunky/python-garminconnect/issues/332)
- [PyPI — garminconnect](https://pypi.org/project/garminconnect/)
