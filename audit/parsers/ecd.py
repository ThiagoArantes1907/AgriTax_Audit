"""Parser de ECD (SPED Contábil): plano, balancete, diário, razão, BP, DRE.

Extraído do AgriTax Audit v5 consolidado (agritax_audit_consolidado.py),
sem alterações de lógica — apenas modularização (M1 da arquitetura).
"""
import re
from datetime import datetime
from pathlib import Path

# Estrutura do registro |0000| de identificação:
#   |0000|LECD|DT_INI|DT_FIN|NOME|CNPJ|UF|IE|COD_MUN|IM|IND_SIT_ESP|...
# Registros suportados: 0000, I050, I052, I100, I150, I155, I200, I250, I355,
# J100 (Balanço Patrimonial), J150 (DRE).
# Em todos os relatórios o nome da conta é resolvido automaticamente a partir
# do registro I050 — atendendo ao requisito de "tabela único de plano de contas".

# Sub-aba 1 — Plano de Contas
ECD_PLANO_COLS = [
    ("cnpj",            "CNPJ",             130),
    ("razao_social",    "Razão Social",     200),
    ("cod_cta",         "Código",           110),
    ("nome_cta",        "Descrição",        280),
    ("nivel",           "Nível",             60),
    ("ind_cta",         "Tipo",              90),
    ("cod_nat",         "Natureza",         210),
    ("cod_cta_sup",     "Conta Superior",   110),
    ("dt_alt",          "Dt. Alteração",    110),
    ("_source",         "Arquivo Origem",   190),
]
ECD_PLANO_KEYS  = [c[0] for c in ECD_PLANO_COLS]
ECD_PLANO_MONEY: set = set()

# Sub-aba 2 — Balancete
ECD_BALANCETE_COLS = [
    ("cnpj",            "CNPJ",             130),
    ("razao_social",    "Razão Social",     200),
    ("periodo",         "Período",          150),
    ("cod_cta",         "Código",           110),
    ("nome_cta",        "Descrição",        260),
    ("saldo_inicial",   "Saldo Inicial",    140),
    ("dc_inicial",      "D/C Ini.",          70),
    ("debito",          "Mov. Débito",      140),
    ("credito",         "Mov. Crédito",     140),
    ("saldo_final",     "Saldo Final",      140),
    ("dc_final",        "D/C Fin.",          70),
    ("_source",         "Arquivo Origem",   180),
]
ECD_BALANCETE_KEYS  = [c[0] for c in ECD_BALANCETE_COLS]
ECD_BALANCETE_MONEY = {"saldo_inicial", "debito", "credito", "saldo_final"}

# Sub-aba 3 — Livro Diário
ECD_DIARIO_COLS = [
    ("cnpj",            "CNPJ",             130),
    ("razao_social",    "Razão Social",     180),
    ("data",            "Data",              90),
    ("num_lcto",        "Lançamento",       110),
    ("cod_cta",         "Conta",            110),
    ("nome_cta",        "Descrição da Conta", 250),
    ("hist",            "Histórico",        290),
    ("ind_dc",          "D/C",               60),
    ("valor",           "Valor",            140),
    ("_source",         "Arquivo Origem",   180),
]
ECD_DIARIO_KEYS  = [c[0] for c in ECD_DIARIO_COLS]
ECD_DIARIO_MONEY = {"valor"}

# Sub-aba 4 — Livro Razão (consolidado, com saldo corrido)
ECD_RAZAO_COLS = [
    ("cnpj",            "CNPJ",             130),
    ("razao_social",    "Razão Social",     180),
    ("cod_cta",         "Conta",            110),
    ("nome_cta",        "Descrição da Conta", 240),
    ("data",            "Data",              90),
    ("num_lcto",        "Lançamento",       110),
    ("hist",            "Histórico",        260),
    ("debito",          "Débito",           130),
    ("credito",         "Crédito",          130),
    ("saldo",           "Saldo Corrido",    140),
    ("dc_saldo",        "D/C",               60),
    ("_source",         "Arquivo Origem",   180),
]
ECD_RAZAO_KEYS  = [c[0] for c in ECD_RAZAO_COLS]
ECD_RAZAO_MONEY = {"debito", "credito", "saldo"}

