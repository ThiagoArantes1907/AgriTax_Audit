> **Instalação nesta plataforma (22/09/2026):** a skill vive em `.claude/skills/revisao-frigorifico/` (SKILL.md, `references/premissas_legais.md`, `references/material_tecnico.md`, `scripts/modulo_frigorifico.py`, `assets/`). A convenção `clientes/<nome>/01_acervo|02_analise|03_entregaveis` deste README mapeia para `engajamentos/<CLIENTE>/<cnpj>/raw` (somente leitura, custódia SHA-256) | `analise/` | `entregaveis/` do PT-AF-003. Rodar o motor com o Python 3.11 (`C:\Users\thiag\AppData\Local\Programs\Python\Python311\python.exe`), que tem openpyxl/pyyaml/python-docx/python-pptx; o 3.13 do PATH não tem. Novos flags do motor: `--desde MM/AAAA`, `--sem-contrib`; consolida matriz+filiais e trata regime cumulativo (0110) e EFD-Contribuições ausente.

# Revisão Tributária de PIS/COFINS — Frigoríficos (Metodologia AgriTax)

**Repositório de conhecimento e execução para o Claude Code.** Contém tudo o que é necessário para conduzir, do inventário ao relatório final, a revisão completa de PIS/COFINS de um frigorífico de abate de bovinos no Lucro Real — por cruzamento eletrônico integral dos dados do e-CAC — e replicar o trabalho em quantos clientes forem necessários.

> **Claude Code: leia este README inteiro e os dois arquivos de `conhecimento/` antes de tocar em qualquer arquivo de cliente.** Todo o trabalho é em português (Brasil). As regras aqui não são sugestões — são a metodologia da casa, validada em casos reais.

---

## 1. O que este repositório faz

Dado o acervo do e-CAC de um frigorífico (EFDs, PER/DCOMPs, consulta de situação, despachos, DARFs, parcelamentos, situação fiscal), a metodologia produz:

| Saída | O que responde |
|---|---|
| **Planilha decisória** (motor Python) | Créditos identificados documento a documento, riscos por competência, presumido do art. 33, matriz de risco |
| **Levantamento PER × DCOMP** | Quanto foi pedido, quanto foi compensado, saldo, lastro na EFD, duplicidades com DARF/parcelamento |
| **Mapa de despachos e prazos** | O que foi indeferido, quando venceu cada prazo, o que ainda é defensável |
| **Saldo projetado com abertura** | O número final de crédito sustentável, componente a componente, conciliado ao centavo |
| **Relatório executivo + apresentação** | Entregáveis para o cliente, na identidade AgriTax |

Resultados de referência (caso Brasluz, 2026): saldo projetado de **R$ 3,99 mi** com lastro documental, **R$ 1,94 mi** de compensações em duplicidade identificadas e canceladas, **R$ 673 mil** de preclusão mapeada com dossiê de responsabilização do terceiro.

---

## 2. Estrutura do repositório

```
revisao-frigorifico-claude-code/
├── README.md                     ← este arquivo (documento-mestre)
├── CLAUDE.md                     ← regras operacionais que o Claude Code carrega sempre
├── conhecimento/
│   ├── premissas_legais.md       ← TODAS as premissas com vigências (leitura obrigatória)
│   └── material_tecnico.md       ← material aprofundado em 9 seções (consulta por etapa)
├── scripts/
│   └── modulo_frigorifico.py     ← motor de cruzamento (testes A1-A8 e B1-B5)
├── modelos/
│   └── situacao_per_modelo.csv   ← modelo do CSV de situação dos PERs
├── assets/
│   └── logo_agritax.png          ← logo oficial (fundo transparente) p/ entregáveis
└── clientes/
    └── <nome-do-cliente>/        ← um diretório por frigorífico (criar no onboarding)
        ├── 01_acervo/            ← arquivos do e-CAC (NUNCA editar — só ler)
        ├── 02_analise/           ← JSONs, CSVs e planilhas de trabalho
        └── 03_entregaveis/       ← decisória, levantamento, relatório, apresentação
```

