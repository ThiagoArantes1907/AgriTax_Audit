# Armadilhas — os erros que já custaram retrabalho

Leia antes de fechar qualquer número. Cada item aqui aconteceu de verdade e
produziu um valor errado num documento que ia para o cliente.

Regra geral que resume quase tudo: **quando o resultado ficar bom demais,
procure o erro de método antes de comemorar.**

## 1. Fluxo × estoque no crédito de PIS/COFINS

**O erro.** Somar o `SLD_CRED` dos registros `M100`/`M500` ao longo de um
trimestre (ou de anos) e chamar o total de "crédito acumulado disponível".

**Por que é errado.** `SLD_CRED` é o resíduo do crédito apurado **naquele mês** e
não descontado nele. Somar meses mede *geração de crédito sem consumo imediato*
— um número de fluxo. Boa parte desse resíduo é descontada nos meses seguintes.

**O certo.** O estoque real está no **Bloco 1100 (PIS) / 1500 (COFINS)**, campo
`SLD_CRED_FIM`, na última competência escriturada. Ele mostra saldo por período
de origem e o que já virou desconto, ressarcimento ou DCOMP.

**Ordem de grandeza do erro.** Num caso real: soma dos resíduos de 2023 a 2026 =
R$ 1,03 milhão; estoque efetivo no 1100/1500 = R$ 222 mil. O relatório já tinha
saído com o número inflado.

**Como apresentar os dois.** Ambos são úteis: o estoque é o que se pode pedir
hoje; o fluxo revela a política de créditos da empresa (gera muito e consome por
desconto, ou acumula sem destino?). Apresente separados e rotulados.

## 2. Competência trimestral × mensal

**O erro.** Cruzar IRPJ/CSLL do Lucro Presumido (ou Real trimestral) casando
competência literal.

**Por que é errado.** A DCTF/MIT confessa por trimestre (`2024.1T` ou o mês de
abertura) e o DARF carrega o mês de encerramento do período de apuração
(`2024.03`). O mesmo trimestre vira duas ou três chaves distintas.

**O certo.** Antes de cruzar, normalize mês → trimestre para os códigos
trimestrais: **2089, 2372, 0220, 6012**.

**Ordem de grandeza.** Num caso real, R$ 1,07 milhão de IRPJ/CSLL apareceu como
"não quitado" sendo que estava tudo pago — inclusive as quotas.

## 3. Ano-base parcial tratado como ano cheio

**O erro.** Escolher o exercício mais recente como base de cálculo anual.

**Por que é errado.** O ano corrente costuma ter poucos meses escriturados. Uma
projeção anual construída sobre 6 meses distorce tudo.

**O certo.** Escolher o ano com **maior cobertura**, desempatando pelo mais
recente. Se precisar anualizar, escale **apenas o que nasce de escrituração
mensal** (EFD, DARF) — ECD e ECF já são anuais por natureza e não devem ser
multiplicadas. Melhor ainda: quando existir exercício fechado na ECD, prefira o
valor dela à extrapolação.

Se blocos diferentes tiverem coberturas diferentes (EFD-ICMS começando em março,
EFD-Contribuições em janeiro), anualize cada um pela própria cobertura.

## 4. Conta-raiz contaminando a classificação contábil

**O erro.** Classificar contas da ECD por palavra-chave testando **toda** a
cadeia de ancestrais.

**Por que é errado.** Planos de contas têm sintéticos de topo como
"CUSTOS, DESPESAS E IMPOSTOS". A palavra "impostos" no topo exclui todos os
descendentes — inclusive matéria-prima.

**O certo.** Testar apenas o nome da própria conta **e o do pai direto**. Isso
basta para classificar e não sofre contaminação.

**Ordem de grandeza.** Zerou uma base creditável de R$ 2,1 milhões.

## 5. Deduções de venda contadas como aquisição

**O erro.** Incluir as contas `(-) PIS`, `(-) COFINS`, `(-) ISS`, `(-) ICMS
sobre vendas` na base de crédito.

**Por que é errado.** São deduções da receita, não compras de bens ou serviços.

**O certo.** Excluir o grupo de deduções da receita. Excluir também `estoque
inicial/final` (mecânica do CMV, não aquisição do período), folha e encargos,
tributos, despesas financeiras e depreciação (o crédito ocorre na aquisição do
bem, não na despesa).

## 6. Versão retificadora não substituindo a original

**O erro.** Agregar todos os arquivos de uma competência.

**Por que é errado.** Original e retificadora convivem na pasta. Somar as duas
dobra o valor.

**O certo.** Determinar a **versão ativa por competência**: retificadora vence
original; entre versões do mesmo tipo, vence a de nome maior (os nomes do BX
carregam o timestamp de transmissão). Aplicar isso a EFD, ECD e ECF.

Depois de reprocessar com uma retificadora nova, **confira explicitamente** se o
cruzamento não dobrou o valor — é rápido e evita erro grave.

## 7. PER/DCOMP inflado

**O erro.** Somar todos os documentos PER/DCOMP da pasta.

**Por que é errado.** Três fontes de duplicidade: downloads repetidos
(`x.pdf` e `x_(1).pdf`), DCOMPs vinculadas a um PER inicial (reutilizam o mesmo
crédito) e cadeias de retificação (a retificadora substitui a retificada).