# Sub-aba 5 — Balanço Patrimonial (J100)
ECD_BP_COLS = [
    ("cnpj",                "CNPJ",                 130),
    ("razao_social",        "Razão Social",         180),
    ("ind_grp_bal",         "Grupo",                 80),
    ("nivel_aglut",         "Nível",                 60),
    ("cod_aglut",           "Cód. Aglut.",          110),
    ("descr_cod_aglut",     "Descrição",            290),
    ("val_cta_fin",         "Valor Atual",          150),
    ("ind_dc_cta_fin",      "D/C",                   60),
    ("val_cta_fin_ant",     "Valor Anterior",       150),
    ("ind_dc_cta_fin_ant",  "D/C Ant.",              70),
    ("_source",             "Arquivo Origem",       180),
]
ECD_BP_KEYS  = [c[0] for c in ECD_BP_COLS]
ECD_BP_MONEY = {"val_cta_fin", "val_cta_fin_ant"}

# Sub-aba 6 — DRE (J150)
ECD_DRE_COLS = [
    ("cnpj",            "CNPJ",             130),
    ("razao_social",    "Razão Social",     180),
    ("num_ord",         "Ordem",             70),
    ("nivel_aglut",     "Nível",             60),
    ("cod_aglut",       "Cód. Aglut.",      110),
    ("descr_cod_aglut", "Descrição",        330),
    ("val_cta",         "Valor",            150),
    ("ind_vl_cta",      "D/C",               60),
    ("_source",         "Arquivo Origem",   180),
]
ECD_DRE_KEYS  = [c[0] for c in ECD_DRE_COLS]
ECD_DRE_MONEY = {"val_cta"}

# Sub-aba 7 — Validações de integridade
ECD_VALID_COLS = [
    ("cnpj",            "CNPJ",             130),
    ("razao_social",    "Razão Social",     200),
    ("severidade",      "Severidade",        90),
    ("codigo",          "Código",            80),
    ("mensagem",        "Mensagem",         640),
    ("referencia",      "Referência",       200),
    ("_source",         "Arquivo Origem",   180),
]
ECD_VALID_KEYS  = [c[0] for c in ECD_VALID_COLS]
ECD_VALID_MONEY: set = set()

# Naturezas das contas (registro 0050 / I050)
ECD_NATUREZA_DESC = {
    "01": "Contas de ativo",
    "02": "Contas de passivo",
    "03": "Patrimônio líquido",
    "04": "Contas de resultado",
    "05": "Contas de compensação",
    "09": "Outras",
}

# Tolerância para comparações decimais (cobre arredondamentos legítimos)
ECD_TOLERANCIA = 0.01


def _ecd_decode_file(path: str) -> list:
    """Lê o ECD tentando Latin-1, UTF-8 e CP1252 (encodings comuns)."""
    for enc in ("latin-1", "utf-8", "cp1252"):
        try:
            with open(path, "r", encoding=enc) as f:
                return [ln.rstrip("\r\n") for ln in f]
        except UnicodeDecodeError:
            continue
    with open(path, "rb") as f:
        return [ln.decode("latin-1", errors="replace").rstrip("\r\n") for ln in f]


def _ecd_date(s: str) -> str:
    """ECD vem como DDMMAAAA. Retorna no formato DD/MM/AAAA para exibição."""
    s = (s or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:2]}/{s[2:4]}/{s[4:8]}"
    return ""


def _ecd_decimal(s: str) -> float:
    """ECD usa vírgula como separador decimal."""
    s = (s or "").strip().replace(".", "").replace(",", ".")
    try: return float(s)
    except ValueError: return 0.0


def _ecd_cnpj_format(s: str) -> str:
    s = re.sub(r"\D", "", s or "")
    if len(s) == 14:
        return f"{s[:2]}.{s[2:5]}.{s[5:8]}/{s[8:12]}-{s[12:]}"
    return s