**Convenção inegociável:** `01_acervo/` é somente leitura. Toda transformação nasce em `02_analise/`; todo produto final vai para `03_entregaveis/`.

---

## 3. Setup

```bash
pip install openpyxl                       # planilhas
# pdftotext (poppler) — extração dos PDFs de PER/DCOMP e despachos:
#   Linux:   apt install poppler-utils
#   Windows: winget install oschwartz10612.Poppler  (ou choco install poppler)
# Entregáveis Word/PowerPoint (opcional, só na etapa 8):
npm install docx pptxgenjs sharp react react-dom react-icons
```

Python 3.10+. O motor não tem dependência além de `openpyxl`. Encoding das EFDs: **latin-1**, sempre.

---

## 4. Onboarding de um frigorífico novo

1. **Criar** `clientes/<nome>/` com as três subpastas e descompactar o acervo em `01_acervo/`.
2. **Solicitar ao titular, se faltarem:** EFD-Contribuições **e** EFD-ICMS de todo o período (baixadas do ReceitanetBX — atenção: o ReceitanetBX às vezes vem só com EFD-ICMS/ECD/ECF; a EFD-Contribuições é um download separado); PDFs de **todos** os PER/DCOMPs; consulta de situação (xlsx do e-CAC); e, se existirem, despachos, intimações, DARFs, parcelamentos, relatório de situação fiscal e **print da caixa postal com a coluna "Data de 1ª leitura"**.
3. **Definir com o titular:** escopo temporal (`--desde MM/AAAA` — regra prática: 5 anos para trás da data atual, porque a prescrição corre mês a mês) e o critério de destino dos extemporâneos quando o primeiro período aberto tiver PER pendente.
4. **Executar as 8 etapas da seção 5**, gravando cada saída no lugar da convenção.
5. **Nunca pular a etapa 1 (inventário)** — é ela que revela múltiplos CNPJs, retificadoras em massa de terceiros, ECDs vazias e acervos incompletos.

---

## 5. O fluxo de revisão em 8 etapas

### Etapa 1 — Inventário do acervo
Contar arquivos por tipo, mapear competências cobertas e CNPJs presentes (matriz + filiais são normais — a EFD-Contribuições é centralizada na matriz, a EFD-ICMS é por estabelecimento; o motor consolida sozinho). Alertar sobre: retificadoras em massa numa mesma data (trabalho anterior de terceiro), competências faltantes, ECD/ECF entregues vazias (já aconteceu: 4 de 5 ECDs de um cliente tinham uma conta de R$ 0,01 e nenhum lançamento — isso é um achado, não um detalhe), arquivos de CNPJs estranhos ao grupo.

### Etapa 2 — Motor de cruzamento
```bash
python3 scripts/modulo_frigorifico.py clientes/<nome>/01_acervo/<pasta_EFDs> \
    --saida clientes/<nome>/03_entregaveis/decisoria_<nome>.xlsx \
    --per clientes/<nome>/02_analise/situacao_per.csv \
    --desde 09/2021
```
O motor: escolhe a **versão vigente** de cada competência (retificadora de maior timestamp; EFD-ICMS com COD_FIN=1 sobre 0), consolida matriz+filiais (`EfdIcmsConsolidada`, com prefixo de CNPJ em `cod_item`/`cod_part` — as numerações são independentes por estabelecimento), roda os testes **A1-A8** (receita e índice de exportação, presumido do art. 33, alíquotas por vigência, CSTs, presumido escriturado sem exportação) e **B1-B5** (notas presentes no ICMS e ausentes na Contribuições — chave a chave; CT-es; classes de insumo zeradas; expurgo do ICMS; bloco M vazio ou EFD sem movimento) e gera a planilha decisória padrão.

O CSV `--per` segue `modelos/situacao_per_modelo.csv` (`competencia;situacao`). Quando a consulta vier como xlsx sem o período de apuração, extrair ano/trimestre dos **PDFs dos PERs** (`pdftotext`, campos "Ano" e "Nº Trimestre") e casar pelo número do documento.

