# Códigos de receita, alíquotas, prazos e base legal

## Códigos de receita (DARF / DCTF / DCTFWeb)

### IRPJ e CSLL

| Código | Tributo | Regime / natureza |
|---|---|---|
| 2089 | IRPJ | Lucro Presumido — **trimestral** |
| 2372 | CSLL | Lucro Presumido — **trimestral** |
| 0220 | IRPJ | Lucro Real trimestral |
| 6012 | CSLL | Lucro Real trimestral |
| 2362 | IRPJ | Estimativa mensal (Real anual) |
| 2484 | CSLL | Estimativa mensal (Real anual) |
| 2430 | IRPJ | Ajuste anual (Real) |
| 6773 | CSLL | Ajuste anual (Real) |
| 2456 | IRPJ | Pagamento/quitação vinculada ao Real |

Os quatro primeiros exigem **normalização mês → trimestre** antes de cruzar
(ver `armadilhas.md`, item 2).

### PIS e COFINS

| Código | Tributo |
|---|---|
| 8109 | PIS/Pasep — faturamento |
| 2172 | COFINS — faturamento |
| 6912 | PIS — não-cumulativo |
| 5856 | COFINS — não-cumulativo |

### Retenções na fonte

| Código | Natureza | Alíquota típica |
|---|---|---|
| 1708 | IRRF sobre serviços prestados por PJ | 1,5% |
| 5952 | CSRF conjunta (PIS+COFINS+CSLL) | 4,65% |
| 5979 | PIS retido | 0,65% |
| 5960 | COFINS retida | 3,00% |
| 5987 | CSLL retida | 1,00% |
| 0561 | IRRF sobre trabalho assalariado | tabela |
| 3208 | IRRF sobre aluguéis e royalties | tabela |
| 5610 / 5880 | IRRF (apurado via DCTFWeb) | — |

### Retenções financeiras (aparecem no demonstrativo de fontes pagadoras)

| Código | Natureza | Pista para reconhecer |
|---|---|---|
| 3426 | Aplicações financeiras de renda fixa (PJ) | alíquota efetiva de 15% a 22,5% |
| 5557 | Operações em bolsa (o "dedo-duro") | alíquota de **0,005%** |
| 8468 | Day-trade | alíquota de **1%** |
| 6800 | Fundos de investimento | — |
| 3277 | Cooperativa de crédito / ato cooperativo | frequentemente zero |

Rendimento alto em 5557 numa empresa de commodities costuma ser **hedge em
contratos futuros**. Vale verificar a caracterização: operação qualificada como
hedge integra o resultado operacional; se for tratada como especulativa, perdas
em renda variável só compensam ganhos da mesma natureza (Lei 8.981/95).

### Previdenciário e terceiros (DCTFWeb)

`1082`, `1099`, `1138`, `1170`, `1176`, `1191`, `1196`, `1200`, `1646`, `1213`,
`1656` — contribuição patronal, segurados, GILRAT e terceiros. O DARF numerado
da DCTFWeb agrupa vários códigos num só documento; leia o conteúdo, não o nome
do arquivo.

## Regras de apuração

### Lucro Presumido

- **Presunção**: 32% para serviços em geral; 8% para comércio, indústria e
  transporte de carga; 16% para transporte de passageiros e certos serviços;
  1,6% para revenda de combustíveis.
- **IRPJ**: 15% sobre a base + **adicional de 10%** sobre o que exceder
  R$ 20.000 × número de meses do período (R$ 60.000 no trimestre).
- **CSLL**: 9% sobre a base.
- Ganhos de capital, rendimentos de aplicações financeiras e demais receitas
  entram **integralmente** na base, sem presunção.
- **PIS/COFINS cumulativos**: 0,65% e 3,00%, sem direito a crédito.
- **Regime de caixa** é admitido (IN RFB 1.700/2017, art. 223; IN SRF 104/1998).
  A opção é feita na virada do exercício e vale para IRPJ, CSLL, PIS e COFINS.
  Em operação com prazo médio de recebimento longo, difere caixa de forma
  permanente enquanto o prazo se mantiver.

**Tese dos 8%/12% para laboratórios** (Lei 9.249/95, art. 15, §1º, III, "a";
STJ REsp 1.116.399; IN RFB 1.700/2017, art. 33): alcança serviços hospitalares e
de auxílio diagnóstico prestados conforme normas da ANVISA. **Não alcança**
análises técnicas para a indústria (CNAE 7120-1/00). Aplicar onde não cabe leva
a contencioso — desaconselhe explicitamente.

### Lucro Real

- **IRPJ**: 15% + adicional de 10%; **CSLL**: 9%.
- **Trava de 30%**: compensação de prejuízo fiscal e base negativa limitada a
  30% do lucro do período (Lei 9.065/95, arts. 15 e 16).