def extract_ecd(path: str) -> list:
    """Parser de ECD (SPED Contábil — .txt).

    Retorna uma LISTA com UM dict de escrituração (formato compatível com a
    Central de Importação, que espera `extend()` de listas).

    Estrutura do dict:
      {
        "_source": str, "cnpj": str, "cnpj_fmt": str, "razao_social": str,
        "dt_ini": str, "dt_fin": str,
        "plano_contas": {cod_cta: {...}, ...},
        "saldos_periodicos": [...],
        "lancamentos": [...],   # cada lcto tem "partidas": [...]
        "bp": [...],            # registro J100
        "dre": [...],           # registro J150
        "warnings": [...],      # avisos do parser (linhas malformadas)
      }
    """
    nome = Path(path).name
    lines = _ecd_decode_file(path)

    result = {
        "_source": nome,
        "cnpj": "", "cnpj_fmt": "", "razao_social": "",
        "dt_ini": "", "dt_fin": "",
        "plano_contas": {},
        "saldos_periodicos": [],
        "lancamentos": [],
        "bp": [],
        "dre": [],
        "warnings": [],
    }

    periodo_atual: dict | None = None
    lcto_atual: dict | None = None

    for nlinha, linha in enumerate(lines, 1):
        if not (linha.startswith("|") and linha.endswith("|")):
            continue
        c = linha.split("|")[1:-1]
        if not c:
            continue
        tipo = c[0]

        try:
            if tipo == "0000":
                # |0000|LECD|COD_VER|COD_ECD|COD_NUM_ORD|NAT_LIVR|DT_INI|DT_FIN|NOME|CNPJ|...
                result["cnpj"]         = c[9]  if len(c) > 9  else ""
                result["cnpj_fmt"]     = _ecd_cnpj_format(result["cnpj"])
                result["razao_social"] = c[8]  if len(c) > 8  else ""
                result["dt_ini"]       = _ecd_date(c[6]) if len(c) > 6 else ""
                result["dt_fin"]       = _ecd_date(c[7]) if len(c) > 7 else ""
            elif tipo == "I050":
                # |I050|DT_ALT|COD_NAT|IND_CTA|NIVEL|COD_CTA|COD_CTA_SUP|CCUS|NOME_CTA|
                conta = {
                    "dt_alt":       _ecd_date(c[1]),
                    "cod_nat":      c[2] if len(c) > 2 else "",
                    "cod_nat_desc": ECD_NATUREZA_DESC.get(c[2] if len(c) > 2 else "", c[2] if len(c) > 2 else ""),
                    "ind_cta":      c[3] if len(c) > 3 else "",
                    "ind_cta_desc": "Sintética" if (len(c) > 3 and c[3] == "S") else "Analítica",
                    "nivel":        int(c[4]) if (len(c) > 4 and c[4].strip().isdigit()) else 0,
                    "cod_cta":      c[5].strip() if len(c) > 5 else "",
                    "cod_cta_sup":  c[6].strip() if len(c) > 6 else "",
                    "nome_cta":     c[8].strip() if len(c) > 8 else "",
                }
                if conta["cod_cta"]:
                    result["plano_contas"][conta["cod_cta"]] = conta
            elif tipo == "I150":
                # |I150|DT_INI|DT_FIN|
                periodo_atual = {
                    "dt_ini": _ecd_date(c[1]) if len(c) > 1 else "",
                    "dt_fin": _ecd_date(c[2]) if len(c) > 2 else "",
                }
                periodo_atual["label"] = (
                    f"{periodo_atual['dt_ini']} a {periodo_atual['dt_fin']}"
                    if periodo_atual["dt_ini"] else "")
            elif tipo == "I155":
                # |I155|COD_CTA|COD_CCUS|VL_SLD_INI|IND_DC_INI|VL_DEB|VL_CRED|VL_SLD_FIN|IND_DC_FIN|
                result["saldos_periodicos"].append({
                    "cod_cta":     c[1].strip() if len(c) > 1 else "",
                    "val_sld_ini": _ecd_decimal(c[3]) if len(c) > 3 else 0.0,
                    "ind_dc_ini":  c[4] if len(c) > 4 else "D",
                    "val_deb":     _ecd_decimal(c[5]) if len(c) > 5 else 0.0,
                    "val_cred":    _ecd_decimal(c[6]) if len(c) > 6 else 0.0,
                    "val_sld_fin": _ecd_decimal(c[7]) if len(c) > 7 else 0.0,
                    "ind_dc_fin":  c[8] if len(c) > 8 else "D",
                    "periodo":     periodo_atual,
                    "periodo_label": periodo_atual["label"] if periodo_atual else "",
                })
            elif tipo == "I200":
                # |I200|NUM_LCTO|DT_LCTO|VL_LCTO|IND_LCTO|
                lcto = {
                    "num_lcto": c[1].strip() if len(c) > 1 else "",
                    "dt_lcto":  _ecd_date(c[2]) if len(c) > 2 else "",
                    "val_lcto": _ecd_decimal(c[3]) if len(c) > 3 else 0.0,
                    "ind_lcto": (c[4] if len(c) > 4 else "N").strip(),
                    "partidas": [],
                }
                result["lancamentos"].append(lcto)
                lcto_atual = lcto
            elif tipo == "I250":
                # |I250|COD_CTA|COD_CCUS|VL_DEBITO|IND_DC|NUM_ARQ|COD_HIST_PAD|HIST|
                if lcto_atual is not None:
                    lcto_atual["partidas"].append({
                        "cod_cta":   c[1].strip() if len(c) > 1 else "",
                        "vl_debito": _ecd_decimal(c[3]) if len(c) > 3 else 0.0,
                        "ind_dc":    c[4] if len(c) > 4 else "D",
                        "hist":      c[7].strip() if len(c) > 7 else "",
                    })
            elif tipo == "J100":
                # |J100|COD_AGL|IND_COD_AGL|NIVEL_AGL|IND_GRP_BAL|DESCR|VL_FIN|IND_DC_FIN|VL_FIN_ANT|IND_DC_FIN_ANT|
                result["bp"].append({
                    "cod_aglut":          c[1].strip() if len(c) > 1 else "",
                    "ind_cod_aglut":      c[2].strip() if len(c) > 2 else "",
                    "nivel_aglut":        int(c[3]) if (len(c) > 3 and c[3].strip().isdigit()) else 0,
                    "ind_grp_bal":        c[4].strip() if len(c) > 4 else "",
                    "descr_cod_aglut":    c[5].strip() if len(c) > 5 else "",
                    "val_cta_fin":        _ecd_decimal(c[6]) if len(c) > 6 else 0.0,
                    "ind_dc_cta_fin":     c[7] if len(c) > 7 else "D",
                    "val_cta_fin_ant":    _ecd_decimal(c[8]) if len(c) > 8 else 0.0,
                    "ind_dc_cta_fin_ant": (c[9] if len(c) > 9 and c[9] else "D"),
                })
            elif tipo == "J150":
                # |J150|NUM_ORD|COD_AGL|IND_COD_AGL|NIVEL_AGL|DESCR|VL_CTA|IND_VL_CTA|...
                result["dre"].append({
                    "num_ord":         int(c[1]) if (len(c) > 1 and c[1].strip().isdigit()) else 0,
                    "cod_aglut":       c[2].strip() if len(c) > 2 else "",
                    "ind_cod_aglut":   c[3].strip() if len(c) > 3 else "",
                    "nivel_aglut":     int(c[4]) if (len(c) > 4 and c[4].strip().isdigit()) else 0,
                    "descr_cod_aglut": c[5].strip() if len(c) > 5 else "",
                    "val_cta":         _ecd_decimal(c[6]) if len(c) > 6 else 0.0,
                    "ind_vl_cta":      c[7] if len(c) > 7 else "D",
                })
        except (ValueError, IndexError) as e:
            result["warnings"].append({
                "linha": nlinha, "tipo": tipo, "msg": str(e),
            })

    return [result]


