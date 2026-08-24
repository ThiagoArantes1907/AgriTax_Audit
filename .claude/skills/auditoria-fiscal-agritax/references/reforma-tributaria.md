# Reforma Tributária do Consumo — premissas do simulador

Base normativa: **EC 132/2023** e **LC 214/2025**. Tudo aqui é parametrizável de
propósito — a alíquota de referência ainda será fixada por lei e resolução do
Senado. Deixe isso explícito em qualquer material entregue ao cliente.

## Alíquota de referência

Estimativa oficial do Ministério da Fazenda: **26,5%**, composta por
**CBS 8,8%** e **IBS 17,7%**. Trate como projeção, nunca como alíquota vigente.

## Calendário da transição

| Ano | CBS | IBS | PIS/COFINS | ICMS/ISS | Observação |
|---|---|---|---|---|---|
| 2026 | 0,9% | 0,1% | integral | integral | Ano-teste: o novo tributo é **compensável** com o PIS/COFINS devido — efeito de caixa praticamente nulo para quem cumpre a obrigação acessória |
| 2027 | integral | 0,1% | **extintos** | integral | IPI reduzido a zero (salvo ZFM); Imposto Seletivo em vigor |
| 2028 | integral | 0,1% | — | integral | mantém 2027 |
| 2029 | integral | 1/10 | — | 90% | início da transição do IBS |
| 2030 | integral | 2/10 | — | 80% | |
| 2031 | integral | 3/10 | — | 70% | |
| 2032 | integral | 4/10 | — | 60% | |
| 2033 | integral | integral | — | **extintos** | regime pleno |

No ano-teste, modele o tributo novo como abatimento do PIS/COFINS devido — não
como custo adicional. Quem não cumprir a acessória perde a compensação.

## Regimes diferenciados (LC 214/2025)

| Redução | Alcance | Base |
|---|---|---|
| 60% | Produtos agropecuários, aquícolas, pesqueiros, florestais e extrativistas vegetais **in natura**; insumos agropecuários | arts. 138 e 145 |
| 60% | Serviços de saúde | art. 128 e anexos |
| 60% | Serviços de educação | art. 128 e anexos |
| 30% | Serviços de **profissões regulamentadas** prestados por sociedade de profissionais | art. 127 |
| Zero | Cesta Básica Nacional | art. 125 e Anexo I |

**O enquadramento é por operação (NBS/NCM), não por CNAE.** O CNAE serve como
bússola no diagnóstico rápido; a conclusão exige olhar o produto ou serviço.
Um atacadista de soja, por exemplo, vende produto com redução de 60% ainda que
seu CNAE seja de comércio.

**A redução de 30% das profissões regulamentadas** costuma ser o item de maior
valor num diagnóstico de prestador de serviços — e depende da sociedade ser
composta por profissionais habilitados na atividade-fim. Evidências úteis:
inscrição em conselho profissional (aparece na DRE como despesa), objeto social,
qualificação dos sócios. Classifique como **indiciário** até ver o contrato
social, e mostre os dois cenários (com e sem o redutor): a diferença costuma ser
o argumento mais forte da proposta.

## Crédito no novo modelo

**Crédito financeiro amplo** (art. 47): credita-se toda aquisição de bens e
serviços usada na atividade econômica. Passam a gerar crédito itens hoje vedados
ou restritos:

- material de uso e consumo
- energia elétrica integral, sem prova de aplicação industrial
- serviços tomados de terceiros em geral
- **ativo imobilizado com crédito integral e imediato** — sem CIAP 1/48
- fretes e armazenagem
- comunicação, software e licenças

**Não geram crédito**: folha de pagamento e encargos (não há incidência),
tributos e multas, despesas financeiras (regime específico, arts. 182 e
seguintes), depreciação (o crédito ocorre na aquisição do bem) e aquisições de
pessoa física não contribuinte — salvo crédito presumido.

**Aquisições de pessoa física** merecem tratamento à parte em qualquer projeção
com fornecedores rurais. Adote crédito zero como hipótese conservadora,
identifique o volume pelo `0150` (CPF sem CNPJ) e quantifique quanto vale cada
ponto percentual de crédito presumido que a regulamentação vier a conceder. Ver
`armadilhas.md`, item 13.

## Método de projeção

Trabalhe a preços de hoje, com receita constante, para isolar o efeito
tributário do efeito de crescimento:

```
carga do ano = tributos legados × fração ainda devida
             + (receita × alíquota nova efetiva − créditos ampliados)

alíquota nova efetiva = (CBS × fração + IBS × fração) × (1 − redutor)
créditos = (base creditável − aquisições de PF) × alíquota das entradas
           × fator de aproveitamento
```

Use fator de aproveitamento conservador (≈90%): nem todo fornecedor estará no
regime regular. Aplique piso zero no tributo novo líquido, mas **investigue**
quando o crédito superar o débito — costuma ser base atípica
(`armadilhas.md`, item 14).

## O que decide o impacto real: quem é o cliente

O aumento nominal só vira perda quando não pode ser repassado.

- **Receita B2B para contribuinte do regime regular**: o adquirente credita
  integralmente o IBS/CBS destacado, então o aumento é neutro na cadeia — **desde
  que o preço seja renegociado**. Contrato longo com preço fechado e sem cláusula
  de revisão transfere todo o aumento para a margem.
- **Receita para consumidor final, Simples ou não contribuinte**: impacto direto.

Meça a fatia B2B com dados reais: a base das retenções no `F600` é um excelente
proxy, porque só PJ retém. Sem essa segregação, a conta muda completamente e a
projeção perde sentido — se não der para medir, diga que é o primeiro dado a
levantar.

## Pontos que sempre entram no plano de transição

1. Cumprir a obrigação acessória do ano-teste (destaque de CBS/IBS nos
   documentos) — sem isso, perde-se a compensação.
2. Segregar o faturamento por tipo de adquirente.
3. Revisar contratos longos: cláusula de revisão por alteração tributária.
4. Mapear fornecedores que não geram crédito cheio (Simples, produtor rural PF,
   MEI) — comprar deles passa a custar mais caro.
5. Reprogramar o cronograma de CAPEX: crédito integral e imediato sobre
   imobilizado a partir de 2027 muda o melhor momento de investir.
6. Simular o **split payment** no fluxo de caixa: o tributo passa a ser retido na
   liquidação financeira, e quem financiava giro com o prazo entre faturar e
   recolher perde esse fôlego.
7. Para quem usa benefício estadual de ICMS: planejar a reposição, porque o
   benefício se extingue com o imposto até 2032.
8. Reavaliar o enquadramento em regime diferenciado por linha de receita.

## Regimes específicos

Serviços financeiros, planos de saúde, bens imóveis, combustíveis, cooperativas,
hotelaria, bares e restaurantes, aviação e SAF têm regras próprias que exigem
análise dedicada. Sinalize a limitação em vez de aplicar o modelo geral.
