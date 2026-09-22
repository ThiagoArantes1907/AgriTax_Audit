---
name: revisao-frigorifico
description: Metodologia AgriTax completa de revisão tributária de PIS/COFINS para frigoríficos de abate de bovinos no Lucro Real, por cruzamento eletrônico de dados do e-CAC. Use esta skill SEMPRE que o usuário mencionar revisão fiscal ou recuperação de créditos de PIS/COFINS de frigorífico, abatedouro, indústria de carnes ou agroindústria; enviar EFDs (Contribuições/ICMS), PER/DCOMPs, despachos decisórios, intimações, DARFs, parcelamentos ou relatório de situação fiscal de um cliente; pedir cotejo de ressarcimento × compensação, saldo de créditos, análise de despachos e prazos, ou diagnóstico/plano de ação tributário — mesmo que não use a palavra "revisão". Também para gerar os entregáveis padrão AgriTax (planilha decisória, levantamento, relatório Word, apresentação).
---

# Revisão PIS/COFINS — Frigorífico de Bovinos (Metodologia AgriTax)

Trabalho em **português (Brasil)**. O objetivo é sempre duplo: **maximizar o benefício de PIS/COFINS com lastro** e **proteger o cliente** de riscos, prazos e teses insustentáveis. Regra de ouro: *nenhum pedido sem escrituração; nenhuma escrituração sem prova; prazo é mérito.*

## Leituras obrigatórias antes de executar

1. **`references/premissas_legais.md`** — TODAS as premissas com vigências (regime do boi, mercado interno a alíquota zero, LC 224, expurgo do ICMS, regra de períodos fechados, prazos/ciência, mecânica PER/DCOMP, armadilhas de parsing). Ler integralmente antes de qualquer análise.
2. **`references/material_tecnico.md`** — o material aprofundado (9 seções: regime jurídico, testes A/B, créditos básicos com ranking de liquidez, operação ponta a ponta, caso numérico, blueprint de automação). Consultar por seção conforme a etapa.

## Insumos a solicitar ao usuário

- EFD-Contribuições + EFD-ICMS/IPI do período (todas as versões; o módulo escolhe a vigente). ZIP com a pasta do e-CAC serve.
- PDFs de **todos** os PER/DCOMPs + **consulta de situação** (planilha do e-CAC). Sem a situação, rodar sem a regra de fechados e sinalizar a pendência.
- Se existirem: despachos decisórios, termos de intimação, DARFs pagos, recibos de parcelamento, relatório de situação fiscal, prints da caixa postal (datas de 1ª leitura!), ECD/ECF (para imobilizado).
- Escopo temporal (ex.: "a partir de 07/2021") — aplicar via `--desde`.

## Fluxo de execução

### Etapa 1 — Inventário do acervo
Descompactar e inventariar: contagens por tipo, competências cobertas, CNPJs presentes (alertar se houver mais de um), versões originais × retificadoras, pastas de despachos/intimações/perdcomp. Registrar padrões suspeitos (retificadoras em massa numa mesma data = trabalho anterior de terceiros).

### Etapa 2 — Rodar o módulo de cruzamento
`python3 scripts/modulo_frigorifico.py <pasta_das_EFDs> --saida decisoria.xlsx [--per situacao.csv] [--desde MM/AAAA]`

O módulo pareia as EFDs por competência (versão vigente), roda os testes A1-A8 (receita/índice de exportação, presumido art. 33, alíquotas, CSTs, presumido sem exportação) e B1-B5 (notas ausentes por chave, CT-es, classes zeradas, expurgo ICMS, bloco M vazio/sem movimento) e gera a **planilha decisória padrão** (Instruções, Resumo, Presumido_Art33, Créditos, Receitas-CST, Expurgos-Riscos, Matriz de Risco). Se a consulta de situação vier em xlsx + PDFs, montar o CSV `competencia;situacao` extraindo ano/trimestre dos PDFs (pdftotext) e casando pelo número do PER — modelo em `assets/situacao_per_modelo.csv`.

**Validar o parsing contra o arquivo real** antes de confiar: receita > 0 (saídas próprias não têm C170 no ICMS — o módulo usa fallback C190/Contribuições), gado detectado, presumido escriturado coerente. Limitação conhecida: **C500 (energia) ainda não é lido — o crédito mapeado é PISO**; dizer isso em todo relatório.

### Etapa 3 — Cotejo Ressarcimento × Compensação
Extrair de cada PDF (pdftotext, e `-layout` para débitos): tipo, ano/trimestre, valores, PER inicial (DCOMPs), débitos (código, PA, principal/multa/juros). Resolver cadeias de retificação (usar só vigentes). Consolidar por crédito (contribuição × trimestre): pedido, utilizado em DCOMPs, saldo. Comparar **valor pedido × saldo ressarcível na EFD vigente** (M100/M500, SLD_CRED códigos 2xx/3xx): classificar CONFERE / SEM LASTRO (bloco M vazio) / PEDIDO MAIOR / EFD MAIOR (crédito não pedido).