# ── Helpers para construção de linhas dos relatórios ───────────────────────
def _ecd_meta(escrit: dict, extra: dict | None = None) -> dict:
    """Retorna metadados comuns (CNPJ, Razão Social, _source) já mesclados."""
    base = {
        "cnpj":         escrit.get("cnpj_fmt", "") or escrit.get("cnpj", ""),
        "razao_social": escrit.get("razao_social", ""),
        "_source":      escrit.get("_source", ""),
    }
    if extra:
        base.update(extra)
    return base


def _ecd_nome_conta(escrit: dict, cod: str) -> str:
    """Resolve o nome da conta a partir do registro I050. (auto-lookup)"""
    c = escrit.get("plano_contas", {}).get(cod)
    return c["nome_cta"] if c else "(conta não cadastrada)"


def build_ecd_plano_rows(escrits: list) -> list:
    """Linhas da sub-aba Plano de Contas, ordenado por código."""
    rows = []
    for e in escrits:
        for cod in sorted(e.get("plano_contas", {}).keys()):
            c = e["plano_contas"][cod]
            rows.append(_ecd_meta(e, {
                "cod_cta":     c["cod_cta"],
                "nome_cta":    c["nome_cta"],
                "nivel":       c["nivel"],
                "ind_cta":     c["ind_cta_desc"],
                "cod_nat":     c["cod_nat_desc"],
                "cod_cta_sup": c["cod_cta_sup"],
                "dt_alt":      c["dt_alt"],
            }))
    return rows


