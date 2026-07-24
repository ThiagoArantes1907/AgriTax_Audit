"""Parser de ECF (Escrituração Contábil Fiscal) — apuração de IRPJ/CSLL.

Construído a partir de 20 ECFs reais do ReceitaNetBX (COD_VER 0008–0011),
não herdado do v5 (que não tinha parser de ECF).

Registros lidos:
    0000  cabeçalho: CNPJ, nome, período, retificadora (S/N) + recibo
    0010  forma de tributação (1=Real … 5=Presumido) e de apuração (A/T)
    *030  identificação do período corrente (T01..T04, A00=ajuste, A01..A12)
    P300/P500  Presumido: IRPJ/CSLL apurados por trimestre
    N630/N670  Real: IRPJ/CSLL do ajuste anual ou trimestre
    N620/N660  Real anual: estimativas mensais (imposto devido no mês)

As linhas dos demonstrativos são (código, descrição, valor); o casamento é
pela DESCRIÇÃO (estável entre versões de layout; os códigos conferidos nos
arquivos reais: N630/26, N670/21, P300/15, P500/13, N620/26, N660/18).
"""
from __future__ import annotations

import re
from pathlib import Path

from ._util import format_competencia_teste, parse_brl

FORMA_TRIB = {
    "1": "LUCRO_REAL", "2": "LUCRO_REAL_ARBITRADO", "3": "PRESUMIDO_REAL",
    "4": "PRESUMIDO_REAL_ARBITRADO", "5": "LUCRO_PRESUMIDO",
    "6": "PRESUMIDO_ARBITRADO", "7": "ARBITRADO", "8": "IMUNE", "9": "ISENTA",
}

# código de receita (DARF) padrão por tipo de apuração — join do CR-04/05
CODIGO_DARF = {
    ("IRPJ", "presumido"): "2089", ("CSLL", "presumido"): "2372",
    ("IRPJ", "real_trimestral"): "0220", ("CSLL", "real_trimestral"): "6012",
    ("IRPJ", "real_estimativa"): "2362", ("CSLL", "real_estimativa"): "2484",
    ("IRPJ", "real_ajuste"): "2430", ("CSLL", "real_ajuste"): "6773",
}

# (registro, descrição normalizada) → papel da linha
_LINHAS = {
    ("P300", "IMPOSTO DE RENDA A PAGAR"): ("IRPJ", "presumido", "valor"),
    ("P300", "BASE DE CALCULO DO IMPOSTO SOBRE O LUCRO PRESUMIDO"): ("IRPJ", "presumido", "base"),
    ("P500", "CSLL A PAGAR"): ("CSLL", "presumido", "valor"),
    ("P500", "BASE DE CALCULO DA CSLL"): ("CSLL", "presumido", "base"),
    ("N630", "IMPOSTO DE RENDA A PAGAR"): ("IRPJ", "real", "valor"),
    ("N630", "BASE DE CALCULO DO IRPJ"): ("IRPJ", "real", "base"),
    ("N670", "CSLL A PAGAR"): ("CSLL", "real", "valor"),
    ("N670", "BASE DE CALCULO DA CSLL"): ("CSLL", "real", "base"),
    ("N620", "IMPOSTO DEVIDO NO MES"): ("IRPJ", "real_estimativa", "valor"),
    ("N620", "BASE DE CALCULO DO IMPOSTO DE RENDA"): ("IRPJ", "real_estimativa", "base"),
    ("N660", "CSLL DEVIDA NO MES"): ("CSLL", "real_estimativa", "valor"),
    ("N660", "BASE DE CALCULO DA CSLL"): ("CSLL", "real_estimativa", "base"),
}


def _norm_desc(s: str) -> str:
    s = s.upper().strip()
    return s.translate(str.maketrans("ÁÀÂÃÉÊÍÓÔÕÚÜÇ", "AAAAEEIOOOUUC"))


def _brl(s: str) -> float:
    s = (s or "").strip()
    if not s:
        return 0.0
    try:
        return parse_brl(s) if "," in s else float(s)
    except (ValueError, TypeError):
        return 0.0


def _decode(path: str | Path) -> list[str]:
    dados = Path(path).read_bytes()
    texto = dados.decode("latin-1", errors="replace")
    return texto.replace("\r\n", "\n").split("\n")


def _competencia(per_apur: str, dt_ini: str, dt_fin: str) -> str:
    """T04 + 01102024 → '2024.4T' | A00 → '2024' | A07 → '2024.07'."""
    ano = dt_fin[4:8] if len(dt_fin) == 8 else dt_ini[4:8]
    m = re.fullmatch(r"T(\d{2})", per_apur or "")
    if m:
        return f"{ano}.{int(m.group(1))}T"
    if per_apur == "A00":
        return ano
    m = re.fullmatch(r"A(\d{2})", per_apur or "")
    if m:
        return f"{ano}.{m.group(1)}"
    return format_competencia_teste(f"{dt_fin[2:4]}/{ano}") if len(dt_fin) == 8 else ano


