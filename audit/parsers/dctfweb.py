"""Parser de DCTFWeb (XML e PDF, inclusive em .zip).

Extraído do AgriTax Audit v5 consolidado (agritax_audit_consolidado.py),
sem alterações de lógica — apenas modularização (M1 da arquitetura).
"""
import io
import os
import re
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET

import pdfplumber

from ._util import format_competencia_teste, parse_brl

def _dctfweb_xml_periodo_to_mmaaaa(per: str) -> str:
    """Converte o perApuracao do XML para 'MM/AAAA' (ou 'AAAA' se anual).

    XML categoria 40 (mensal): 'MMAAAA' -> 'MM/AAAA'   (ex '112023' -> '11/2023')
    XML categoria 41 (13º/anual): 'AAAA' -> 'AAAA'
    """
    per = (per or "").strip()
    if re.fullmatch(r"\d{6}", per):
        return f"{per[:2]}/{per[2:]}"
    if re.fullmatch(r"\d{4}", per):
        return per
    return per


def _parse_dctfweb_xml(xml_bytes: bytes, source_name: str) -> list:
    """Parser do XML de Saída da DCTFWeb (formato SERPRO DctfXml v3.0).

    Retorna uma lista de dicts no MESMO formato de extract_dctfweb (PDF),
    para não quebrar a Central de Importação / Resumo por Tributo / Excel.

    Cada linha representa um <CreditoTributarioApurado> (um tributo).
    """
    import xml.etree.ElementTree as ET

    try:
        texto = xml_bytes.decode("utf-8", errors="replace")
    except Exception:
        texto = xml_bytes.decode("latin-1", errors="replace")

    # O XML usa namespaces; é mais simples remover os prefixos e trabalhar
    # com nomes de tag puros (regex já basta dado o formato fixo do SERPRO).
    def _tag(nome: str, escopo: str = None) -> str:
        """Extrai o conteúdo da primeira tag <nome> dentro de `escopo`."""
        alvo = escopo if escopo is not None else texto
        m = re.search(rf"<{nome}>(.*?)</{nome}>", alvo, re.S)
        return m.group(1).strip() if m else ""

    # ── Cabeçalho ──
    cnpj_raw = _tag("inscContrib")
    # inscContrib vem só com dígitos — formata XX.XXX.XXX/XXXX-XX
    cnpj_fmt = cnpj_raw
    d = re.sub(r"\D", "", cnpj_raw)
    if len(d) == 14:
        cnpj_fmt = f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"

    per_apuracao = _tag("perApuracao")
    periodo_decl = _dctfweb_xml_periodo_to_mmaaaa(per_apuracao)

    categoria_cod = _tag("categoriaDCTF")
    categoria_map = {
        "40": "Mensal",
        "41": "13º Salário (Anual)",
        "44": "Aferição (Obra/CNO)",
    }
    categoria = categoria_map.get(categoria_cod,
                                  f"Cat. {categoria_cod}" if categoria_cod
                                  else "")

    ind_retif = _tag("indRetificacao")  # 1 = retificadora, 2 = original
    ind_zerada = _tag("indZerada")      # 1 = com movimento, 2 = zerada

    cabecalho = {
        "_source":               source_name,
        "cnpj":                  cnpj_fmt,
        "razao_social":          _tag("nomeContribuinte"),
        "periodo_apuracao_decl": periodo_decl,
        "numero_recibo":         _tag("numRecibo"),
        "dt_transmissao":        "",  # o XML de saída não traz data/hora
        "categoria":             categoria,
        "classificacao_trib":    _tag("clasTrib"),
        "ausencia_fatos":        "Sim" if ind_zerada == "2" else "Não",
        "retificadora":          "Sim" if ind_retif == "1" else "Não",
    }

    # ── Mapeamento de grupos (ctCodGrupo -> nome amigável) ──
    grupo_por_codigo = {
        "13": "IRPJ", "14": "IRRF", "18": "COFINS", "20": "PIS",
        "21": "CSLL", "37": "CSRF",
        "44": "Contribuição Previdenciária",   # patronal
        "45": "Contribuição Previdenciária",   # segurados
        "46": "Outras Contribuições",          # terceiros/outras entidades
    }

    # ── Compensações globais (bloco A035) — para somar por código ──
    comp_por_codrec = {}   # codReceita -> valor total compensado
    comp_lista = []
    bloco_comp = ""
    mbc = re.search(r"<A035-Compensacoes>(.*?)</A035-Compensacoes>",
                    texto, re.S)
    if mbc:
        bloco_comp = mbc.group(1)
        for cm in re.finditer(r"<Compensacao>(.*?)</Compensacao>",
                              bloco_comp, re.S):
            b = cm.group(1)
            cod = _tag("codReceita", b)
            num = _tag("numDoc", b)
            try:
                vlc = float(_tag("vlCredito", b) or "0")
            except ValueError:
                vlc = 0.0
            comp_por_codrec[cod] = comp_por_codrec.get(cod, 0.0) + vlc
            comp_lista.append({"numero_processo": num, "tipo": "DCOMP",
                               "valor": vlc})

    # ── Tributos: cada <CreditoTributarioApurado> ──
    debitos = []
    for m in re.finditer(
            r"<CreditoTributarioApurado>(.*?)</CreditoTributarioApurado>",
            texto, re.S):
        b = m.group(1)

        cod_receita = _tag("codReceita", b)
        descricao = _tag("ctDescricaoTributo", b)
        cod_grupo = _tag("ctCodGrupo", b)
        desc_grupo = _tag("ctDescGrupo", b)

        def _f(tag, escopo=b):
            v = _tag(tag, escopo)
            try:
                return float(v) if v else 0.0
            except ValueError:
                return 0.0

        ct_valor = _f("ctValor")        # débito apurado do tributo
        vl_total_cred = _f("vlTotalCred")  # total de créditos vinculados
        saldo = _f("saldoaPagar")

        # Retenção Lei 9.711 vinculada a este tributo (bloco A270 interno)
        ret_9711 = _f("retLei9711",
                      _tag("A270-RetencaoLei9711ValoresVinculados", b)
                      if "A270-RetencaoLei9711ValoresVinculados" in b
                      else b)

        # Compensação vinculada a este código de receita
        cred_comp = comp_por_codrec.get(cod_receita, 0.0)

        # Período do débito (paDebito vem como DDMMAAAA -> usa MM/AAAA)
        pa = _tag("paDebito", b)
        if re.fullmatch(r"\d{8}", pa):
            periodo_trib = f"{pa[2:4]}/{pa[4:]}"
        else:
            periodo_trib = periodo_decl

        # Grupo amigável
        grupo = grupo_por_codigo.get(cod_grupo)
        if not grupo:
            du = descricao.upper()
            if du.startswith("CP"):
                grupo = "Contribuição Previdenciária"
            elif "IRRF" in du:
                grupo = "IRRF"
            else:
                grupo = desc_grupo.title() if desc_grupo else "Outros"

        debitos.append({
            **cabecalho,
            "codigo_receita":    cod_receita,
            "descricao":         descricao[:180],
            "grupo_tributo":     grupo,
            "cno":               "",
            "cnpj_prest":        "",
            "periodo":           periodo_trib,
            "competencia_teste": format_competencia_teste(periodo_trib),
            "debito_apurado":    ct_valor,
            "deducoes":          0.0,
            "cred_compensacao":  cred_comp,
            "cred_pagamento":    ret_9711,   # retenção 9711 = crédito vinculado
            "cred_suspensao":    0.0,
            "saldo_pagar":       saldo,
            "qtd_compensacoes":  sum(1 for c in comp_lista
                                     if c["valor"] and
                                     cod_receita in comp_por_codrec),
            "numeros_dcomp":     " / ".join(
                c["numero_processo"] for c in comp_lista) if cred_comp else "",
            "_compensacoes_raw": [c for c in comp_lista] if cred_comp else [],
        })

    # Declaração zerada (sem movimento) e nenhum tributo capturado
    if not debitos and cabecalho.get("ausencia_fatos") == "Sim":
        debitos.append({
            **cabecalho,
            "codigo_receita":    "",
            "descricao":         "(Sem fatos geradores)",
            "grupo_tributo":     "—",
            "cno":               "",
            "cnpj_prest":        "",
            "periodo":           periodo_decl,
            "competencia_teste": format_competencia_teste(periodo_decl),
            "debito_apurado":    0.0,
            "deducoes":          0.0,
            "cred_compensacao":  0.0,
            "cred_pagamento":    0.0,
            "cred_suspensao":    0.0,
            "saldo_pagar":       0.0,
            "qtd_compensacoes":  0,
            "numeros_dcomp":     "",
            "_compensacoes_raw": [],
        })

    return debitos