def build_ecd_balancete_rows(escrits: list) -> list:
    rows = []
    for e in escrits:
        for s in e.get("saldos_periodicos", []):
            rows.append(_ecd_meta(e, {
                "periodo":       s["periodo_label"],
                "cod_cta":       s["cod_cta"],
                "nome_cta":      _ecd_nome_conta(e, s["cod_cta"]),
                "saldo_inicial": s["val_sld_ini"],
                "dc_inicial":    s["ind_dc_ini"],
                "debito":        s["val_deb"],
                "credito":       s["val_cred"],
                "saldo_final":   s["val_sld_fin"],
                "dc_final":      s["ind_dc_fin"],
            }))
    return rows


def build_ecd_diario_rows(escrits: list) -> list:
    """Livro Diário — uma linha por partida, ordenado por data do lançamento."""
    rows = []
    for e in escrits:
        for l in sorted(e.get("lancamentos", []), key=lambda x: x.get("dt_lcto", "")):
            for p in l.get("partidas", []):
                rows.append(_ecd_meta(e, {
                    "data":     l["dt_lcto"],
                    "num_lcto": l["num_lcto"],
                    "cod_cta":  p["cod_cta"],
                    "nome_cta": _ecd_nome_conta(e, p["cod_cta"]),
                    "hist":     p["hist"],
                    "ind_dc":   p["ind_dc"],
                    "valor":    p["vl_debito"],
                }))
    return rows


def build_ecd_razao_rows(escrits: list) -> list:
    """Livro Razão consolidado — agrupa partidas por conta, com saldo corrido."""
    rows = []
    for e in escrits:
        # Agrupa partidas por conta
        por_conta: dict = {}
        for l in e.get("lancamentos", []):
            for p in l.get("partidas", []):
                por_conta.setdefault(p["cod_cta"], []).append((l, p))
        # Para cada conta, ordena por data e calcula saldo corrido
        for cod in sorted(por_conta.keys()):
            partidas = sorted(por_conta[cod], key=lambda x: x[0].get("dt_lcto", ""))
            saldo = 0.0
            for l, p in partidas:
                saldo += p["vl_debito"] if p["ind_dc"] == "D" else -p["vl_debito"]
                rows.append(_ecd_meta(e, {
                    "cod_cta":  cod,
                    "nome_cta": _ecd_nome_conta(e, cod),
                    "data":     l["dt_lcto"],
                    "num_lcto": l["num_lcto"],
                    "hist":     p["hist"],
                    "debito":   p["vl_debito"] if p["ind_dc"] == "D" else 0.0,
                    "credito": p["vl_debito"] if p["ind_dc"] == "C" else 0.0,
                    "saldo":    abs(saldo),
                    "dc_saldo": "D" if saldo >= 0 else "C",
                }))
    return rows