- **Estimativa mensal**: pode ser suspensa ou reduzida por balancete
  (Lei 8.981/95, art. 35). Empresa que já usa balancete demonstra maturidade —
  o ponto de atenção passa a ser o fechamento do ano.
- **Saldo negativo**: quando as antecipações (IRRF + estimativas) superam o
  devido no ajuste, o excesso é crédito restituível ou compensável.
  Acrescido de **Selic a partir do mês subsequente ao encerramento** do período
  de apuração até a compensação (IN RFB 2.055/2021).
- **PIS/COFINS não-cumulativos**: 1,65% e 7,6%, com crédito.

### Simples Nacional

- **Fator R** = (folha dos 12 meses + encargos) ÷ receita bruta dos 12 meses.
  Igual ou acima de 28% → Anexo III; abaixo → Anexo V (LC 123/2006, art. 18,
  §§5º-J e 5º-M).
- **RBT12** móvel define a faixa; sublimite estadual afeta ICMS/ISS.
- Optante **não sofre CSRF** (IN RFB 459/2004, art. 3º, II) — não procure
  retenção de terceiros nesse período.
- Reperformar exige as apurações do **PGDAS-D**; extratos de pagamento (DAS)
  sozinhos não bastam.

## Créditos e ressarcimento

- **Manutenção do crédito** vinculado a receita não tributada e ressarcimento em
  dinheiro ou compensação: Lei 11.033/2004, art. 17 e Lei 11.116/2005, art. 16.
- **PER/DCOMP**: IN RFB 2.055/2021.
- **Compensação não homologada**: multa isolada de 50% sobre o débito
  (Lei 9.430/96, art. 74, §17).
- **Prazo**: 5 anos (CTN, arts. 165 a 168). Para saldo negativo, conta do
  encerramento do período de apuração.
- **Desconto × ressarcimento**: descontar é imediato e gratuito; ressarcimento
  entra em fila e não rende Selic. Enquanto houver débito para absorver, o
  desconto é racional — a rotina de pedido vira necessária quando a receita
  tributada cai e o crédito deixa de ser absorvido.

## Regras setoriais frequentes

**Agro**
- Lei 10.925/2004, art. 1º, I e Decreto 5.630/2005: alíquota zero de PIS/COFINS
  para adubos e fertilizantes do Capítulo 31 da TIPI. **O benefício segue a
  destinação, não só a classificação** — Solução de Consulta COSIT nº 54/2019
  afastou a alíquota zero para ureia destinada a finalidades diversas, entre as
  quais a industrialização de outros produtos. Efeito vinculante
  (IN RFB 2.058/2021).
- Lei 12.865/2013, art. 29: suspensão de PIS/COFINS nas vendas de soja e
  derivados a pessoa jurídica.
- Crédito presumido da agroindústria: `COD_CRED` terminado em `06`.

**ICMS**
- LC 87/96, art. 33: energia elétrica com crédito para uso industrial; material
  de uso e consumo **vedado até 2033**; ativo imobilizado creditado à razão de
  1/48 via CIAP.
- Benefícios estaduais que zeram o ICMS a recolher: PRODEIC (MT, Lei estadual
  7.958/2003, com contrapartidas a FUNDES/FUNDED), FOMENTAR e PRODUZIR (GO).
  Confirme nos ajustes `E111`. **Esses benefícios se extinguem junto com o ICMS
  até 2032** — o Fundo de Compensação da EC 132/2023 cobre apenas parte e exige
  habilitação.

**Serviços**
- ISS de 2% a 5% conforme município; aparece na ECD como dedução da receita.
- Sociedades de profissões regulamentadas têm tratamento próprio no IBS/CBS
  (ver `reforma-tributaria.md`).

## Sinais de risco que merecem investigação

- **Folha inexistente com receita relevante**: execução dos serviços remunerada
  como prestação de terceiros. Se houver pessoalidade, habitualidade e
  subordinação, a requalificação gera contribuição patronal, GILRAT e terceiros
  (~28%) retroativos, mais multa. Trate como ponto a estruturar, nunca como
  acusação — e apresente a ordem de grandeza como cenário hipotético.
- **CST genérico em volume relevante** (CST 49 "outras operações"): transfere ao
  contribuinte o ônus de provar o enquadramento de cada operação.
- **Lucros a distribuir parados no passivo circulante**: além de distorcer os
  indicadores, exige deliberação formalizada.
- **Despesas nominadas a pessoas físicas ligadas, sem contrato**: risco de
  distribuição disfarçada de lucros.
- **Distribuição de lucros isenta** (Lei 9.249/95, art. 10; IN RFB 1.700/2017,
  art. 238, §2º): confirmar lastro no lucro contábil com ECD regular.