**Validar o parsing contra o arquivo real antes de confiar em qualquer número:** receita > 0 (saídas de emissão própria **não têm C170** na EFD-ICMS — o motor cai para C190 e busca o recorte por NCM na Contribuições), gado detectado (PF pelo CPF no 0150; NF de produtor mod. 04 com COD_SIT 08 é válida), presumido escriturado coerente com os M100/M500.

### Etapa 3 — Cotejo Ressarcimento × Compensação
Extrair de cada PDF (`pdftotext`; usar `-layout` para os blocos de débito): tipo de documento, ano/trimestre do crédito, valores, PER inicial (nas DCOMPs) e os débitos (código da receita, PA, principal/multa/juros — atenção: no bloco do débito, o regex ingênuo de código captura o "0001" do CNPJ; ancorar no rótulo "Código da Receita/Denominação"). Resolver **cadeias de retificação** (só versões vigentes; excluir "Retificado"/"Cancelado"). Consolidar por crédito (contribuição × trimestre): pedido, utilizado, saldo. Comparar **valor pedido × saldo ressarcível na EFD vigente** (Σ SLD_CRED dos M100/M500, códigos 2xx/3xx) e classificar: `CONFERE` / `SEM LASTRO (bloco M vazio)` / `PEDIDO MAIOR` / `EFD MAIOR (crédito não pedido)`.

### Etapa 4 — Duplicidades de quitação
Cruzar os débitos das DCOMPs vigentes com **DARFs pagos** (código + período + valores ao centavo) e com **parcelamentos** (débitos consolidados no recibo). Débito quitado por duas vias → **DCOMP a cancelar (tipo 1.7), antes de qualquer análise**. Regra metodológica aprendida a duro custo: quando várias DCOMPs fatiam o mesmo débito, o confronto é **agregado por código × período** — nunca comparar o mesmo DARF contra cada fatia isoladamente (gera falso positivo em massa).

### Etapa 5 — Despachos, prazos e ciência
De cada despacho: PER, processo, quadro pedido × confirmado, decisão, DCOMPs não homologadas, valor devedor consolidado. **Ciência** pela caixa postal: data de 1ª leitura OU 15º dia do envio, o que vier primeiro; manifestação de inconformidade = ciência + 30 dias. Conciliar a cobrança com pagamentos já feitos: **DARF avulso (só código+PA) não baixa cobrança de processo — a ação é ALOCAR o pagamento ao processo, nunca pagar de novo** (o DARF novo emitido pelo sistema fica como plano B dentro da validade, para evitar Dívida Ativa). Cruzar com o relatório de situação fiscal (DAU, SISPAR, parcelamentos ativos, recuperação judicial, corresponsabilidades). Prazos perdidos por terceiro → montar **dossiê de responsabilização** (IDs das mensagens, datas de leitura, inércia, atos posteriores) e **capturar os comprovantes de leitura ANTES de revogar as procurações**.

### Etapa 6 — Saldo projetado com abertura
Consolidar o saldo de crédito pós-plano **sem dupla contagem**, em quatro componentes: (1) mapeado a escriturar em períodos abertos; (2) mapeado de períodos fechados → apropriação extemporânea (**só blocos A/C/D/F** — ver seção 6); (3) já escriturado e nunca pedido (parcela básica); (4) pedidos sustentados em análise, líquidos das utilizações válidas. **Fora do saldo, como linhas informativas:** teses vedadas excluídas, preclusos (→ via judicial), utilização liberada por cancelamentos (é passivo evitado, não crédito). A conciliação tem de fechar ao centavo — as utilizações somadas devem bater com as DCOMPs uma a uma.

### Etapa 7 — Plano de ação em 4 fases
**Fase 0 (imediata):** cancelamentos de DCOMP, alocação de pagamentos, cancelamento de pedidos em tese vedada, revogação de acessos de terceiros, rotina de caixa postal. **Fase 1 (30-60d):** retificar EFDs — **prescrição primeiro (2021 expira mês a mês!), depois o trimestre do maior pedido pendente**, antes do próximo lote de despachos da DRF; regularizar espontaneamente o débito da LC 224 (10% sobre vendas internas desde 04/2026); sequência declaratória EFD → DCTF → ECF → DIRBI. **Fase 2 (60-90d):** reposicionar PERs (para cima onde a EFD sustenta mais, para baixo onde o pedido excede o lastro), responder intimações, tratar débitos remanescentes (condições de RJ quando aplicável). **Fase 3 (90d+):** energia/C500, imobilizado via ECD/notas 1551-2551, rotina de ressarcimento trimestral, transição CBS 2027.