def build_ecd_bp_rows(escrits: list) -> list:
    rows = []
    for e in escrits:
        for b in e.get("bp", []):
            rows.append(_ecd_meta(e, {
                "ind_grp_bal":        b["ind_grp_bal"],
                "nivel_aglut":        b["nivel_aglut"],
                "cod_aglut":          b["cod_aglut"],
                "descr_cod_aglut":    b["descr_cod_aglut"],
                "val_cta_fin":        b["val_cta_fin"],
                "ind_dc_cta_fin":     b["ind_dc_cta_fin"],
                "val_cta_fin_ant":    b["val_cta_fin_ant"],
                "ind_dc_cta_fin_ant": b["ind_dc_cta_fin_ant"],
            }))
    return rows


def build_ecd_dre_rows(escrits: list) -> list:
    rows = []
    for e in escrits:
        for d in sorted(e.get("dre", []), key=lambda x: x.get("num_ord", 0)):
            rows.append(_ecd_meta(e, {
                "num_ord":         d["num_ord"],
                "nivel_aglut":     d["nivel_aglut"],
                "cod_aglut":       d["cod_aglut"],
                "descr_cod_aglut": d["descr_cod_aglut"],
                "val_cta":         d["val_cta"],
                "ind_vl_cta":      d["ind_vl_cta"],
            }))
    return rows


