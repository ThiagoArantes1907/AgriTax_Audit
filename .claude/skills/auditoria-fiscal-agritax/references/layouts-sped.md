# Layouts SPED — posições validadas contra arquivos reais

**Convenção de indexação usada aqui.** Todas as posições assumem que a linha foi
quebrada assim:

```python
def campos(ln):
    return ln.split("|")[1:-1] if ln.endswith("|") else ln.split("|")[1:]
```

Ou seja: `f[0]` é o nome do registro (`0000`, `M100`...), `f[1]` é o primeiro
campo de conteúdo. Ler o arquivo como `latin-1` e quebrar por `\r\n`.

Cada layout abaixo foi conferido contra arquivos reais do ReceitaNetBX. Onde a
documentação oficial e o arquivo real divergiram, vale o arquivo real — e a
divergência está anotada.

## Índice

- [EFD-Contribuições](#efd-contribuições)
- [EFD-ICMS/IPI](#efd-icmsipi)
- [ECD](#ecd-escrituração-contábil-digital)
- [ECF](#ecf-escrituração-contábil-fiscal)
- [Identificação automática de tipo](#identificação-automática-de-tipo)

---

## EFD-Contribuições

### 0000 — abertura

```
|0000|COD_VER|TIPO_ESCRIT|IND_SIT_ESP|NUM_REC_ANTERIOR|DT_INI|DT_FIN|NOME|CNPJ|UF|COD_MUN|...
```

- `f[2]` TIPO_ESCRIT: `0` original, `1` retificadora
- `f[4]` NUM_REC_ANTERIOR: recibo da declaração retificada
- `f[5]` DT_INI, `f[6]` DT_FIN (formato `DDMMAAAA`)
- `f[7]` NOME, `f[8]` CNPJ, `f[9]` UF

> **Armadilha histórica**: uma versão anterior deste método lia o CNPJ como se
> fosse data e produzia zero fatos em arquivos reais. Sempre valide extraindo a
> competência de um arquivo conhecido antes de rodar em lote.

### Cadastros

```
|0150|COD_PART|NOME|COD_PAIS|CNPJ|CPF|IE|COD_MUN|SUFRAMA|...
|0200|COD_ITEM|DESCR_ITEM|COD_BARRA|COD_ANT_ITEM|UNID_INV|TIPO_ITEM|COD_NCM|...
```

- `0150`: `f[4]` CNPJ e `f[5]` CPF — a presença de CPF sem CNPJ identifica
  **pessoa física** (produtor rural, autônomo). Isso importa para crédito.
- `0200`: `f[7]` COD_NCM

### Documentos e itens

```
|C100|IND_OPER|IND_EMIT|COD_PART|COD_MOD|COD_SIT|SER|NUM_DOC|CHV_NFE|DT_DOC|DT_E_S|VL_DOC|...
|C170|NUM_ITEM|COD_ITEM|DESCR_COMPL|QTD|UNID|VL_ITEM|VL_DESC|IND_MOV|CST_ICMS|CFOP|COD_NAT|...
```

- `C100`: `f[1]` IND_OPER (`0` entrada, `1` saída), `f[3]` COD_PART, `f[11]` VL_DOC
- `C170`: `f[6]` VL_ITEM, `f[9]` CST_ICMS, `f[10]` CFOP, `f[24]` CST_PIS
- `A100`/`A170`: documentos de serviço (NFS-e). Em `A100`, `f[12]` VL_DOC.

### Receita consolidada (regime cumulativo)

```
|F550|VL_REC_COMP|CST_PIS|VL_DESC_PIS|VL_BC_PIS|ALIQ_PIS|VL_PIS|CST_COFINS|...
```

- `f[1]` VL_REC_COMP, `f[2]` CST_PIS

Empresas do Presumido normalmente concentram a receita aqui e **não têm C100 de
saída** — não conclua "sem faturamento" por ausência de C100.

### Retenções sofridas

```
|F600|IND_NAT_RET|DT_RET|VL_BC_RET|VL_RET|COD_REC|IND_NAT_REC|CNPJ|VL_RET_PIS|VL_RET_COFINS|IND_DEC|
```

- `f[3]` VL_BC_RET, `f[4]` VL_RET, `f[7]` CNPJ da fonte pagadora

Uso não óbvio e muito valioso: **a soma de VL_BC_RET aproxima a receita faturada
contra pessoa jurídica**, porque só PJ retém. Serve como proxy da fatia B2B do
faturamento quando não há detalhe por cliente.

### Bloco M — apuração e créditos

```
|M100|COD_CRED|IND_CRED_ORI|VL_BC_CRED|ALIQ|QUANT_BC|ALIQ_QUANT|VL_CRED|VL_AJUS_ACRES|VL_AJUS_REDUC|VL_CRED_DIF|VL_CRED_DISP|IND_DESC_CRED|VL_CRED_DESC|SLD_CRED|
|M105|NAT_BC_CRED|CST_PIS|VL_BC_PIS_TOT|VL_BC_PIS_CUM|VL_BC_PIS_NC|VL_BC_PIS|...
|M200|VL_TOT_CONT_NC_PER|VL_CRED_DESC|VL_CRED_DESC_ANT|VL_TOT_CONT_NC_DEV|VL_RET_NC|VL_OUT_DED_NC|VL_CONT_NC_REC|VL_TOT_CONT_CUM_PER|VL_RET_CUM|VL_OUT_DED_CUM|VL_CONT_CUM_REC|VL_TOT_CONT_REC|
|M205|NUM_CAMPO|COD_REC|VL_DEBITO|
```

`M500`/`M505`/`M600`/`M605` são os equivalentes de COFINS, mesma estrutura.

Campos que importam:
- `M100/M500`: `f[1]` COD_CRED, `f[3]` VL_BC_CRED, `f[7]` VL_CRED,
  `f[11]` VL_CRED_DISP, `f[13]` VL_CRED_DESC, `f[14]` SLD_CRED
- `M105/M505`: `f[1]` NAT_BC_CRED, `f[6]` VL_BC
- `M200/M600`: `f[4]` total devido não-cumulativo, `f[8]` total cumulativo do
  período, `f[9]` retenções deduzidas, `f[11]` a recolher cumulativo,
  `f[12]` total a recolher
- `M205/M605`: `f[2]` COD_REC, `f[3]` VL_DEBITO — **nesta ordem**, confirmado em
  arquivo real (`|M205|08|691201|2,91|`). A ordem inversa aparece em
  documentação secundária e está errada.

**COD_CRED** (`M100`/`M500`): o primeiro dígito indica a vinculação da receita —
`1xx` receita tributada no mercado interno, `2xx` receita **não tributada** no
mercado interno, `3xx` exportação. Os dois últimos indicam o tipo (`01` alíquota
básica, `02` diferenciadas, `04` estoque de abertura, `06` presumido da
agroindústria...). Créditos `2xx` e `3xx` são os ressarcíveis em dinheiro.

**NAT_BC_CRED** (`M105`/`M505`) — natureza da base que gerou o crédito:

| Cód | Natureza |
|---|---|
| 01 | Aquisição de bens para revenda |
| 02 | Aquisição de bens utilizados como insumo |
| 03 | Aquisição de serviços utilizados como insumo |
| 04 | Energia elétrica e térmica |
| 05 | Aluguéis de prédios |
| 06 | Aluguéis de máquinas e equipamentos |
| 07 | Armazenagem de mercadoria e frete na operação de venda |
| 08 | Contraprestações de arrendamento mercantil |
| 09 | Depreciação de bens do ativo imobilizado |
| 10 | Depreciação de edificações e benfeitorias |
| 11 | Amortização de intangível |
| 12 | Devolução de vendas não-cumulativas |
| 13 | Outras operações com direito a crédito |
| 14 | Transporte de cargas — subcontratação |
| 18 | Estoque de abertura |

### Bloco 1100/1500 — controle de créditos de períodos anteriores

```
|1100|PER_APU_CRED|ORIG_CRED|CNPJ_SUC|COD_CRED|VL_CRED_APU|VL_CRED_EXT_APU|VL_TOT_CRED_APU|VL_CRED_DESC_PA_ANT|VL_CRED_PER_PA_ANT|VL_CRED_DCOMP_PA_ANT|SD_CRED_DISP_EFD|VL_CRED_DESC_EFD|VL_CRED_PER_EFD|VL_CRED_DCOMP_EFD|VL_CRED_TRANS|VL_CRED_OUT|SLD_CRED_FIM|
```

`1500` é o equivalente de COFINS.

- `f[1]` período de origem do crédito, `f[4]` COD_CRED, `f[5]` apurado
- `f[11]` saldo disponível, `f[12]` descontado, `f[13]` ressarcimento,
  `f[14]` DCOMP, `f[17]` **saldo final**

**Este é o registro que diz quanto crédito realmente existe.** Ver a armadilha
"fluxo × estoque" em `armadilhas.md` — é o erro mais caro do método.

---

## EFD-ICMS/IPI

### 0000 — abertura (layout diferente do da EFD-Contribuições)

```
|0000|COD_VER|COD_FIN|DT_INI|DT_FIN|NOME|CNPJ|CPF|UF|IE|COD_MUN|IM|SUFRAMA|IND_PERFIL|IND_ATIV|
```

- `f[2]` COD_FIN: `0` original, `1` retificadora
- `f[3]` DT_INI, `f[4]` DT_FIN
- `f[5]` NOME, `f[6]` CNPJ, `f[8]` UF

A **data em `f[3]`/`f[4]`** (e não em `f[5]`/`f[6]`) é o discriminador confiável
para separar EFD-ICMS de EFD-Contribuições em uma pasta misturada.

### Apuração

```
|E110|VL_TOT_DEBITOS|VL_AJ_DEBITOS|VL_TOT_AJ_DEBITOS|VL_ESTORNOS_CRED|VL_TOT_CREDITOS|VL_AJ_CREDITOS|VL_TOT_AJ_CREDITOS|VL_ESTORNOS_DEB|VL_SLD_CREDOR_ANT|VL_SLD_APURADO|VL_TOT_DED|VL_ICMS_RECOLHER|VL_SLD_CREDOR_TRANSPORTAR|DEB_ESP|
```

- `f[1]` débitos, `f[5]` créditos, `f[6]` ajustes credores,
  `f[9]` saldo credor anterior, `f[12]` **ICMS a recolher**,
  `f[13]` saldo credor a transportar

Gap grande entre débitos e ICMS a recolher = benefício estadual (crédito
outorgado, PRODEIC/MT, FOMENTAR-PRODUZIR/GO). Confirme nos ajustes `E111`.

### Consolidação e itens

```
|C190|CST_ICMS|CFOP|ALIQ_ICMS|VL_OPR|VL_BC_ICMS|VL_ICMS|...
|E111|COD_AJ_APUR|DESCR_COMPL_AJ|VL_AJ_APUR|
```

- `C190`: `f[1]` CST, `f[2]` CFOP, `f[4]` VL_OPR
- `E111`: `f[1]` código estadual do ajuste, `f[3]` valor

A EFD-ICMS registra o documento em `C100` e **consolida em `C190`** — muitos
documentos não têm `C170`. Comparar totais de `C100` (documento) com `C190`
(consolidação) produz números incompatíveis; ver `armadilhas.md`.

---

## ECD (Escrituração Contábil Digital)

### 0000 e plano de contas

```
|0000|LECD|DT_INI|DT_FIN|NOME|CNPJ|UF|IE|COD_MUN|IM|IND_SIT_ESP|...
|I050|DT_ALT|COD_NAT|IND_CTA|NIVEL|COD_CTA|COD_CTA_SUP|CTA|
```

- `0000`: `f[2]` DT_INI, `f[3]` DT_FIN — o exercício sai de `f[3][4:8]`
- `I050`: `f[2]` COD_NAT, `f[4]` NIVEL, `f[5]` COD_CTA, `f[6]` COD_CTA_SUP,
  `f[7]` nome da conta

COD_NAT: `01` ativo, `02` passivo, `03` patrimônio líquido, `04` resultado,
`05` contas de compensação.

### Saldos e resultado

```
|I150|DT_INI|DT_FIN|
|I155|COD_CTA|COD_CCUS|VL_SLD_INI|IND_DC_INI|VL_DEB|VL_CRED|VL_SLD_FIN|IND_DC_FIN|
|I350|DATA|
|I355|COD_CTA|COD_CCUS|VL_SLD_FIN|IND_DC|
```

- `I155`: `f[1]` conta, `f[2]` saldo inicial, `f[3]` D/C inicial, `f[4]` débitos,
  `f[5]` créditos, `f[7]` **saldo final**, `f[8]` D/C final
- `I355`: `f[1]` conta, `f[3]` valor, `f[4]` D/C

**Balanço**: use o **último bloco `I150`** (último período do exercício) e
percorra os `I155`. As contas analíticas são as únicas com saldo — para montar
grupos, suba a hierarquia por `COD_CTA_SUP` até o nível desejado.

**DRE**: use o **último bloco `I350`** e seus `I355` (saldos antes do
encerramento). Agregue pelo pai direto da conta analítica para obter os grupos
naturais do plano. Se houver vários blocos `I350`, restringir ao último evita
multiplicar o resultado.

Sinal: no ativo, débito é positivo; no passivo e PL, crédito é positivo. Em
`I355`, conta credora é receita, devedora é custo/despesa.

`J150` (DRE estruturada) existe, mas o lado das colunas varia por layout —
valide escolhendo o lado em que "anual = soma dos trimestres" fecha, ou prefira
`I355`.

---

## ECF (Escrituração Contábil Fiscal)

### 0000 e regime

```
|0000|LECF|COD_VER|CNPJ|NOME|COD_SIT_ESP|...|DT_INI|DT_FIN|IND_RETIF|HASH_ORIGINAL|...
|0010|FORMA_TRIB|...
```

- `f[3]` CNPJ, `f[4]` NOME, `f[9]` DT_INI, `f[10]` DT_FIN
- `f[11]` IND_RETIF: `N` original, `S` **retificadora**; `f[12]` traz o hash da
  declaração retificada
- `0010`: `f[1]` FORMA_TRIB — `1` Real, `2` Real/Arbitrado, `3` Presumido/Real,
  `4` Presumido/Arbitrado, `5` **Presumido**, `6` Arbitrado, `7` imune, `8` isenta

### Registros de apuração

Todos seguem `|REG|NUM_LINHA|DESCRIÇÃO|VALOR|`. O casamento por número de linha
é frágil entre versões de layout — **prefira casar pela descrição normalizada**.

**Lucro Presumido:**

| Registro | Conteúdo | Linhas úteis |
|---|---|---|
| `P200` | Base do IRPJ | 8 receita a 32%, 10 resultado da presunção, 11 aplicações financeiras, 20 demais receitas, 26 base |
| `P300` | Cálculo do IRPJ | 1 base, 3 alíquota de 15%, 4 adicional, 10 (−) IRRF, 15 IRPJ a pagar |
| `P400` | Base da CSLL | 4 receita a 32%, 6 resultado, 21 base |
| `P500` | Cálculo da CSLL | 1 base, 2 CSLL apurada, 11 (−) CSLL retida, 13 CSLL a pagar |

**Lucro Real — ajuste anual:**

| Registro | Conteúdo | Linhas úteis |
|---|---|---|
| `N630` | IRPJ do ajuste | 1 base, 3 alíquota de 15%, 4 adicional, 20 (−) IRRF, 24 (−) estimativa paga, 26 IRPJ a pagar |
| `N670` | CSLL do ajuste | 1 base, 2 CSLL, 19 (−) estimativa paga, 21 CSLL a pagar |

**Lucro Real — estimativa mensal (balancete de suspensão/redução):**

| Registro | Conteúdo | Linhas úteis |
|---|---|---|
| `N620` | IRPJ mensal | 1 base acumulada, 3 15%, 4 adicional, 20 (−) devido em meses anteriores, 26 devido no mês |
| `N660` | CSLL mensal | 2 base, 3 CSLL apurada, 12 (−) devida em meses anteriores, 18 devida no mês |

Valor **negativo** em `N620` linha 26 ou `N660` linha 18 significa que o
resultado acumulado caiu — não há estimativa a pagar naquele mês. Não confunda
com saldo negativo do ajuste anual, que está em `N630`/`N670`. Ver `armadilhas.md`.

`*030` delimita o período (`T01`–`T04` trimestres, `A00` ano, `A01`–`A12` meses).
No Real trimestral, o resultado de `L300` é por trimestre — o anual é a soma.

---

## Identificação automática de tipo

Ordem de teste que funciona em pasta misturada:

1. Segunda posição da primeira linha é `LECD` → **ECD**
2. Segunda posição é `LECF` → **ECF**
3. Primeira linha começa com `0000` e `f[3]` tem formato de data → **EFD-ICMS/IPI**
4. Primeira linha começa com `0000` e `f[5]`/`f[6]` têm formato de data →
   **EFD-Contribuições**

Para PDFs do e-CAC, combine palavras-chave do conteúdo com o nome do arquivo.
Cuidado: capas de DCTF aparecem com as letras espaçadas (`D C T F`) — remova
espaços antes de comparar, ou 24 documentos ficam sem identificação.
