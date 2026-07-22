"""Parser de PER/DCOMP (PDFs do e-CAC) + planilha de status.

Extraído do AgriTax Audit v5 consolidado (agritax_audit_consolidado.py),
sem alterações de lógica — apenas modularização (M1 da arquitetura).
"""
import os
import re

import openpyxl
import pdfplumber

from ._util import format_competencia_teste, parse_brl

_PERDCOMP_NUM_RE = re.compile(r'\d{5}\.\d{5}\.\d{6}\.\d\.\d\.\d{2}-\d{4}')

def parse_status_excel(path: str) -> dict:
    """
    Lê planilha de status exportada do eCAC e retorna:
      { numero_perdcomp: { situacao, cnpj, tipo_documento, tipo_credito, data_transmissao } }

    Detecta automaticamente as colunas pelo nome do cabeçalho.
    Situações consideradas "cancelado":  contêm "cancelad".
    Situações consideradas "retificado": contêm "retificad".
    """
    wb   = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws   = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        return {}

    # Localiza linha de cabeçalho (primeira com ≥ 3 células preenchidas)
    hdr_idx = 0
    for i, row in enumerate(rows[:15]):
        if sum(1 for c in row if c is not None) >= 3:
            hdr_idx = i
            break

    headers = [str(c).strip().lower() if c else "" for c in rows[hdr_idx]]

    def _find(*kws):
        """Retorna índice da coluna cujo header contenha qualquer uma das palavras-chave."""
        for kw in kws:
            for i, h in enumerate(headers):
                if kw in h:
                    return i
        return None

    col_num  = _find("número do documento", "nº perdcomp", "num perdcomp",
                     "numero do documento", "perdcomp", "número", "numero", "nº", "n°")
    col_sit  = _find("situação", "situacao", "status")
    col_cnpj = _find("cnpj")
    col_tipo = _find("tipo de documento", "tipo doc")
    col_cred = _find("tipo de crédito", "tipo de credito", "crédito", "credito")
    col_data = _find("data de transmissão", "data transmissao", "transmissão", "transmissao", "data")

    # Fallback: detecta a coluna do Nº PERDCOMP pelo formato dos valores
    if col_num is None:
        for row in rows[hdr_idx + 1 : hdr_idx + 6]:
            for j, cell in enumerate(row):
                if cell and _PERDCOMP_NUM_RE.match(str(cell).strip()):
                    col_num = j
                    break
            if col_num is not None:
                break

    if col_num is None:
        return {}

    result = {}
    for row in rows[hdr_idx + 1:]:
        if not row or all(c is None for c in row):
            continue
        raw_num = str(row[col_num]).strip() if row[col_num] is not None else ""
        # Remove espaços internos que às vezes aparecem no Excel
        num = re.sub(r'\s+', '', raw_num)
        if not _PERDCOMP_NUM_RE.match(num):
            continue

        def _val_at(col):
            return str(row[col]).strip() if col is not None and col < len(row) and row[col] is not None else ""

        result[num] = {
            "situacao":       _val_at(col_sit),
            "cnpj":           _val_at(col_cnpj),
            "tipo_documento": _val_at(col_tipo),
            "tipo_credito":   _val_at(col_cred),
            "data_transmissao": _val_at(col_data),
        }
    return result


def _is_cancelled(num: str, status_map: dict) -> bool:
    """True se o PERDCOMP estiver cancelado na planilha de status."""
    if not num or not status_map:
        return False
    return "cancelad" in status_map.get(num, {}).get("situacao", "").lower()


def _is_retified(num: str, status_map: dict) -> bool:
    """True se o PERDCOMP estiver retificado na planilha de status do eCAC.

    Cobre os casos em que a planilha indica 'Retificado' mas o PDF do
    retificador ainda não foi importado — sem esse helper, esses casos
    seriam tratados como vigentes no Controle de Créditos.
    """
    if not num or not status_map:
        return False
    return "retificad" in status_map.get(num, {}).get("situacao", "").lower()


def _get(label, text):
    pat = re.escape(label) + r'[ \t]+([^\n\r]+)'
    m = re.search(pat, text, re.IGNORECASE)
    return m.group(1).strip() if m else ""

def _get_first(text, *labels):
    for label in labels:
        v = _get(label, text)
        if v: return v
    return ""

def _val(label, block):
    """Extrai valor numérico (começa com dígito) após rótulo."""
    pat = re.escape(label) + r'[ \t]+([\d][^\n\r]*)'
    m = re.search(pat, block, re.IGNORECASE)
    return m.group(1).strip() if m else ""

