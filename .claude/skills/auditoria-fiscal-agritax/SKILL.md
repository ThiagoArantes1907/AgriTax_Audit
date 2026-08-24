---
name: auditoria-fiscal-agritax
description: >
  Método completo de auditoria e diagnóstico fiscal-contábil brasileiro a partir
  de arquivos oficiais (SPED EFD-Contribuições, EFD-ICMS/IPI, ECD, ECF, DCTF,
  DCTFWeb, DARF, PER/DCOMP, extratos do Simples, situação fiscal e demonstrativo
  de fontes pagadoras do e-CAC). Use SEMPRE que o pedido envolver ler ou cruzar
  arquivos SPED, revisar apuração de IRPJ/CSLL/PIS/COFINS/ICMS, procurar créditos
  a recuperar, saldo negativo, ressarcimento, PER/DCOMP, retenções na fonte,
  qualidade dos números contábeis, impacto da Reforma Tributária (CBS/IBS), ou
  montar diagnóstico/proposta para um cliente contábil — mesmo que o usuário não
  cite "auditoria" e apenas peça "dá uma olhada nesses arquivos da empresa X",
  "quanto ela tem a recuperar", "revisa a contabilidade dela" ou "faz o
  inventário dessa pasta". Também vale quando aparecerem siglas como EFD, ECD,
  ECF, DCTFWeb, PER/DCOMP, DIRF, CST, CFOP, Bloco M, saldo credor ou balancete
  de suspensão.
---

# Auditoria fiscal e contábil a partir de arquivos oficiais

Este método nasceu de auditorias reais em empresas de agronegócio, indústria,
comércio atacadista e serviços. Cada regra aqui existe porque algum número saiu
errado antes — o valor da skill está tanto no que ela manda fazer quanto nas
armadilhas que ela evita.

## O que torna esse trabalho confiável

Três compromissos sustentam tudo. Se algum for quebrado, o diagnóstico vira
chute bem formatado — e num relatório que vai para a diretoria de um cliente,
isso custa a relação inteira.

**Todo número é rastreável até um documento oficial.** Nunca aplique média de
mercado ao caso concreto e apresente como se fosse do cliente. Quando usar
referência setorial, diga que é referência.

**A ausência de dado é informação, não silêncio.** Acervo incompleto não pode
empobrecer a análise nem virar afirmação falsa. Cada medida carrega fonte e
nível de confiança: `confirmado` (lido no arquivo oficial), `indiciário` (a
evidência aponta, falta confirmar), `estimado` (calculado por proxy declarado) e
`lacuna` (não existe no acervo — diga o que falta e o que mudaria).

**Achado sem valor e sem prazo é ruído.** Toda conclusão relevante recebe
quantificação em reais e uma data-limite (decadência, prescrição, virada de
exercício, prazo da Reforma).

## Fluxo de trabalho

Siga nesta ordem. Pular etapas costuma produzir os erros catalogados em
`references/armadilhas.md`.

1. **Inventário do acervo.** Antes de qualquer análise, liste o que existe por
   tipo, período e cobertura. Identifique a versão ativa de cada competência
   (retificadora vence a original). Um inventário honesto já é entregável.
2. **Identificação da empresa.** CNPJ, CNAE, UF, natureza jurídica, sócios,
   porte, regime por exercício. O CNAE define presunção, regimes diferenciados
   da Reforma e teses aplicáveis. Consulte o cadastro público quando útil.
3. **Estruturação.** Extraia os fatos fiscais dos arquivos com os layouts de
   `references/layouts-sped.md`. Nunca adivinhe posição de campo: os layouts ali
   foram validados contra arquivos reais.
4. **Cruzamentos.** Aplique os confrontos de `references/cruzamentos.md`:
   escriturado × declarado × pago × compensado, contábil × fiscal, pedido ×
   lastro, retificadoras.
5. **Reperformance.** Refaça a apuração — presunção, adicional, trava de
   prejuízo, Fator R, alíquotas. Regras em `references/codigos-e-regras.md`.
6. **Qualidade contábil.** Avalie se os demonstrativos representam a empresa
   real (custo segregado, estoques, provisões, partes relacionadas). Roteiro em
   `references/entregaveis.md`.
7. **Reforma Tributária.** Projete a transição CBS/IBS com os números do
   cliente. Parâmetros em `references/reforma-tributaria.md`.
8. **Entregáveis.** Monte o material conforme `references/entregaveis.md`.

Quando o usuário pedir só uma etapa ("faz o inventário", "revisa os créditos"),
entregue aquela etapa — mas mantenha os três compromissos acima.

## Quando ler cada referência

Carregue sob demanda, não tudo de uma vez:

| Arquivo | Leia quando |
|---|---|
| `references/layouts-sped.md` | for abrir qualquer arquivo SPED — posições de campo validadas |
| `references/codigos-e-regras.md` | precisar de código de receita, alíquota, prazo ou base legal |
| `references/cruzamentos.md` | for confrontar fontes ou interpretar divergência |
| `references/armadilhas.md` | **sempre**, antes de fechar qualquer número — é a lista de erros que já custaram retrabalho |
| `references/reforma-tributaria.md` | for simular CBS/IBS |
| `references/entregaveis.md` | for produzir relatório, memória de cálculo ou apresentação |

## Postura na análise

**Comece pelo que está certo.** Um diagnóstico que só ataca parece peça de
venda. Reconhecer com precisão o que está bem feito (e dizer por que é incomum)
compra a credibilidade necessária para o que vem depois. Se houver uma tese de
mercado que não se aplica ao caso, desaconselhe explicitamente — isso vale mais
que qualquer argumento de venda.

**Separe as naturezas do valor encontrado.** Ganho recorrente, ganho de caixa
(diferimento), crédito disponível e proteção de risco não se somam num número
único. Somar tudo infla a expectativa e destrói a confiança na primeira
conferência.

**Diga quando não sabe.** Se dois métodos de medição divergirem, apresente o
item como ponto a conciliar com a magnitude observada — nunca escolha o número
mais bonito. Essa honestidade é o que diferencia o trabalho.

**Duvide do resultado bom demais.** Carga tributária que vai a zero, crédito que
dobra, margem de 90% em atividade de baixa margem: quase sempre é erro de
método. Antes de reportar, procure a explicação alternativa — está em
`references/armadilhas.md`.

## Automação

Quando o volume justificar (dezenas de arquivos, séries longas, vários
clientes), escreva scripts Python em vez de ler manualmente. Padrões que se
repetem:

- Ler SPED como texto `latin-1`, quebrar por `\r\n`, separar campos por `|`
  descartando o primeiro elemento vazio — as posições dos layouts assumem isso.
- Converter valor brasileiro (`1.234,56`) antes de somar.
- Selecionar a versão ativa por competência antes de agregar.
- Emitir uma tabela por competência para conferência antes de consolidar.

Se o projeto já tiver uma plataforma de auditoria (parsers, cruzamentos,
geradores de relatório), use-a em vez de recriar — e corrija-a quando um caso
real expuser defeito, deixando o motivo registrado no commit.