### Etapa 4 — Duplicidades de quitação (o achado que salva o cliente)
Cruzar os débitos das DCOMPs vigentes com **DARFs pagos** (código + período + valores ao centavo) e com **parcelamentos** (débitos consolidados). Débito quitado por duas vias → **DCOMP a cancelar (tipo 1.7), antes de análise**. Atenção metodológica: o confronto é **agregado por código×período** quando várias DCOMPs fatiam o mesmo débito — nunca comparar o mesmo DARF contra cada fatia isoladamente.

### Etapa 5 — Despachos, prazos e ciência
De cada despacho: PER, processo, valores pedido×confirmado, decisão, DCOMPs não homologadas, valor devedor consolidado, prazos. **Ciência**: buscar a data de 1ª leitura na caixa postal (print/histórico) — leitura OU 15º dia, o que vier primeiro; manifestação = ciência + 30 dias. Conciliar a cobrança com pagamentos existentes: **DARF avulso não baixa cobrança de processo — a ação é ALOCAR o pagamento, nunca pagar de novo** (DARFs novos do processo ficam de plano B dentro da validade). Verificar situação fiscal/DAU. Se prazos foram perdidos por terceiro: montar **dossiê de responsabilização** (IDs das mensagens, datas de leitura, inércia, atos posteriores) e recomendar revogação de procurações no e-CAC — capturando os comprovantes ANTES.

### Etapa 6 — Saldo projetado com abertura
Consolidar o saldo de crédito pós-plano por componente, sem dupla contagem: (1) mapeado a escriturar em períodos abertos; (2) mapeado de períodos fechados → extemporâneo (só blocos A/C/D/F); (3) já escriturado e não pedido (básico); (4) pedidos sustentados em análise, líquidos de utilizações válidas. **Fora do saldo** (linhas informativas): teses vedadas excluídas, preclusos (→ judicial), utilização liberada por cancelamentos (passivo evitado). Conciliar tudo ao centavo — as utilizações devem fechar com as DCOMPs.

### Etapa 7 — Plano de ação em 4 fases
Fase 0 (imediata): cancelamentos, alocações, cancelar pedidos de tese vedada, revogar acessos de terceiros, rotina de caixa postal. Fase 1 (30-60d): retificar EFDs — **prescrição primeiro (mês a mês!), depois o trimestre do maior pedido pendente**; débito LC 224 espontâneo; sequência EFD→DCTF→ECF→DIRBI. Fase 2 (60-90d): reposicionar PERs (para cima E para baixo), intimações, débitos remanescentes (condições de RJ quando aplicável). Fase 3 (90d+): energia/C500, imobilizado via ECD, rotina trimestral, transição CBS 2027.

### Etapa 8 — Entregáveis padrão AgriTax
- **Planilha decisória** (saída do módulo) + **levantamento** (abas: Saldo Projetado, Despachos e Prazos, Situação Fiscal, Cancelamentos DCOMP, Cotejo por Crédito, Saldo EFD × Pedido, Mapeado × PER, Documentos, Alertas).
- **Relatório Word** ao cliente: capa, quadro geral em números, situações no formato **"O que encontramos / O impacto / O que faremos"**, saldo projetado com tabela, plano em fases, decisões requeridas. Linguagem de empresário, sem sigla sem explicação; a contabilidade parceira nunca é exposta.
- **Apresentação** 16:9 (9 slides: capa, acervo, recuperar × corrigir, situações, saldo com gráfico, fases, urgências, decisões com checkbox, fechamento proteger+recuperar). Versão anonimizada para prospecção: remover nome/CNPJ E menções a recuperação judicial.
- Identidade visual: verde 8CBE30, chumbo 404040, dourado E8A800, vermelho C0392B para riscos, Arial. Validar planilhas com recalc e documentos visualmente antes de entregar.

## Princípios inegociáveis

1. Vigência por competência — nunca a lei de hoje sobre o passado.
2. Presumido do art. 33 sem exportação = ZERO na via administrativa (COSIT 80/2021); o caminho do MI é art. 17/11.033 + art. 16/11.116.
3. Período fechado remapeia oportunidade só via blocos A/C/D/F; risco fechado vai à Matriz de Risco.
4. Todo número entregue tem trilha: documento → EFD → planilha → relatório, conciliado ao centavo.
5. Cada decisão de risco (estorno, cancelamento, via judicial, responsabilização) é formalizada como decisão do cliente — nunca tomada em silêncio.