### Etapa 8 — Entregáveis padrão AgriTax
- **Planilha decisória** (saída do motor) + **levantamento** com as abas: Saldo Projetado, Despachos e Prazos, Situação Fiscal, Cancelamentos DCOMP, Cotejo por Crédito, Saldo EFD × Pedido, Mapeado × PER, Documentos, Alertas.
- **Relatório Word** ao cliente: capa com logo, quadro geral em números, cada situação no formato **"O que encontramos / O impacto / O que faremos"**, saldo projetado, plano em fases, seção de decisões requeridas. Linguagem de empresário (nenhuma sigla sem explicação); parceiros do cliente (a contabilidade) nunca são expostos — a atribuição de falhas é factual e nominal a quem deu causa.
- **Apresentação 16:9** (9 slides): capa · acervo analisado · recuperar × corrigir · tabela das situações · saldo com gráfico · plano em fases · urgências da semana · decisões com checkbox · fechamento "proteger + recuperar". Versão anonimizada para prospecção: remover nome/CNPJ **e** menções a recuperação judicial.
- **Identidade visual:** verde `#8CBE30`, chumbo `#404040`, dourado `#E8A800`, vermelho `#C0392B` para riscos, fonte Arial, logo em `assets/`. Validar planilhas com recálculo e documentos com inspeção visual antes de entregar.

---

## 6. Regras e premissas inegociáveis (resumo)

O detalhe completo, com vigências e fundamentos, está em **`conhecimento/premissas_legais.md`** — leitura obrigatória. O esqueleto:

1. **Vigência por competência, sempre.** Expurgo do ICMS da base só a partir de 05/2023 (Lei 14.592). LC 224 (10% da alíquota nas vendas internas) só a partir de 04/2026. Nunca aplicar a regra de hoje ao passado.
2. **O regime do boi é a Lei 12.058/2009 (arts. 32-34)** — não o art. 8º da Lei 10.925. Presumido do art. 33 = 50% das alíquotas, **condicionado à exportação** (SC COSIT 80/2021). **Frigorífico 100% mercado interno tem presumido ZERO na via administrativa** — pedido com código 206 vinculado a MI é indeferimento anunciado.
3. **O caminho de caixa do mercado interno** é o crédito básico com manutenção pelo **art. 17 da Lei 11.033/2004** e ressarcimento pelo **art. 16 da Lei 11.116/2005** (vendas a alíquota zero mantêm e monetizam os créditos).
4. **Períodos fechados** (PER deferida/análise concluída/homologado): oportunidades remapeiam para apropriação extemporânea no primeiro período aberto, **somente benefícios inseríveis por documento — blocos A, C, D e F** da EFD-Contribuições; ajuste puro de apuração (bloco M) não entra em período de destino. Riscos de período fechado vão para a **Matriz de Risco**, nunca para ação de correção.
5. **Prazo é mérito.** Ciência DTE = leitura ou 15º dia; manifestação = +30 dias; prescrição do crédito = 5 anos, mês a mês. Nos casos reais, mais valor se perdeu por prazo do que por tese.
6. **Nenhum pedido sem escrituração; nenhuma escrituração sem prova.** A sequência EFD → DCTF → ECF → DIRBI → PER → DCOMP não admite inversão — a inversão é a origem dos indeferimentos em massa.
7. **Todo número tem trilha** — documento → EFD → planilha de análise → relatório — e concilia ao centavo. Um valor que não fecha é um valor que não sai do repositório.
8. **Multa isolada de 50% da compensação não homologada é inconstitucional** (Tema 736/STF) — o custo real da não homologação é mora de 20% + juros, administrável se antecipado.
9. **CBS 2027:** o regime de PIS/COFINS acaba ao fim de 2026 (corrida contra o fim do regime); na CBS, o presumido do produtor rural passa a existir **sem a trava da exportação** — a tese vedada hoje vira legítima, preparar o cliente.