**O certo.** Deduplicar pelo número do PER/DCOMP, pular documentos com número de
pedido vinculado e excluir os alvos de retificação.

**Ordem de grandeza.** R$ 45,8 milhões caíram para R$ 20,4 milhões.

## 8. Lacuna de acervo lida como irregularidade

**O erro.** Reportar "declarado sem pagamento" ou "pago sem declaração" quando a
fonte que faltava simplesmente não foi baixada.

**O certo.** Antes de qualificar, verifique a cobertura da fonte. Se a série
está incompleta, o achado é **lacuna de acervo** e a ação é completar a coleta.

**Ordem de grandeza.** Num caso real, 75 apontamentos de "sem declaração" viraram
2 depois que as DCTFWeb faltantes entraram — e os "quitados" subiram de 15 para
88. Se aquilo tivesse ido para o cliente como irregularidade, a reunião teria
sido constrangedora.

## 9. Estimativa mensal negativa confundida com saldo negativo

**O erro.** Ler valor negativo em `N620` linha 26 (ou `N660` linha 18) como
saldo negativo do ajuste anual.

**Por que é errado.** Esses registros são o balancete de suspensão/redução: a
linha subtrai o "devido em meses anteriores" do acumulado. Negativo significa que
o resultado acumulado caiu — não há estimativa a pagar naquele mês.

**O certo.** O saldo negativo do ajuste está em `N630` linha 26 (IRPJ) e `N670`
linha 21 (CSLL). O negativo mensal é, no máximo, **indício** de que as
estimativas superaram o ajuste — confirme com os DARFs efetivamente pagos.

## 10. ECF que não deduz as estimativas pagas

**O que procurar.** `N630` linha 24 e `N670` linha 19 zeradas, com DARF de
estimativa (códigos 2362/2484) efetivamente recolhido no ano.

**O que significa.** O dinheiro saiu do caixa mas o crédito não foi constituído
na declaração. Como a Receita valida o PER/DCOMP contra a ECF, pedir sem
retificar tende a indeferimento ou malha.

**A ação.** Retificar a ECF **antes** de protocolar. Verifique todos os
exercícios: esse defeito costuma ser rotina de fechamento, não deslize isolado.

## 11. Retenções: confronto com o demonstrativo de fontes pagadoras

O demonstrativo de fontes pagadoras (Sistema DIRF, no e-CAC) mostra o que
terceiros declararam ter retido. Compare com o que a empresa deduziu:

- **Deduzido > informado**: o direito permanece (vale a retenção efetiva, provada
  por nota e comprovante), mas é exatamente esse cruzamento que a malha faz.
  Ação: montar dossiê probatório e cobrar das fontes a entrega/retificação da
  EFD-Reinf.
- **Deduzido < informado**: há crédito não aproveitado. Ação: retificar e pedir.
- **Zero retido em período de Simples**: correto — optante não sofre CSRF
  (IN RFB 459/2004, art. 3º, II). Não procure crédito onde não pode existir.

## 12. Níveis de agregação incompatíveis na EFD-ICMS

**O erro.** Comparar totais de `C100` (valor do documento) com `C190`
(consolidação por CST/CFOP/alíquota) ou com `C170` (itens).

**Por que é errado.** Nem todo documento tem `C170`; `VL_DOC` inclui parcelas que
não estão no valor da operação. Os três níveis dão números diferentes por
construção.

**O certo.** Escolher um nível e mantê-lo dos dois lados da comparação. Se os
métodos divergirem, **apresente como ponto a conciliar**, com a magnitude
observada — nunca escolha o número mais conveniente.

## 13. Crédito de aquisição de pessoa física

Em qualquer projeção que dependa de crédito sobre compras (especialmente agro),
separe as aquisições de **pessoa física** — identificáveis no `0150` pelo CPF sem
CNPJ. No IBS/CBS elas não geram crédito cheio; há previsão de crédito presumido,
mas o percentual depende de regulamentação.

Sem essa separação, a projeção de uma trading de grãos deu carga tributária
**zero** — resultado bonito e irresponsável. Adote a hipótese conservadora
(crédito zero sobre PF) e quantifique quanto vale cada ponto de crédito
presumido que vier a ser concedido.

## 14. Base atípica na projeção

Se as aquisições do exercício-base superarem ~80% da receita, verifique se o ano
é representativo: empresa em implantação, ramp-up ou ano atípico produz projeção
artificialmente favorável no novo modelo (que credita quase tudo). Sinalize e
proponha refazer com exercício em regime normal.

Atenção: em distribuidoras e tradings, compras próximas da receita são normais —
o gatilho é indício, não veredito.

## 15. Percentuais compostos e margens

Ao afirmar margem, defina a base e mantenha-a: margem bruta sobre receita
**líquida** é diferente de sobre receita bruta. E "margem aparente" (o que a DRE
mostra) só faz sentido comparada com "margem real" (com todos os custos de
execução reclassificados) se as duas usarem a mesma base.

Recalcule antes de publicar: números tirados de rascunho anterior envelhecem mal.
