# Cruzamentos — o que cada confronto revela

Todo cruzamento usa a mesma chave de conciliação: **(CNPJ, código-base de
receita, competência canônica)**.

- **Código-base**: os 4 dígitos principais. Sub-códigos (`6912-01`) existem nos
  DARFs e não na EFD — casar pela base e somar os dois lados evita falsos "só de
  um lado". Para o Simples, use um pseudocódigo (`SIMPLES-COFINS`).
- **Competência canônica**: `AAAA.MM`, `AAAA.NT` ou `AAAA`. Códigos trimestrais
  (2089, 2372, 0220, 6012) precisam de normalização mês → trimestre.
- **Tolerância**: R$ 0,05 em toda comparação.
- **Versão ativa**: aplicar antes de agregar (ver `armadilhas.md`, item 6).

---

## Contábil × fiscal

**Confronta**: resultado da ECD × resultado declarado na ECF.

**Revela**: escrituração contábil que não conversa com a fiscal — normalmente
erro de transporte ou uso de bases diferentes.

**Cuidado**: no Real trimestral, o resultado da ECF é por trimestre; o anual é a
soma. Ao ler `J150`, valide o lado das colunas pela regra "anual = soma dos
trimestres".

**Sinal de qualidade forte**: quando receita da ECD, da ECF e da EFD batem **ao
centavo**, diga isso ao cliente — é incomum e indica disciplina na origem dos
dados.

---

## Escriturado × declarado

**Confronta**: o que a EFD-Contribuições (Bloco M) e a ECF apuraram × o que a
DCTF/DCTFWeb confessou.

**Revela**:
- `Divergente`: apurou um valor e confessou outro.
- `Só na escrituração`: apurou e não confessou — risco de omissão.
- `Só na DCTF/DCTFWeb`: confessou sem escrituração correspondente. Muito comum e
  legítimo no previdenciário e nas retenções, que não passam pela EFD. Não trate
  como achado sem antes verificar a natureza do código.

---

## Confissão × quitação

**Confronta**: DCTF + DCTFWeb + Simples × DARF + DAS + compensações.

**Revela**:
- `Saldo a pagar`: confessado e não quitado.
- `Sem declaração`: quitado sem confissão localizada.
- `Pago a maior`: quitação acima do confessado — frequentemente é crédito.

**Trabalhe só com o principal.** Multa e juros nos DARFs distorcem o confronto.

**Antes de qualificar qualquer linha, cheque a cobertura da fonte** — este é o
cruzamento que mais produz falso positivo por acervo incompleto
(`armadilhas.md`, item 8).

---

## Pedido × lastro (PER/DCOMP × crédito escriturado)

**Confronta**: créditos pedidos em PER/DCOMP × crédito escriturado no período.

**Revela**:
- Pedido acima do lastro → risco de glosa e multa isolada de 50%.
- Crédito escriturado sem pedido → oportunidade com prazo de decadência.

**Três cuidados decisivos:**

1. **Deduplicar os pedidos** antes de somar (`armadilhas.md`, item 7).
2. **Separar por tributo a partir do fato**, não do código: `M100` (PIS) e `M500`
   (COFINS) compartilham o mesmo `COD_CRED` (ex.: 101). Agrupar por código
   mistura os dois tributos e o pedido nunca fecha. Feito certo, o pedido bate
   com o lastro ao centavo.
3. **Fluxo não é estoque** (`armadilhas.md`, item 1). Para dimensionar pedido,
   vá ao Bloco 1100/1500.

---

## Retificadoras

**Confronta**: original × retificadora da mesma competência.

**Revela**: o que mudou e por quê. Uma retificadora que altera débito merece
explicação; uma que corrige omissão de crédito é boa notícia.

**Método**: comparar conjunto de linhas dos dois arquivos e isolar apenas os
registros de conteúdo fiscal. Numa retificação bem feita, a diferença é pequena e
localizada — se muitas linhas mudaram, investigue.

---

## Retenções: escriturado × informado por terceiros

**Confronta**: F600 e deduções da ECF × demonstrativo de fontes pagadoras (DIRF)
no e-CAC.

**Revela**: crédito não aproveitado ou exposição documental. Interpretação em
`armadilhas.md`, item 11.

---

## EFD-ICMS × EFD-Contribuições

**Confronta**: entradas e saídas registradas nas duas escriturações do mesmo
período.

**Revela**: aquisições ausentes na EFD-Contribuições (crédito que deixou de
existir) e diferenças de enquadramento entre os dois tributos.

**Assimetria clássica**: créditos de entrada existirem no ICMS e não no
PIS/COFINS — as mesmas compras deveriam alimentar os dois.

**Cuidado**: níveis de agregação diferentes produzem números incompatíveis
(`armadilhas.md`, item 12). Parte da diferença é legítima — a EFD-ICMS registra
remessas, devoluções e transferências que não são receita.

---

## Reperformance

Não é confronto entre fontes: é **refazer a conta** a partir da base declarada.

- **Presumido**: aplicar a presunção sobre a receita, somar demais receitas,
  calcular 15% + adicional e 9% de CSLL, deduzir retenções. Comparar com o
  declarado.
- **Real**: verificar a trava de 30%, a composição do ajuste e — ponto que rende
  achado com frequência — se as estimativas pagas foram deduzidas
  (`armadilhas.md`, item 10).
- **Simples**: RBT12 móvel, anexo, Fator R mês a mês, sublimite.

Reperformance sem achado **também é resultado** e vale ser dito: "24 apurações
refeitas, nenhum erro" é uma frase que constrói confiança.