def parse_pdf(path):
    with pdfplumber.open(path) as pdf:
        pages = [p.extract_text(x_tolerance=3, y_tolerance=3) or "" for p in pdf.pages]
    txt = "\n".join(pages)

    # CNPJ e Nº PERDCOMP ficam na mesma linha do cabeçalho de cada página
    hm = re.search(
        r'CNPJ\s+(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})\s+'
        r'(\d{5}\.\d{5}\.\d{6}\.\d\.\d\.\d{2}-\d{4})',
        txt)
    cnpj            = hm.group(1) if hm else ""
    numero_perdcomp = hm.group(2) if hm else ""

    nome     = _get("Nome Empresarial", txt)
    data_tx  = _get("Data de Transmissão", txt)
    tipo_doc = _get("Tipo de Documento", txt)
    tipo_cred= _get("Tipo de Crédito", txt)

    # Pedido de origem (Nº do PER/DCOMP Inicial)
    num_vinc = _get("Nº do PER/DCOMP Inicial", txt).strip()

    # Retificação: este documento é uma retificação de outro?
    retificador = _get("PER/DCOMP Retificador", txt)            # "Sim" ou "Não"
    num_retificado = ""
    if retificador.lower() == "sim":
        # Número do PER/DCOMP que está sendo retificado
        num_retificado = _get_first(txt,
            "Nº do PER/DCOMP que está sendo Retificado",
            "Nº do PER/DCOMP sendo Retificado",
            "PER/DCOMP a ser Retificado",
            "Número do PER/DCOMP Retificado",
            "PER/DCOMP Retificado",
            "Nº do PER/DCOMP Inicial")    # fallback: em alguns casos coincide

    # Valores do crédito - prioridade: mais específico primeiro
    # ATENÇÃO: Ressarcimento e Restituição têm nomes de campos DIFERENTES no eCAC!
    valor_cred = _get_first(txt,
        "Valor do Pedido de Ressarcimento",            # PER Ressarcimento PIS/COFINS (LIQUIDO)
        "Valor do Pedido de Restituição",              # PER Restituição (Retenção)
        "Crédito Passível de Ressarcimento",           # PER Ressarcimento fallback
        "Crédito Passível de Restituição Apurado no",  # DCOMP Retenção
        "Valor Original do Crédito Inicial",            # PIS/COFINS DCOMP
        "Crédito Passível de Restituição",              # PER Restituição fallback
        "Valor Original do Crédito",
        "Valor a Compensar",
        "Valor Total do Crédito",
        "Valor Bruto do Crédito",
        "Valor do Ressarcimento",
        "Valor do Crédito",                             # BRUTO - usar só como último recurso
        "Crédito Original na Data da Entrega",          # "da" - Retenção
        "Crédito Original na Data de Entrega")          # "de" - PIS/COFINS

    valor_util = _get_first(txt,
        "Total do Crédito Original Utilizado neste Documento",
        "Valor Utilizado nesta DCOMP",
        "Valor Compensado",
        "Total dos Débitos deste Documento")

    # "Total dos Débitos deste Documento" — capturado SEPARADAMENTE para que
    # o controle de créditos use o valor real dos débitos compensados, não o
    # crédito original consumido (que difere quando há atualização SELIC /
    # correções monetárias).
    valor_total_debitos = _get_first(txt,
        "Total dos Débitos deste Documento")

    # Período do crédito: Trimestre+Ano, Mês+Ano, Competência (Retenção) ou Período de Apuração
    m_trim = re.search(r'\nTrimestre[ \t]+([^\n]+)', txt)
    m_ano  = re.search(r'\nAno[ \t]+(\d{4})', txt)
    m_mes  = re.search(r'\nMês[ \t]+([^\n]+)', txt)
    m_comp = re.search(r'\nCompetência[ \t]+([^\n]+)', txt)   # Retenção Lei 9.711/98
    trimestre  = m_trim.group(1).strip() if m_trim else ""
    mes        = m_mes.group(1).strip()  if m_mes  else ""
    ano        = m_ano.group(1)          if m_ano  else ""
    competencia= m_comp.group(1).strip() if m_comp else ""
    if trimestre and ano:
        periodo_cred = f"{trimestre}/{ano}"
    elif mes and ano:
        periodo_cred = f"{mes}/{ano}"
    elif competencia:
        periodo_cred = competencia                             # ex: "Abril de 2025"
    elif ano:
        periodo_cred = ano
    else:
        m_pa = re.search(
            r'(?:PIS|COFINS|CSLL|IRPJ|RETEN|CONTRIB)[^\n]*\n'
            r'(?:[^\n]*\n){0,15}?Período de Apuração[ \t]+([^\n]+)',
            txt, re.IGNORECASE)
        periodo_cred = m_pa.group(1).strip() if m_pa else ""

    # Código/Natureza do crédito (ressarcimento PIS/COFINS: 101, 201, 310 etc.)
    # Padrões: "101 - Aquisição de bens para revenda", "Natureza do Crédito: 101"
    codigo_credito = _get_first(txt,
        "Natureza da Base de Cálculo",
        "Natureza do Crédito",
        "Código do Crédito")
    if not codigo_credito:
        # Tenta extrair padrão "NNN - Descrição" na seção de crédito (antes dos débitos)
        trecho = txt[:txt.find("\n001. Débito")] if "\n001. Débito" in txt else txt
        m_cod = re.search(r'\n(\d{3})\s*[-]\s*([^\n]{5,60})', trecho)
        if m_cod:
            codigo_credito = f"{m_cod.group(1)} - {m_cod.group(2).strip()}"

    credito = {
        "tipo_credito":    tipo_cred,
        "periodo_apuracao": periodo_cred,
        "valor_original":  valor_cred,
        "valor_utilizado": valor_util,
        "valor_total_debitos": valor_total_debitos,
        "codigo_credito":  codigo_credito,
    }

    # Débitos: cada bloco inicia com "NNN. Débito TIPO"
    debitos = []
    debt_iters = list(re.finditer(r'\n(\d{3})\.\s+Débito\s+([^\n]+)', txt))
    for i, dm in enumerate(debt_iters):
        s   = dm.start()
        e   = debt_iters[i+1].start() if i+1 < len(debt_iters) else len(txt)
        blk = txt[s:e]

        grupo  = _get("Grupo de Tributo", blk)
        codigo = _get("Código da Receita/Denominação", blk)
        # Período de Apuração - precisa do "de" para não pegar "Período Apuração DCTFWeb"
        pm_pa  = re.search(r'Período de Apuração[ \t]+([^\n]+)', blk, re.IGNORECASE)
        periodo = pm_pa.group(1).strip() if pm_pa else ""

        principal = _val("Principal", blk)
        multa     = _val("Multa",     blk)
        juros     = _val("Juros",     blk)
        total     = _val("Total",     blk)   # lowercase "Total", diferente de "TOTAL"

        tipo_deb = grupo if grupo else dm.group(2).strip()
        if codigo and codigo[:40] not in tipo_deb:
            tipo_deb = f"{tipo_deb} | {codigo[:50]}"

        # Extrai apenas o código NNNN-NN (ou NNNN) da string "codigo"
        # Ex.: "0481-01 - IRRF sobre Rendimentos" → "0481-01"
        cod_receita_deb = ""
        if codigo:
            m_cod = re.match(r'(\d{3,4}(?:-\d{1,2})?)', codigo.strip())
            if m_cod:
                cod_receita_deb = m_cod.group(1)

        debitos.append({
            "tipo_debito":       tipo_deb,
            "codigo_receita_debito": cod_receita_deb,
            "periodo_apuracao":  periodo,
            "valor_principal":   principal,
            "valor_multa":       multa,
            "valor_juros":       juros,
            "valor_total":       total,
            "valor_compensado":  total,
        })

    # Cria entrada de crédito se há pedido vinculado OU se há valor de crédito/utilizado
    tem_credito = bool(num_vinc or valor_cred or valor_util)
    return [{
        "cnpj":                    cnpj,
        "razao_social":            nome,
        "tipo_pedido":             tipo_doc,
        "numero_perdcomp":         numero_perdcomp,
        "numero_pedido_vinculado": num_vinc,
        "data_transmissao":        data_tx,
        "retificador":             retificador,           # "Sim" / "Não"
        "numero_perdcomp_retificado": num_retificado,     # Nº do doc que este retifica
        "creditos":                [credito] if tem_credito else [],
        "debitos":                 debitos,
    }]