def _extract_dctfweb_from_zip(zip_path: str) -> list:
    """Extrai o XML de dentro de um .zip de DCTFWeb e faz o parse.

    O .zip do eCAC traz o XMLSaida_*.xml + um .p7s (assinatura).
    Usa só o .xml.
    """
    import zipfile
    with zipfile.ZipFile(zip_path) as zf:
        nomes_xml = [n for n in zf.namelist()
                     if n.lower().endswith(".xml")]
        if not nomes_xml:
            raise RuntimeError("O .zip não contém nenhum arquivo .xml.")
        # Se houver mais de um, prioriza o que começa com 'XMLSaida'
        nomes_xml.sort(key=lambda n: (0 if "xmlsaida" in n.lower() else 1, n))
        nome = nomes_xml[0]
        dados = zf.read(nome)
    # Usa o nome do .zip como source (preserva DCTFWEB_AAAA_MM_CAT_XML.zip)
    return _parse_dctfweb_xml(dados, Path(zip_path).name)


def extract_dctfweb(pdf_path: str) -> list:
    """Parser de DCTFWeb — aceita PDF, XML ou ZIP.

    Detecta o formato pela extensão do arquivo:
      .xml  -> XML de Saída da DCTFWeb (formato SERPRO DctfXml v3.0)
      .zip  -> .zip do eCAC contendo o XML de Saída
      .pdf  -> Relatório PDF da Declaração Completa (parser legado)

    Retorna sempre o mesmo formato: lista de dicts, um por tributo.
    """
    ext = Path(pdf_path).suffix.lower()

    if ext == ".zip":
        return _extract_dctfweb_from_zip(pdf_path)

    if ext == ".xml":
        with open(pdf_path, "rb") as f:
            return _parse_dctfweb_xml(f.read(), Path(pdf_path).name)

    # ext == ".pdf" (ou desconhecido) — parser PDF legado
    return _extract_dctfweb_pdf(pdf_path)


