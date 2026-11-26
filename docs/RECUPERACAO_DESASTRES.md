# Backup externo e recuperação de desastres

## Resumo

- O servidor (VM Oracle) gera, todo dia, um backup do banco **cifrado com uma chave pública**. O servidor
  consegue criar o backup, mas **não consegue abri-lo**.
- A chave que abre o backup (chave de recuperação, `HMRK-...`) fica **só com você** (papel e gerenciador de senhas).
  Ela **não é gravada** na VM nem no bucket. Ela **passa** pela VM em três momentos, sempre digitada por você com a
  tela oculta e sem registro em log: quando é gerada e conferida, no ensaio de recuperação e numa restauração.
- Uma cópia do arquivo cifrado é enviada ao **Oracle Object Storage** por uma URL de PAR (Pre-Authenticated Request).
  Se a VM for perdida, o arquivo continua no bucket e pode ser aberto por você, em outra VM, com a chave.

Limites honestos dessa proteção:
- Quem roubar o bucket ou a PAR só obtém arquivo cifrado.
- Quem invadir a VM **já lê o banco em uso**; a chave pública que encontra lá não abre os backups antigos. Se a VM
  estiver comprometida no momento de um ensaio ou de uma restauração, a chave digitada pode ser capturada.
  Por isso o ensaio completo é feito, de preferência, numa VM nova e descartável.
- O formato não prova **quem** criou um backup: quem tiver a chave pública e a PAR consegue enviar um backup falso.
  A restauração mostra a data de criação autenticada e a quantidade de dias e exige a sua confirmação; ative o
  versionamento do bucket (obrigatório, abaixo) para que nada enviado possa destruir o que já estava lá.

## Desenho

1. **Formato HMBK2** (`healthmon/backup.py`): para cada backup gera-se uma chave efêmera X25519; ECDH com a chave
   pública de recuperação, HKDF-SHA256 e ChaCha20-Poly1305. O cabeçalho (versão, data/hora UTC, SHA-256 do banco,
   linhas de `daily_metrics`, versão do esquema, chave efêmera) é autenticado: alterá-lo invalida a decifragem.
   O arquivo termina com um SHA-256 do conteúdo, que permite ao servidor detectar corrupção sem ter a chave.
2. **Snapshot consistente**: API de backup do SQLite (seguro com o banco em uso e em modo WAL), depois
   `PRAGMA integrity_check` no snapshot. Se falhar, o backup é abortado com erro e nada é gravado.
3. **Retenção local**: `rotate` mantém os diários e semanais (`[backup] manter_diarios`, `manter_semanais`).
   Depois que a chave de recuperação é adotada, o formato antigo (HMBK1, que o servidor consegue abrir) é
   **recusado**: se a chave pública sumir ou for trocada, o backup falha e gera alerta em vez de mudar de formato em silêncio.
4. **Cópia externa** (`healthmon/offsite.py`): PUT do arquivo cifrado na PAR; depois baixa o objeto e compara o SHA-256
   com o local. O estado fica em `offsite_state.json` (último envio ok, último erro, falhas consecutivas).
   Falha de rede registra o erro e **nunca apaga** backup local. Nomes dos objetos: `healthmon-AAAAMMDD-HHMMSS.hmbk`
   (data/hora no nome, um objeto por backup).

## O que foi testado (e o que NÃO foi)

Testes automatizados em `tests/test_dr.py`, com **dados sintéticos** (`healthmon demo`) e **armazenamento simulado**
(servidor HTTP falso local que imita PUT, GET e listagem). **Nada foi testado contra um bucket real da Oracle.**
Antes de confiar no sistema, faça o ensaio real descrito abaixo.

Cobertos: ida e volta HMBK2; servidor sem a chave privada não decifra; chave errada falha; arquivo adulterado falha
(inclusive com checksum recalculado); backup com outra thread gravando sem parar (restaurado passa em
`integrity_check` e é consistente); envio com verificação remota; novas tentativas; falha de rede registra erro e mantém o
backup local; a URL da PAR não aparece em logs, exceções nem no estado; e um **ensaio completo de desastre** (base
sintética, backup, envio, apagar todo o `HEALTHMON_HOME`, HOME novo vazio, download, restauração com a chave e
comparação tabela a tabela por contagem e hash).

Não coberto: comportamento real da API da Oracle (ver "Não confirmado" abaixo), listagem por PAR no bucket real,
expiração da PAR, limites de taxa, bancos grandes.

## Passo a passo (pelo iPhone, no console da Oracle)