def flatten_rows(perdcomps, filename):
    rows = []
    for p in perdcomps:
        hdr = {k: p.get(k,"") for k in [
            "cnpj","razao_social","tipo_pedido","numero_perdcomp",
            "numero_pedido_vinculado","data_transmissao",
            "retificador","numero_perdcomp_retificado"]}
        hdr["_source"] = filename
        for c in p.get("creditos", []):
            rows.append({**hdr,
                "tipo_registro":    "Crédito",
                "tipo":             c.get("tipo_credito",""),
                "periodo_apuracao": c.get("periodo_apuracao",""),
                "competencia_teste": format_competencia_teste(c.get("periodo_apuracao","")),
                "valor_original":   c.get("valor_original",""),
                "valor_utilizado":  c.get("valor_utilizado",""),
                "valor_total_debitos": c.get("valor_total_debitos",""),
                "codigo_credito":   c.get("codigo_credito",""),
                "valor_multa":"","valor_juros":"","valor_total":""})
        for d in p.get("debitos", []):
            rows.append({**hdr,
                "tipo_registro":    "Débito",
                "tipo":             d.get("tipo_debito",""),
                "codigo_receita_debito": d.get("codigo_receita_debito",""),
                "periodo_apuracao": d.get("periodo_apuracao",""),
                "competencia_teste": format_competencia_teste(d.get("periodo_apuracao","")),
                "valor_original":   d.get("valor_principal",""),
                "valor_utilizado":  d.get("valor_compensado",""),
                "valor_multa":      d.get("valor_multa",""),
                "valor_juros":      d.get("valor_juros",""),
                "valor_total":      d.get("valor_total","")})
        if not p.get("creditos") and not p.get("debitos"):
            rows.append({**hdr,
                "tipo_registro":"","tipo":"","periodo_apuracao":"",
                "competencia_teste":"",
                "valor_original":"","valor_utilizado":"",
                "valor_multa":"","valor_juros":"","valor_total":""})
    return rows