### Decisões que o Claude Code NUNCA toma sozinho — sempre pergunta ao titular
- Cancelar/retificar qualquer PER ou DCOMP (a análise recomenda; o titular autoriza).
- Estornar presumido escriturado ou manter como tese com risco assumido.
- Acionar a via judicial para créditos preclusos.
- Critério de destino de extemporâneos quando o período de destino tem PER pendente.
- Qualquer atribuição de responsabilidade a terceiros em documento entregável.
- Pagar qualquer DARF (especialmente: nunca sugerir pagamento sem antes conciliar com pagamentos e compensações existentes — a regra da alocação).

---

## 7. Armadilhas de parsing (aprendidas em arquivo real)

| Armadilha | Consequência se ignorada | Tratamento |
|---|---|---|
| Saídas de emissão própria sem C170 na EFD-ICMS | Receita = 0, índice de exportação errado | Fallback: Contribuições C170 → ICMS C170 → C190 agregado |
| Múltiplas versões por competência (+ arquivos `.rec`) | Analisar versão morta | Retificadora de maior timestamp; ICMS COD_FIN=1 > 0; ignorar `.rec` |
| Assinatura digital binária no fim do arquivo | Linhas-lixo no parse | Só processar linhas iniciadas em `\|`; encoding latin-1 |
| Matriz + filiais (numerações de item/participante independentes) | Colisão de códigos, itens trocados | `EfdIcmsConsolidada` prefixa `cod_item`/`cod_part` por CNPJ |
| Offset de campos no split (`strip("\|")` ou não) | CFOP lido como CST, valores errados | Padronizar UM split e validar com CFOPs conhecidos antes de somar |
| Regex de código de débito em PDF de DCOMP | Captura "0001" do CNPJ | Ancorar no rótulo "Código da Receita/Denominação"; `-layout` |
| **C500 (energia elétrica) não é lido pelo motor** | Crédito mapeado é PISO | Declarar em todo relatório; incorporar C500 é o item nº 1 do roadmap |
| ECD/ECF entregues vazias | Levantamento de imobilizado impossível pela contabilidade | Usar notas CFOP 1551/2551 das EFDs + L100 da ECF vigente; registrar como achado grave |

---

## 8. Casos de referência (o que cada um ensinou)

- **Brasluz (Luziânia-GO, 2026)** — o caso-escola completo: bloco M vazio por 26 meses, R$ 2,42 mi pedidos sem lastro, presumido MI de R$ 1,67 mi em tese vedada, 8 despachos com preclusão confirmada por leitura, duplicidades DARF/parcelamento de R$ 1,94 mi, alocação de pagamentos em vez de repagamento, empresa em RJ. Entregáveis-modelo em `clientes/` quando o histórico for migrado.
- **Frigorífico Modelo (BA)** — matriz+filial, gado majoritariamente de PJ, industrialização por encomenda (CFOPs 5924/5125), ECDs vazias em 4 de 5 anos, imobilizado levantado por notas 1551/2551 (R$ 2,17 mi em máquinas → ~R$ 200 mil de crédito imediato pela Lei 11.774), carteira previdenciária separada do escopo PIS/COFINS.

---

## 9. Roadmap do motor

1. **C500/energia elétrica** na EFD-ICMS (maior lacuna atual — o mapeado é piso).
2. Cotejo automatizado com DCTF (débito confessado × quitações declaradas).
3. Testes do bloco C do plano original (imobilizado via ECD, quando a ECD existir de verdade).
4. Situação de PER a nível competência × contribuição (hoje é por competência).
5. Geração assistida dos entregáveis Word/PPTX a partir de templates versionados neste repo.

---

*Metodologia AgriTax Tributário & Contábil — Thiago Arantes. Uso interno. Cada premissa legal referencia a norma e a vigência em `conhecimento/premissas_legais.md`; em caso de conflito entre este README e aquele arquivo, o arquivo de premissas prevalece.*