def _extract_dctfweb_pdf(pdf_path: str) -> list:
    """Parser de DCTFWeb (Declaração Completa) — formato PDF.

    Retorna uma lista de dicts, um por bloco "Débito Apurado e Crédito Vinculado".
    Para declarações com "Ausência de Fatos Geradores: Sim", retorna 1 linha
    representando a declaração negativa (sem tributos).
    """
    # PDF é texto puro — usa pdfplumber (sem OCR)
    try:
        with pdfplumber.open(pdf_path) as pdf:
            full_text = "\n".join(page.extract_text(layout=True) or ""
                                   for page in pdf.pages)
    except Exception:
        # Fallback para pdftotext se pdfplumber falhar
        try:
            import subprocess
            r = subprocess.run(["pdftotext", "-layout", pdf_path, "-"],
                                capture_output=True, text=True, timeout=60)
            full_text = r.stdout
        except Exception as e:
            raise RuntimeError(f"Falha ao extrair texto do PDF: {e}")

    if not full_text.strip():
        return []

    # ── Cabeçalho ──
    cabecalho = {}
    nome_arq = Path(pdf_path).name
    cabecalho["_source"] = nome_arq

    # CNPJ
    m = re.search(r'CNPJ\s+(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})', full_text)
    cabecalho["cnpj"] = m.group(1) if m else ""

    # Razão social — pega linha ANTES e linha DEPOIS de "Nome do Contribuinte",
    # filtrando ruído do cabeçalho ministerial
    razao = ""
    lines = full_text.split("\n")
    for i, ln in enumerate(lines):
        if "Nome do Contribuinte" in ln:
            parte1 = lines[i-1].strip() if i > 0 else ""
            parte2 = lines[i+1].strip() if i+1 < len(lines) else ""
            for ruido in ("MINISTÉRIO", "RELATÓRIO", "SECRETARIA"):
                if ruido in parte1.upper(): parte1 = ""
                if ruido in parte2.upper(): parte2 = ""
            if parte2.startswith("CNPJ") or "Período" in parte2:
                parte2 = ""
            razao = (parte1 + " " + parte2).strip()
            break
    cabecalho["razao_social"] = razao

    # Período da declaração
    m = re.search(r'Período apuração\s+(\d{2}/\d{4}|\d{4})', full_text)
    cabecalho["periodo_apuracao_decl"] = m.group(1) if m else ""

    # Número do Recibo
    m = re.search(r'Número do Recibo\s+(\S+)', full_text)
    cabecalho["numero_recibo"] = m.group(1) if m else ""

    # Data/Hora — primeira data DD/MM/AAAA HH:MM:SS
    m = re.search(r'(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2})', full_text)
    cabecalho["dt_transmissao"] = m.group(1) if m else ""

    # Categoria via nome do arquivo: padrão *_NN_*.pdf onde NN é a categoria
    # Aceita também sufixo " (1)", " (2)" etc., ou número de aferição após o NN
    cat = ""
    m = re.search(r'_(\d{2})_(?:\d+)?(?:\s*\([^\)]*\))?\.pdf$', nome_arq)
    if m:
        cat = m.group(1)
    categoria_map = {
        "40": "Mensal",
        "41": "13º Salário (Anual)",
        "44": "Aferição (Obra/CNO)",
    }
    cabecalho["categoria"] = categoria_map.get(cat, f"Cat. {cat}" if cat else "")

    # Classificação Tributária
    m = re.search(r'Classificação Tributária\s+(.+?)(?:\n|$)', full_text)
    cabecalho["classificacao_trib"] = m.group(1).strip()[:100] if m else ""

    # Ausência de Fatos Geradores
    m = re.search(r'Ausência de Fatos Geradores\s+(Sim|Não)', full_text)
    cabecalho["ausencia_fatos"] = m.group(1) if m else "Não"

    # ── Blocos "Débito Apurado e Crédito Vinculado" ──
    blocos = re.split(r'Débito Apurado e Crédito Vinculado', full_text)[1:]

    debitos = []
    for bloco in blocos:
        mc = re.search(r'Código da Receita\s+(\d{4}-\d{2})', bloco)
        if not mc:
            continue
        codigo = mc.group(1)

        # Descrição (até quebra de linha ou novo campo)
        md = re.search(
            r'Descrição\s+(.+?)(?:\n\s*CNPJ Prest|\n\s*CNO\s|\n\s*Período Apuração|\n\s*Débito\s)',
            bloco, re.S)
        descricao = " ".join(md.group(1).split()) if md else ""

        # CNO (Cadastro Nacional de Obras)
        mcno = re.search(r'CNO\s+([\d\.]+/\d+|\-)', bloco)
        cno = mcno.group(1) if (mcno and mcno.group(1) != "-") else ""

        # CNPJ Prest/Incorp
        mcnpj = re.search(
            r'CNPJ Prest/Incorp\s*\n?.*?\s+(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})',
            bloco, re.S)
        cnpj_prest = mcnpj.group(1) if mcnpj else ""

        # Período do tributo (pode diferir do período da declaração — ex: 13º)
        mp = re.search(r'Período Apuração\s*\n?\s*(\d{2}/\d{4}|\d{4})', bloco)
        periodo = mp.group(1) if mp else cabecalho.get("periodo_apuracao_decl", "")

        # Valores monetários
        def _money(label, txt):
            mm = re.search(rf'{label}\s+([\d\.]+,\d{{2}})', txt)
            if mm:
                try: return float(mm.group(1).replace(".", "").replace(",", "."))
                except ValueError: return 0.0
            return 0.0

        debito_apurado = _money(r'Débito Apurado', bloco)

        # Deduções (Salário Família) — linha "Deduções Salário Família: X,XX"
        m_ded = re.search(r'Deduções\s+(?:[^\d\n]*?)([\d\.]+,\d{2})', bloco)
        deducoes = float(m_ded.group(1).replace(".","").replace(",",".")) if m_ded else 0.0

        m_comp = re.search(r'Créditos Compensação:\s*([\d\.]+,\d{2})', bloco)
        cred_comp = float(m_comp.group(1).replace(".","").replace(",",".")) if m_comp else 0.0

        m_pag = re.search(r'Créditos Pagamento:\s*([\d\.]+,\d{2})', bloco)
        cred_pag = float(m_pag.group(1).replace(".","").replace(",",".")) if m_pag else 0.0

        m_susp = re.search(r'Créditos Suspensão:\s*([\d\.]+,\d{2})', bloco)
        cred_susp = float(m_susp.group(1).replace(".","").replace(",",".")) if m_susp else 0.0

        saldo = _money(r'Saldo a Pagar', bloco)

        # Compensações vinculadas (lista de DCOMPs)
        compensacoes = []
        for cm in re.finditer(
                r'Número do Processo\s+(\S+).+?Tipo\s+(\S+).+?Valor\s+([\d\.]+,\d{2})',
                bloco, re.S):
            compensacoes.append({
                "numero_processo": cm.group(1),
                "tipo": cm.group(2),
                "valor": float(cm.group(3).replace(".","").replace(",",".")),
            })

        # Grupo deriva da descrição
        desc_upper = descricao.upper()
        if descricao.startswith("CP"):
            grupo = "Contribuição Previdenciária"
        elif "IRRF" in desc_upper:
            grupo = "IRRF"
        elif "RET DE CONTRIB" in desc_upper or "CONTRIBUI" in desc_upper:
            grupo = "Outras Contribuições"
        else:
            grupo = "Outros"

        debitos.append({
            **cabecalho,
            "codigo_receita":   codigo,
            "descricao":        descricao[:180],
            "grupo_tributo":    grupo,
            "cno":              cno,
            "cnpj_prest":       cnpj_prest,
            "periodo":          periodo,
            "competencia_teste": format_competencia_teste(periodo),
            "debito_apurado":   debito_apurado,
            "deducoes":         deducoes,
            "cred_compensacao": cred_comp,
            "cred_pagamento":   cred_pag,
            "cred_suspensao":   cred_susp,
            "saldo_pagar":      saldo,
            "qtd_compensacoes": len(compensacoes),
            "numeros_dcomp":    " / ".join(c["numero_processo"] for c in compensacoes),
            "_compensacoes_raw": compensacoes,
        })

    # Se "Ausência de Fatos Geradores: Sim" e não capturamos nenhum bloco,
    # gera 1 linha representando a declaração negativa
    if not debitos and cabecalho.get("ausencia_fatos") == "Sim":
        debitos.append({
            **cabecalho,
            "codigo_receita":   "",
            "descricao":        "(Sem fatos geradores)",
            "grupo_tributo":    "—",
            "cno":              "",
            "cnpj_prest":       "",
            "periodo":          cabecalho.get("periodo_apuracao_decl", ""),
            "competencia_teste": format_competencia_teste(
                cabecalho.get("periodo_apuracao_decl", "")),
            "debito_apurado":   0.0,
            "deducoes":         0.0,
            "cred_compensacao": 0.0,
            "cred_pagamento":   0.0,
            "cred_suspensao":   0.0,
            "saldo_pagar":      0.0,
            "qtd_compensacoes": 0,
            "numeros_dcomp":    "",
            "_compensacoes_raw": [],
        })

    return debitos