def extract_ecf(path: str | Path) -> list[dict]:
    """Uma linha por (período de apuração × tributo) com valor apurado e base."""
    nome = Path(path).name
    linhas = _decode(path)

    cab = {"_source": nome, "cnpj": "", "razao_social": "", "dt_ini": "",
           "dt_fin": "", "retificadora": False, "num_rec_anterior": "",
           "forma_trib": "", "forma_apur": ""}
    # (per_apur, tributo) → row em construção
    apuracoes: dict = {}
    per_atual = ("", "", "")   # (per_apur, dt_ini, dt_fin)

    for ln in linhas:
        if not ln.startswith("|"):
            continue
        c = ln.split("|")[1:-1] if ln.rstrip().endswith("|") else ln.split("|")[1:]
        if not c:
            continue
        reg = c[0]

        if reg == "0000" and len(c) >= 11:
            # |0000|LECF|COD_VER|CNPJ|NOME|IND_SIT_INI|SIT_ESP|PAT_REMAN|DT_SIT_ESP|
            #  DT_INI|DT_FIN|RETIFICADORA|NUM_REC|TIP_ECF|COD_SCP
            cnpj_raw = re.sub(r"\D", "", c[3])
            cab["cnpj"] = (f"{cnpj_raw[0:2]}.{cnpj_raw[2:5]}.{cnpj_raw[5:8]}"
                           f"/{cnpj_raw[8:12]}-{cnpj_raw[12:14]}"
                           if len(cnpj_raw) == 14 else c[3])
            cab["razao_social"] = c[4].strip()[:200]
            cab["dt_ini"], cab["dt_fin"] = c[9], c[10]
            cab["retificadora"] = (c[11].strip().upper() == "S") if len(c) > 11 else False
            cab["num_rec_anterior"] = c[12].strip() if len(c) > 12 else ""
        elif reg == "0010" and len(c) >= 5:
            # |0010|HASH_ANT|OPT_REFIS|FORMA_TRIB|FORMA_APUR|...
            cab["forma_trib"] = FORMA_TRIB.get(c[3].strip(), c[3].strip())
            cab["forma_apur"] = c[4].strip()   # A = anual, T = trimestral
        elif reg.endswith("030") and len(reg) == 4 and len(c) >= 4:
            per_atual = (c[3].strip(), c[1].strip(), c[2].strip())
        elif len(c) >= 4 and (reg, _norm_desc(c[2])) in _LINHAS:
            tributo, tipo, papel = _LINHAS[(reg, _norm_desc(c[2]))]
            if tipo == "real":   # ajuste anual (A00) ou trimestre real
                tipo = "real_ajuste" if per_atual[0] == "A00" else "real_trimestral"
            per_apur, dt_i, dt_f = per_atual
            chave = (per_apur, tributo)
            row = apuracoes.setdefault(chave, {
                **cab,
                "periodo_apuracao": per_apur,
                "competencia_teste": _competencia(per_apur, dt_i or cab["dt_ini"],
                                                  dt_f or cab["dt_fin"]),
                "tributo": tributo, "tipo_apuracao": tipo,
                "codigo_receita": CODIGO_DARF.get((tributo, tipo), ""),
                "base_calculo": 0.0, "valor_apurado": 0.0,
            })
            row["base_calculo" if papel == "base" else "valor_apurado"] = _brl(c[3])

    return [apuracoes[k] for k in sorted(apuracoes)]


_REGS_DEMONSTRATIVO = ("P200", "P300", "P400", "P500",
                       "N620", "N630", "N660", "N670")


def extract_linhas_demonstrativo(path: str | Path,
                                 registros=_REGS_DEMONSTRATIVO) -> list[dict]:
    """Todas as linhas (código, descrição, valor) dos demonstrativos, com o
    período corrente — insumo da reperformance RP-02."""
    nome = Path(path).name
    cnpj = forma_trib = ""
    per_atual = ("", "", "")
    saida = []
    for ln in _decode(path):
        if not ln.startswith("|"):
            continue
        c = ln.split("|")[1:-1] if ln.rstrip().endswith("|") else ln.split("|")[1:]
        if not c:
            continue
        reg = c[0]
        if reg == "0000" and len(c) >= 11:
            cnpj = re.sub(r"\D", "", c[3])
        elif reg == "0010" and len(c) >= 5:
            forma_trib = FORMA_TRIB.get(c[3].strip(), c[3].strip())
        elif reg.endswith("030") and len(reg) == 4 and len(c) >= 4:
            per_atual = (c[3].strip(), c[1].strip(), c[2].strip())
        elif reg in registros and len(c) >= 4:
            per, dt_i, dt_f = per_atual
            saida.append({
                "_source": nome, "cnpj": cnpj, "forma_trib": forma_trib,
                "registro": reg, "periodo_apuracao": per,
                "competencia_teste": _competencia(per, dt_i, dt_f),
                "dt_ini": dt_i, "dt_fin": dt_f,
                "codigo": c[1].strip(), "descricao": c[2].strip(),
                "valor": _brl(c[3]),
            })
    return saida