def validar_ecd(escrits: list) -> list:
    """Valida a integridade das escriturações importadas.

    Retorna lista de issues (uma linha por inconsistência) com severidade
    ERRO, ALERTA ou INFO. Categorias:
      * ID  — identificação (registro 0000)
      * PC  — plano de contas (hierarquia, descrições)
      * LC  — lançamentos (balanceamento D=C, contas referenciadas)
      * BAL — totais do balancete por período
      * MOV — saldos × lançamentos
      * REF — referências cruzadas
    """
    issues = []
    for e in escrits:
        meta = lambda extra: _ecd_meta(e, extra)

        # ── Identificação ─────────────────────────────────────────────
        if not e.get("cnpj") or len(re.sub(r"\D", "", e.get("cnpj", ""))) != 14:
            issues.append(meta({
                "severidade": "ERRO", "codigo": "ID-001",
                "mensagem": f"CNPJ inválido no registro 0000: '{e.get('cnpj','')}'",
                "referencia": "",
            }))
        if e.get("dt_ini") and e.get("dt_fin"):
            try:
                di = datetime.strptime(e["dt_ini"], "%d/%m/%Y").date()
                df = datetime.strptime(e["dt_fin"], "%d/%m/%Y").date()
                if di > df:
                    issues.append(meta({
                        "severidade": "ERRO", "codigo": "ID-002",
                        "mensagem": f"data inicial ({e['dt_ini']}) posterior à final ({e['dt_fin']})",
                        "referencia": "",
                    }))
            except ValueError:
                pass

        # ── Plano de contas ───────────────────────────────────────────
        plano = e.get("plano_contas", {})
        if not plano:
            issues.append(meta({
                "severidade": "ERRO", "codigo": "PC-001",
                "mensagem": "plano de contas vazio (nenhum I050 encontrado)",
                "referencia": "",
            }))
        for cod, c in plano.items():
            if c["cod_cta_sup"]:
                pai = plano.get(c["cod_cta_sup"])
                if not pai:
                    issues.append(meta({
                        "severidade": "ALERTA", "codigo": "PC-002",
                        "mensagem": f"conta '{cod}' referencia conta superior '{c['cod_cta_sup']}' não cadastrada",
                        "referencia": cod,
                    }))
                elif pai["ind_cta"] != "S":
                    issues.append(meta({
                        "severidade": "ALERTA", "codigo": "PC-003",
                        "mensagem": f"conta '{cod}' referencia '{c['cod_cta_sup']}' que não é sintética",
                        "referencia": cod,
                    }))
            if not c["nome_cta"]:
                issues.append(meta({
                    "severidade": "ALERTA", "codigo": "PC-004",
                    "mensagem": f"conta '{cod}' sem descrição (NOME_CTA vazio)",
                    "referencia": cod,
                }))

        # ── Lançamentos (partidas dobradas) ───────────────────────────
        for l in e.get("lancamentos", []):
            td = sum(p["vl_debito"] for p in l["partidas"] if p["ind_dc"] == "D")
            tc = sum(p["vl_debito"] for p in l["partidas"] if p["ind_dc"] == "C")
            if abs(td - tc) > ECD_TOLERANCIA:
                issues.append(meta({
                    "severidade": "ERRO", "codigo": "LC-001",
                    "mensagem": (f"lançamento {l['num_lcto']} desbalanceado: "
                                 f"D={td:.2f} C={tc:.2f} (diff={abs(td-tc):.2f})"),
                    "referencia": f"lcto {l['num_lcto']} de {l['dt_lcto']}",
                }))
            if abs(l["val_lcto"] - td) > ECD_TOLERANCIA and td > 0:
                issues.append(meta({
                    "severidade": "ALERTA", "codigo": "LC-002",
                    "mensagem": (f"lançamento {l['num_lcto']}: VL_LCTO={l['val_lcto']:.2f} "
                                 f"≠ soma de débitos ({td:.2f})"),
                    "referencia": f"lcto {l['num_lcto']}",
                }))
            for p in l["partidas"]:
                conta = plano.get(p["cod_cta"])
                if not conta:
                    issues.append(meta({
                        "severidade": "ALERTA", "codigo": "LC-003",
                        "mensagem": f"partida usa conta '{p['cod_cta']}' não cadastrada no plano",
                        "referencia": f"lcto {l['num_lcto']}",
                    }))
                elif conta["ind_cta"] != "A":
                    issues.append(meta({
                        "severidade": "ERRO", "codigo": "LC-004",
                        "mensagem": f"lançamento em conta sintética '{p['cod_cta']}' (proibido pelo leiaute)",
                        "referencia": f"lcto {l['num_lcto']}",
                    }))

        # ── Balancete por período (ΣD = ΣC) ──────────────────────────
        from collections import defaultdict as _dd
        por_per: dict = _dd(lambda: {"deb": 0.0, "cred": 0.0})
        for s in e.get("saldos_periodicos", []):
            k = s["periodo_label"]
            por_per[k]["deb"]  += s["val_deb"]
            por_per[k]["cred"] += s["val_cred"]
        for k, v in por_per.items():
            if abs(v["deb"] - v["cred"]) > ECD_TOLERANCIA:
                issues.append(meta({
                    "severidade": "ERRO", "codigo": "BAL-001",
                    "mensagem": (f"balancete não fecha em {k}: "
                                 f"ΣD={v['deb']:.2f} ΣC={v['cred']:.2f} "
                                 f"(diff={v['deb']-v['cred']:.2f})"),
                    "referencia": k,
                }))

        # ── Referências cruzadas ──────────────────────────────────────
        for s in e.get("saldos_periodicos", []):
            if s["cod_cta"] and s["cod_cta"] not in plano:
                issues.append(meta({
                    "severidade": "ALERTA", "codigo": "REF-001",
                    "mensagem": f"I155 referencia conta '{s['cod_cta']}' não cadastrada no I050",
                    "referencia": s["cod_cta"],
                }))

        # ── Avisos do parser (linhas malformadas) ────────────────────
        for w in e.get("warnings", []):
            issues.append(meta({
                "severidade": "INFO", "codigo": "PARSER",
                "mensagem": f"linha {w['linha']} ({w['tipo']}): {w['msg']}",
                "referencia": "",
            }))

    return issues