def build_dctfweb_resumo(rows: list) -> list:
    """Resumo por código de receita (totalizadores)."""
    if not rows:
        return []
    grupos = {}
    for r in rows:
        cod = r.get("codigo_receita", "")
        if not cod: continue   # ignora linhas de "sem fatos geradores"
        if cod not in grupos:
            grupos[cod] = {
                "codigo_receita": cod,
                "descricao": r.get("descricao", ""),
                "grupo_tributo": r.get("grupo_tributo", ""),
                "qtd_declaracoes": 0,
                "total_debito": 0.0,
                "total_deducoes": 0.0,
                "total_compensacao": 0.0,
                "total_pagamento": 0.0,
                "total_suspensao": 0.0,
                "total_saldo": 0.0,
            }
        g = grupos[cod]
        g["qtd_declaracoes"] += 1
        g["total_debito"]      += float(r.get("debito_apurado", 0) or 0)
        g["total_deducoes"]    += float(r.get("deducoes", 0) or 0)
        g["total_compensacao"] += float(r.get("cred_compensacao", 0) or 0)
        g["total_pagamento"]   += float(r.get("cred_pagamento", 0) or 0)
        g["total_suspensao"]   += float(r.get("cred_suspensao", 0) or 0)
        g["total_saldo"]       += float(r.get("saldo_pagar", 0) or 0)
    return sorted(grupos.values(), key=lambda x: x["codigo_receita"])