Os nomes dos menus podem variar um pouco; use o navegador em modo "Versão para computador" se o console ficar apertado.

### 1. Gerar o par de chaves (uma vez)

Feito pelo `deploy/oracle/configurar.sh` (etapa 4), que roda `healthmon chaves-recuperacao` num contêiner sem
registro de saída. A chave `HMRK-...` aparece uma vez; guarde-a no app Senhas e em papel. Em seguida o comando
pede que você **cole a chave de volta** para provar que foi guardada; só então ela passa a valer. Logo depois o
script cria um backup do banco vazio, envia ao bucket e pede a chave mais uma vez para abrir esse backup.

### 2. Criar o bucket

1. Console da Oracle > menu (três linhas) > **Storage** > **Buckets** (Object Storage).
2. Escolha o compartimento e a região da VM e toque em **Create Bucket**.
3. Nome, por exemplo, `healthmon-backups`; camada **Standard**; deixe o bucket **privado** (padrão).
4. **Obrigatório: habilite Object Versioning.** Sem ele, quem tiver a URL de acesso pode sobrescrever os backups
   com lixo. Com ele, cada sobrescrita vira uma versão nova e a anterior continua recuperável pelo console.
5. **Retenção remota**: abra o bucket > **Lifecycle Policy Rules** > **Create Rule**: ação **Delete**, objetos com mais de
   45 dias (ajuste ao seu tamanho de backup). É assim que o armazenamento remoto não cresce para sempre, porque
   **uma PAR não pode excluir objetos** (confirmado na documentação). Se versionamento estiver ligado, crie também
   uma regra para versões anteriores.

### 3. Criar a PAR

1. Dentro do bucket: **Pre-Authenticated Requests** > **Create Pre-Authenticated Request**.
2. Tipo: **Bucket**; acesso: **Permit object reads and writes** (leitura e escrita); marque
   **Enable Object Listing** (necessário para o `list_remote` e para a recuperação achar o backup mais recente).
3. Defina a validade (a documentação diz que a data é obrigatória e não tem limite máximo; sugestão: 1 ano).
   Anote a data de expiração na sua agenda.
4. **Copie a URL agora**: o console mostra a URL completa uma única vez. A URL tem o formato
   `https://objectstorage.<região>.oraclecloud.com/p/<token>/n/<namespace>/b/<bucket>/o/`
   (formato de acordo com o uso corrente; ver "Não confirmado").
5. Na VM, a URL é pedida pelo `configurar.sh` (etapa 5) com a tela oculta. Para trocá-la depois:
   `docker run --rm -it --log-driver none -v "$PWD/data:/data" healthmon:local healthmon definir-segredo OCI_PAR_URL`.
   Nunca a envie por chat, e-mail, commit nem coloque em `config.toml`. **A URL é uma credencial**: quem a tem escreve e lê no bucket.

Como só há objetos cifrados, o vazamento da PAR não expõe seus dados de saúde, mas permitiria a um terceiro
enviar ou sobrescrever objetos no bucket (apagar não é possível por PAR). Se vazar, revogue (remova a PAR no console) e crie outra.

### 4. Renovar a PAR

A PAR não pode ser editada. Antes do vencimento (ponha lembrete 30 dias antes): crie uma nova com as mesmas opções,
grave a nova com `definir-segredo OCI_PAR_URL` (comando acima), reinicie o serviço e rode o envio para conferir. Depois remova a PAR antiga.
Uma PAR também **deixa de funcionar** se o usuário que a criou for removido ou perder as permissões no bucket.
O `offsite.status()` passa a acusar `alerta` quando o último envio ok passa de 36 h ou há 2+ falhas consecutivas.

## Recuperação completa em VM nova

**Não rode a instalação normal numa VM nova quando o bucket já tem backups**: ela geraria outra chave e enviaria um
banco vazio. O script detecta esse caso e para. Use o modo de recuperação:

1. Crie a VM nova (mesmo roteiro de `IMPLANTACAO.md`, passos 2 a 4).
2. Tenha em mãos a URL de acesso do bucket (se perdeu, crie outra no console: o bucket continua lá) e a chave de recuperação.
3. Na VM:
   ```bash
   ./deploy/oracle/configurar.sh --recuperar
   ```
   O script pede a senha nova do painel e a URL do bucket, lista os backups do bucket (o mais recente primeiro) e
   deixa você escolher. Depois de digitar a chave de recuperação, ele mostra **quando o backup foi criado e quantos
   dias de dados contém** e só restaura se você digitar `RESTAURAR`. Um backup vazio ou com data incompatível com o
   nome é sinalizado. A chave pública é regravada a partir da sua chave: os backups seguintes continuam abrindo com ela.
