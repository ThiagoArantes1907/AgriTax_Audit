"""Parser do extrato do PGDAS-D (Simples Nacional, 2 layouts do e-CAC).

Extraído do AgriTax Audit v5 consolidado (agritax_audit_consolidado.py),
sem alterações de lógica — apenas modularização (M1 da arquitetura).
"""
import re
import unicodedata
from pathlib import Path

import pdfplumber

from ._util import format_competencia_teste

SIMPLES_MONEY_RE = re.compile(r"-?\d{1,3}(?:\.\d{3})*,\d{2}")

SIMPLES_COLS = [
    ("competencia",      "Competencia",           95),
    ("cnpj",             "CNPJ",                  135),
    ("empresa",          "Empresa",               220),
    ("tipo",             "Tipo Apuracao",         100),
    ("anexo",            "Anexo",                  60),
    ("fator_r",          "Fator r",                70),
    ("rpa",              "Receita Bruta (RPA)",   140),
    ("receita_vendas",   "Rec. Vendas",           115),
    ("receita_servicos", "Rec. Servicos",         115),
    ("receita_outras",   "Rec. Outras",           115),
    ("rbt12",            "RBT12",                 130),
    ("irpj",             "IRPJ",                   95),
    ("csll",             "CSLL",                   95),
    ("cofins",           "COFINS",                100),
    ("pis",              "PIS/Pasep",             100),
    ("cpp",              "INSS/CPP",              105),
    ("icms",             "ICMS",                   85),
    ("ipi",              "IPI",                    80),
    ("iss",              "ISS",                    90),
    ("total_debito",     "Total Debito (DAS)",    130),
    ("aliquota_efetiva", "Aliq. Efetiva %",       105),
    ("num_declaracao",   "No Declaracao/Apur.",   165),
    ("_source",          "Arquivo",               200),
]

# Aba Resumo: consolidado por ano-calendario
SIMPLES_RESUMO_COLS = [
    ("ano",              "Ano",                    70),
    ("qtd_apuracoes",    "Qtd. Apuracoes",        110),
    ("rpa",              "Receita Bruta Total",   150),
    ("receita_vendas",   "Rec. Vendas",           120),
    ("receita_servicos", "Rec. Servicos",         120),
    ("receita_outras",   "Rec. Outras",           120),
    ("irpj",             "IRPJ",                  100),
    ("csll",             "CSLL",                  100),
    ("cofins",           "COFINS",                105),
    ("pis",              "PIS/Pasep",             105),
    ("cpp",              "INSS/CPP",              110),
    ("icms",             "ICMS",                   90),
    ("ipi",              "IPI",                    85),
    ("iss",              "ISS",                    95),
    ("total_debito",     "Total Debito",          130),
    ("aliquota_efetiva", "Aliq. Efetiva %",       110),
]

# Colunas monetarias (alinhamento a direita / formato R$). aliquota_efetiva
# NAO entra aqui — e percentual, formatado a parte.
SIMPLES_MONEY_KEYS = {
    "rpa", "receita_vendas", "receita_servicos", "receita_outras", "rbt12",
    "irpj", "csll", "cofins", "pis", "cpp", "icms", "ipi", "iss", "total_debito",
}

# Palavras-chave que provam que o OCR produziu texto util de DCTF.
# Incluimos variantes com e sem acento porque Tesseract com lang='por'
# preserva acentos (DEBITO / DÉBITO, DECLARACAO / DECLARAÇÃO).
_DCTF_KEYWORDS = ["CNPJ", "DCTF", "DEBITO", "DÉBITO", "TRIBUTO",
                  "MINISTERIO", "MINISTÉRIO", "DECLARACAO", "DECLARAÇÃO"]


def _simples_money(s: str) -> float:
    """Converte valor monetario BR ('2.514.358,13') para float."""
    s = (s or "").strip()
    if not s:
        return 0.0
    try:
        return float(s.replace(".", "").replace(",", "."))
    except ValueError:
        return 0.0


def _simples_norm(s: str) -> str:
    """Minusculas sem acento — para classificar a descricao da atividade."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower()


def _simples_grab_total(lines: list, contains: str, forbid: str = None) -> float:
    """Acha a linha que contem `contains`; o valor TOTAL (3a coluna) pode estar
    na propria linha (layout B) ou na linha imediatamente anterior (layout A,
    onde o rotulo quebra em duas linhas)."""
    for i, ln in enumerate(lines):
        if contains in ln and (not forbid or forbid not in ln):
            for cand in (ln, lines[i - 1] if i > 0 else ""):
                nums = SIMPLES_MONEY_RE.findall(cand)
                if len(nums) >= 3:
                    return _simples_money(nums[-1])
    return 0.0


def _simples_classify(desc: str) -> str:
    """Classifica a atividade em vendas / servicos / outras pela descricao."""
    d = _simples_norm(desc)
    if any(k in d for k in ("revenda", "comercio", "industrializ",
                            "venda de mercador")):
        return "vendas"
    if "locacao" in d:
        return "outras"
    if "servico" in d:
        return "servicos"
    return "outras"


def extract_simples(pdf_path: str) -> list:
    """Processa um Extrato do PGDAS-D e retorna lista com UM dict (uma apuracao).

    Lanca RuntimeError se o PDF nao puder ser interpretado como extrato do
    Simples Nacional.
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            full = "\n".join((pg.extract_text() or "") for pg in pdf.pages)
    except Exception as e:
        raise RuntimeError(f"Nao foi possivel abrir o PDF: {e}")

    if not full.strip():
        raise RuntimeError("PDF sem texto extraivel — nao parece um extrato "
                            "do PGDAS-D do eCAC.")

    if ("Simples Nacional" not in full
            and "PGDAS-D" not in full):
        raise RuntimeError("O PDF nao contem as marcas de um Extrato do "
                            "Simples Nacional (PGDAS-D).")

    lines = [ln.strip() for ln in full.split("\n")]
    nome_arq = Path(pdf_path).name

    layout_b = ("Extrato do Simples Nacional" in full
                or "Período de Apuração (PA)" in full
                or "Periodo de Apuracao (PA)" in full)

    # ── Competencia (MM/YYYY) ─────────────────────────────────────────────
    competencia = ""
    if layout_b:
        m = re.search(r"Per[ií]odo de Apura[çc][ãa]o \(PA\):\s*(\d{2}/\d{4})", full)
        if m:
            competencia = m.group(1)
    else:
        m = re.search(r"Per[ií]odo de Apura[çc][ãa]o:\s*\d{2}/(\d{2})/(\d{4})", full)
        if m:
            competencia = f"{m.group(1)}/{m.group(2)}"
    if not competencia:
        m = re.search(r"(\d{4})[_-](\d{2})", nome_arq)   # fallback pelo nome
        if m:
            competencia = f"{m.group(2)}/{m.group(1)}"

    # ── Tipo de apuracao (Original / Retificadora) ────────────────────────
    tipo = "Retificadora" if re.search(
        r"(Declara[çc][ãa]o|Apura[çc][ãa]o) Retificadora", full) else "Original"

    # ── CNPJ ──────────────────────────────────────────────────────────────
    cnpj = ""
    m = re.search(r"CNPJ Matriz:\s*([\d./-]+)", full)
    if m:
        cnpj = m.group(1).strip()
    if not cnpj:
        m = re.search(r"CNPJ Estabelecimento:\s*(\d{2}\.\d{3}\.\d{3}/0001-\d{2})",
                      full)
        if m:
            cnpj = m.group(1)
    if not cnpj:
        m = re.search(r"CNPJ B[áa]sico:\s*([\d.]+)", full)
        if m:
            cnpj = m.group(1).strip()

    # ── Nome empresarial ──────────────────────────────────────────────────
    empresa = ""
    m = re.search(r"Nome [Ee]mpresarial:\s*(.+)", full)
    if m:
        empresa = m.group(1).split("  ")[0].strip()

    # ── Numero da declaracao / apuracao ───────────────────────────────────
    num_decl = ""
    m = re.search(r"N[ºo°] da Declara[çc][ãa]o:\s*(\d+)", full)
    if m:
        num_decl = m.group(1)
    if not num_decl:
        m = re.search(r"Informa[çc][õo]es da Apura[çc][ãa]o\s*(\d+)", full)
        if m:
            num_decl = m.group(1)

    # ── RPA (Receita Bruta do PA) e RBT12 ─────────────────────────────────
    rpa = _simples_grab_total(lines, "Receita Bruta do PA (RPA)")
    rbt12 = _simples_grab_total(lines, "(RBT12)", forbid="RBT12p")

    # ── Fator r e Anexo ───────────────────────────────────────────────────
    fator_r, anexo = "N/A", ""
    m = re.search(r"Fator r\s*=\s*([^\n]+)", full)
    if m:
        fr = m.group(1).strip()
        mm = re.match(r"(0,\d+)", fr)
        if mm:
            fator_r = mm.group(1)
        ma = re.search(r"Anexo\s+([IVX]+)", fr)
        if ma:
            anexo = ma.group(1)
    if not anexo:
        ma = re.search(r"tributados pelo Anexo\s+([IVX]+)", full)
        if ma:
            anexo = ma.group(1)

    # ── Receita por atividade (classificada Vendas/Servicos/Outras) ───────
    rec = {"vendas": 0.0, "servicos": 0.0, "outras": 0.0}
    for m in re.finditer(
            r"Valor do D[ée]bito por Tributo para a Atividade[^\n]*\n"
            r"(.*?)Receita Bruta Informada:\s*R\$\s*([\d.,]+)",
            full, re.DOTALL):
        rec[_simples_classify(m.group(1))] += _simples_money(m.group(2))

    # ── Debito por tributo — Total Geral da Empresa (Declarado) ───────────
    # Ordem na linha: IRPJ CSLL COFINS PIS/Pasep INSS/CPP ICMS IPI ISS Total
    trib = [0.0] * 9
    idx_tg = next((i for i, ln in enumerate(lines)
                   if "Total Geral da Empresa" in ln), None)
    if idx_tg is not None:
        for ln in lines[idx_tg + 1:]:
            nums = SIMPLES_MONEY_RE.findall(ln)
            if len(nums) >= 9:
                trib = [_simples_money(x) for x in nums[:9]]
                break

    total_debito = trib[8]
    aliquota = round(total_debito / rpa * 100, 2) if rpa else 0.0

    # ── Seção 6 — DAS gerado e sua arrecadação ────────────────────────────
    # 6.0: número do DAS gerado nesta apuração.
    # 6.2: "Data de Pagamento ... Valor Pago" se quitado, ou
    #      "Não foi reconhecido pagamento até a presente data" se em aberto.
    das_numero, das_dt_pagto, das_valor_pago = "", "", 0.0
    m = re.search(r"N[úu]mero:\s*(\d{6,})", full)
    if m:
        das_numero = m.group(1)
    idx_62 = next((i for i, ln in enumerate(lines)
                   if "Informações da Arrecadação" in ln
                   or "Informacoes da Arrecadacao" in ln), None)
    if idx_62 is not None:
        bloco = "\n".join(lines[idx_62:idx_62 + 4])
        if "Não foi reconhecido" not in bloco and "Nao foi reconhecido" not in bloco:
            mp = re.search(r"(\d{2}/\d{2}/\d{4})\s+[\d/]+\s+"
                           r"(\d{1,3}(?:\.\d{3})*,\d{2})", bloco)
            if mp:
                das_dt_pagto   = mp.group(1)
                das_valor_pago = _simples_money(mp.group(2))
    das_pago = bool(das_dt_pagto and das_valor_pago > 0)

    if not competencia and not rpa:
        raise RuntimeError("Nao foi possivel localizar a competencia nem a "
                            "Receita Bruta do PA no extrato.")

    return [{
        "competencia":      competencia,
        "cnpj":             cnpj,
        "empresa":          empresa,
        "tipo":             tipo,
        "anexo":            anexo,
        "fator_r":          fator_r,
        "rpa":              rpa,
        "receita_vendas":   rec["vendas"],
        "receita_servicos": rec["servicos"],
        "receita_outras":   rec["outras"],
        "rbt12":            rbt12,
        "irpj":   trib[0], "csll": trib[1], "cofins": trib[2],
        "pis":    trib[3], "cpp":  trib[4], "icms":   trib[5],
        "ipi":    trib[6], "iss":  trib[7],
        "total_debito":     total_debito,
        "aliquota_efetiva": aliquota,
        "num_declaracao":   num_decl,
        # Quitação via DAS (seção 6) — usada no cruzamento da Conciliação
        "das_numero":       das_numero,
        "das_dt_pagamento": das_dt_pagto,
        "das_valor_pago":   das_valor_pago,
        "das_pago":         das_pago,
        "_source":          nome_arq,
    }]


def build_simples_resumo(rows: list) -> list:
    """Consolida as apuracoes do Simples por ano-calendario."""
    from collections import defaultdict
    _campos = ("rpa", "receita_vendas", "receita_servicos", "receita_outras",
               "irpj", "csll", "cofins", "pis", "cpp", "icms", "ipi", "iss",
               "total_debito")
    acc = defaultdict(lambda: {"qtd_apuracoes": 0,
                               **{k: 0.0 for k in _campos}})
    for r in rows:
        comp = r.get("competencia", "") or ""
        ano = comp.split("/")[-1] if "/" in comp else (comp or "-")
        a = acc[ano]
        a["qtd_apuracoes"] += 1
        for k in _campos:
            a[k] += float(r.get(k, 0) or 0)
    out = []
    for ano, a in sorted(acc.items()):
        aliq = round(a["total_debito"] / a["rpa"] * 100, 2) if a["rpa"] else 0.0
        out.append({"ano": ano, "aliquota_efetiva": aliq, **a})
    return out