4. O script segue para a autorização, pede a chave da Garmin (ela **não** faz parte do backup) e inicia o serviço.
5. Importe os dias posteriores ao backup: `docker compose run --rm healthmon healthmon historico --dias 7`.
6. Confira no painel os últimos dias, perfil, exames e treinos.

Para restaurar sobre um servidor que continua de pé (banco corrompido): `docker compose stop healthmon`, depois
`docker run --rm -it --log-driver none -v "$PWD/data:/data" healthmon:local healthmon restaurar` e
`docker compose up -d`. A restauração é recusada com o serviço em execução, e o banco anterior fica preservado
como `healthmon.antes-restauracao-<data>.db`.

Para **ensaiar** sem tocar no banco em uso:
```bash
docker run --rm -it --log-driver none --tmpfs /tmp -v "$PWD/data:/data" -v "$PWD/deploy:/deploy:ro" healthmon:local bash /deploy/oracle/ensaio-recuperacao.sh
```
Baixa o backup mais recente, pede a chave com a tela oculta, restaura em diretório temporário, mostra as contagens e
apaga. Faça uma vez após configurar e a cada trimestre. O ensaio mais forte é o da lista acima, numa VM descartável.

## RPO de 24 horas e o que o invalida

Backup diário significa perda máxima de **até 24 h** de dados gravados no servidor (ex.: refeições, check-ins,
pesos lançados depois do último backup). Os dados que vêm da Garmin podem ser ressincronizados.
O RPO deixa de valer quando:

- o job de backup ou o envio estão falhando (`offsite.status()['alerta']`, falhas consecutivas, envio com mais de 36 h);
- a PAR venceu, foi revogada ou o criador perdeu permissão;
- a cota do Always Free foi atingida (ver abaixo);
- você perdeu a chave de recuperação (os backups existem, mas ninguém abre, nem a Oracle, nem o servidor);
- o horário do backup foi alterado para menos de uma vez por dia;
- a VM caiu antes do primeiro envio ao bucket (o backup só existia na própria VM).

## Documentação da Oracle consultada

Confirmado:

- PAR (acesso de bucket, escrita, leitura, listagem opcional; criador precisa manter permissões; limite de 3500 bytes na
  URL; **"Pre-authenticated requests can't be used to delete buckets or objects"**; **validade obrigatória e sem limite máximo**;
  PAR não é editável): https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/usingpreauthenticatedrequests.htm
- Limites Always Free de Object Storage: 20 GB (padrão/acesso infrequente/arquivo combinados) e 50.000 requisições de API
  por mês; (para contas pagas ou com créditos: 10 GB por camada):
  https://docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm
- Regras de ciclo de vida (excluir objetos e versões antigas): https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/usinglifecyclepolicies.htm
- Versionamento (cobrado como armazenamento): https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/usingversioning.htm
- ListObjects (até 1000 objetos por chamada, paginação por `nextStartWith`/`start`, parâmetros `prefix`, `fields`):
  https://docs.oracle.com/en-us/iaas/tools/python/latest/api/object_storage/client/oci.object_storage.ObjectStorageClient.html
- Campo `access_uri` da PAR: https://docs.oracle.com/en-us/iaas/tools/python/latest/api/object_storage/models/oci.object_storage.models.PreauthenticatedRequest.html

**Não confirmado** (não foi possível verificar nas páginas acessíveis; trate como suposição até o ensaio real):

- O formato exato da URL de PAR de bucket (`.../p/<token>/n/<ns>/b/<bucket>/o/`) e o fato de o código anexar o nome do objeto
  à URL; a página de criação da PAR que tentei abrir devolveu "não encontrada".
- Que um GET na própria URL da PAR de bucket (com listagem habilitada) devolve o JSON do ListObjects com `objects[].name`
  e aceita `prefix`, `start` e `fields` (a documentação confirma o JSON e `nextStartWith` na API de listagem, não na PAR).
- Que PUT por PAR aceita objetos grandes sem upload em partes (limite não consultado; os backups são pequenos).
- Os nomes exatos dos menus do console no iPhone.
- Que as regras de ciclo de vida estão disponíveis na conta Always Free sem custo e como as requisições do healthmon
  (PUT, GET, listagem, verificação) contam nas 50.000/mês; o uso esperado é de poucas dezenas por dia.
- O código HTTP exato que a Oracle devolve ao negar um DELETE por PAR (por isso o healthmon nem tenta apagar).
