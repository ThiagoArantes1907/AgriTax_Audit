"""Parser de EFD-Contribuições (arquivo SPED .txt — Bloco M e 0000).

Extraído do AgriTax Audit v5 consolidado (agritax_audit_consolidado.py),
sem alterações de lógica — apenas modularização (M1 da arquitetura).
"""
import re
from pathlib import Path

from ._util import format_competencia_teste

EFD_DETAIL_COLS = [
    ("cnpj",                "CNPJ",                       130),
    ("razao_social",        "Razão Social",               210),
    ("periodo",             "Período",                     90),
    ("competencia_teste",   "Competência Teste",          120),
    ("tributo",             "Tributo",                     90),  # PIS / COFINS
    ("codigo_receita",      "Cód. Receita",                90),
    ("descricao_codigo",    "Descrição Cód. Receita",     230),
    ("regime",              "Regime",                     130),  # Cumulativo / Não-Cumul.
    ("base_calculo",        "Base de Cálculo",            140),
    ("aliquota",            "Alíquota (%)",               100),
    ("debito_apurado",      "Débito Apurado",             140),
    ("ajuste_acrescimo",    "Ajustes Acréscimo",          140),
    ("ajuste_reducao",      "Ajustes Redução",            140),
    ("contrib_periodo",     "Contribuição do Período",    150),
    ("ded_credito",         "Deduções (Crédito)",         140),
    ("ded_outras",          "Outras Deduções",            140),
    ("contrib_a_recolher",  "Contribuição a Recolher",    150),
    ("_source",             "Arquivo Origem",             200),
]
EFD_KEYS = [c[0] for c in EFD_DETAIL_COLS]
EFD_MONEY_KEYS = {"base_calculo", "aliquota", "debito_apurado",
                   "ajuste_acrescimo", "ajuste_reducao", "contrib_periodo",
                   "ded_credito", "ded_outras", "contrib_a_recolher"}

EFD_RESUMO_COLS = [
    ("tributo",             "Tributo",                    100),
    ("codigo_receita",      "Cód. Receita",                90),
    ("descricao_codigo",    "Descrição",                  230),
    ("qtd_periodos",        "Qtd. Períodos",              130),
    ("total_base",          "Total Base de Cálculo",      170),
    ("total_debito",        "Total Débito Apurado",       170),
    ("total_deducoes",      "Total Deduções",             140),
    ("total_recolher",      "Total a Recolher",           150),
]
EFD_RESUMO_MONEY = {"total_base", "total_debito", "total_deducoes", "total_recolher"}

# Mapa código → descrição (códigos de receita típicos PIS/COFINS)
EFD_CODIGO_DESC = {
    # PIS
    "8109": "PIS - Faturamento (Cumulativo)",
    "6912": "PIS - Não-Cumulativo (Mercado Interno)",
    "4574": "PIS - Importação",
    "1921": "PIS - Folha de Salários",
    "8496": "PIS - Receitas Financeiras",
    # COFINS
    "2172": "COFINS - Faturamento (Cumulativo)",
    "5856": "COFINS - Não-Cumulativo (Mercado Interno)",
    "5442": "COFINS - Importação",
    "8645": "COFINS - Receitas Financeiras",
    "5960": "COFINS - Combustíveis",
}


def _efd_brl(s: str) -> float:
    """Converte string SPED ('1234,56' ou '0,00') para float."""
    s = (s or "").strip().replace(".", "").replace(",", ".")
    try: return float(s)
    except ValueError: return 0.0


def _efd_periodo(dt_ini: str) -> str:
    """Extrai período MM/AAAA de DT_INI no formato DDMMAAAA."""
    s = (dt_ini or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[2:4]}/{s[4:8]}"
    return ""


def _efd_decode_file(path: str) -> list:
    """Lê o arquivo SPED tentando Latin-1 e UTF-8 (encodings mais comuns)."""
    for enc in ("latin-1", "utf-8", "cp1252"):
        try:
            with open(path, "r", encoding=enc) as f:
                return [ln.rstrip("\r\n") for ln in f]
        except UnicodeDecodeError:
            continue
    # Último recurso: lê como bytes e decodifica com errors='replace'
    with open(path, "rb") as f:
        return [ln.decode("latin-1", errors="replace").rstrip("\r\n")
                for ln in f.readlines()]


def extract_efd_contribuicoes(path: str) -> list:
    """Parser de EFD Contribuições (.txt SPED).

    Retorna lista de dicts, uma linha por (período × código de receita)
    cruzando dados do M205/M210 (PIS) e M605/M610 (COFINS).
    """
    nome = Path(path).name
    lines = _efd_decode_file(path)

    # ── Cabeçalho (registro 0000) ──────────────────────────────────────────
    # Layout oficial da EFD-Contribuições (validado em arquivo real do BX):
    #   |0000|COD_VER|TIPO_ESCRIT|IND_SIT_ESP|NUM_REC_ANTERIOR|DT_INI|DT_FIN|NOME|CNPJ|UF|...
    #     c0    c1        c2          c3            c4           c5     c6    c7   c8
    # TIPO_ESCRIT: 0 = original, 1 = retificadora (NUM_REC_ANTERIOR preenchido).
    # (O v5 lia DT_INI/CNPJ em posições erradas — corrigido aqui, ver M6.)
    cabecalho = {"_source": nome, "cnpj": "", "razao_social": "",
                 "periodo": "", "competencia_teste": "",
                 "retificadora": False, "num_rec_anterior": ""}
    for ln in lines:
        if ln.startswith("|0000|"):
            c = ln.split("|")[1:-1] if ln.endswith("|") else ln.split("|")[1:]
            if len(c) >= 9:
                cabecalho["retificadora"] = c[2].strip() == "1"
                cabecalho["num_rec_anterior"] = c[4].strip()
                dt_ini = c[5]
                cabecalho["periodo"] = _efd_periodo(dt_ini)
                cabecalho["competencia_teste"] = format_competencia_teste(
                    cabecalho["periodo"])
                cabecalho["razao_social"] = c[7][:200]
                cnpj_raw = re.sub(r"\D", "", c[8])
                if len(cnpj_raw) == 14:
                    cabecalho["cnpj"] = (
                        f"{cnpj_raw[0:2]}.{cnpj_raw[2:5]}.{cnpj_raw[5:8]}"
                        f"/{cnpj_raw[8:12]}-{cnpj_raw[12:14]}")
                else:
                    cabecalho["cnpj"] = c[8]
            break

    # ── Indexa M205/M210 (PIS) e M605/M610 (COFINS) por código ─────────────
    # M200 — Consolidação PIS:
    #   |M200|VL_TOT_CONT_NC_PER|VL_TOT_CRED_DESC|VL_TOT_CRED_DESC_ANT|VL_TOT_CONT_NC_DEV|
    #         VL_RET_NC|VL_OUT_DED_NC|VL_CONT_NC_REC|
    #         VL_TOT_CONT_CUM_PER|VL_RET_CUM|VL_OUT_DED_CUM|VL_CONT_CUM_REC|
    # M205 — Detalhamento PIS por código:
    #   |M205|NUM_CAMPO|VL_DEBITO|COD_REC|
    # M210 — Detalhamento PIS Não-Cumulativo:
    #   |M210|COD_CONT|VL_REC_BRT|VL_BC_CONT|VL_AJUS_ACRES_BC_PIS|VL_AJUS_REDUC_BC_PIS|
    #         VL_BC_CONT_AJUS|ALIQ_PIS|QUANT_BC_PIS|ALIQ_PIS_QUANT|VL_CONT_APUR|...
    # (M600/M605/M610 análogos para COFINS)

    debitos = []
    i = 0
    n = len(lines)

    def _split(line):
        """Quebra linha em campos, removendo o '|' inicial e final."""
        parts = line.split("|")
        # |REG|f1|f2|...|fN|  → ['', 'REG', 'f1', ..., 'fN', '']
        return parts[1:-1] if len(parts) >= 2 else []

    # Coleta dados M210 / M610 (regime + base + alíquota) por código
    m210_por_codigo = {}   # PIS — não-cumulativo
    m610_por_codigo = {}   # COFINS — não-cumulativo

    while i < n:
        ln = lines[i]
        f = _split(ln)
        if not f:
            i += 1; continue
        reg = f[0]

        if reg == "M210":
            # PIS Não-Cumulativo
            # f: REG, COD_CONT, VL_REC_BRT, VL_BC_CONT, VL_AJUS_ACRES, VL_AJUS_RED,
            #    VL_BC_CONT_AJUS, ALIQ_PIS, ..., VL_CONT_APUR, ...
            try:
                cod = f[1] if len(f) > 1 else ""
                base    = _efd_brl(f[3]) if len(f) > 3 else 0.0
                acres   = _efd_brl(f[4]) if len(f) > 4 else 0.0
                reduc   = _efd_brl(f[5]) if len(f) > 5 else 0.0
                aliq    = _efd_brl(f[7]) if len(f) > 7 else 0.0
                debito  = _efd_brl(f[10]) if len(f) > 10 else 0.0
                m210_por_codigo[cod] = {
                    "base": base, "aliquota": aliq,
                    "ajus_acres": acres, "ajus_reduc": reduc,
                    "debito": debito,
                }
            except Exception:
                pass

        elif reg == "M610":
            # COFINS Não-Cumulativo (mesmo layout do M210)
            try:
                cod = f[1] if len(f) > 1 else ""
                base    = _efd_brl(f[3]) if len(f) > 3 else 0.0
                acres   = _efd_brl(f[4]) if len(f) > 4 else 0.0
                reduc   = _efd_brl(f[5]) if len(f) > 5 else 0.0
                aliq    = _efd_brl(f[7]) if len(f) > 7 else 0.0
                debito  = _efd_brl(f[10]) if len(f) > 10 else 0.0
                m610_por_codigo[cod] = {
                    "base": base, "aliquota": aliq,
                    "ajus_acres": acres, "ajus_reduc": reduc,
                    "debito": debito,
                }
            except Exception:
                pass

        i += 1

    # Soma de deduções totais do M200 / M600 (rateadas pelos códigos)
    def _parse_m200_m600(lines):
        """Retorna {'pis': dict, 'cofins': dict} com totais de débito/dedução."""
        out = {"pis": {}, "cofins": {}}
        for ln in lines:
            f = _split(ln)
            if not f: continue
            reg = f[0]
            if reg == "M200":
                # Mapeamento simplificado: usamos o total a recolher como referência
                try:
                    out["pis"] = {
                        "tot_nc":    _efd_brl(f[1]) if len(f)>1 else 0.0,
                        "ded_cred":  _efd_brl(f[2]) if len(f)>2 else 0.0,
                        "ded_outras": _efd_brl(f[6]) if len(f)>6 else 0.0,
                        "rec_nc":    _efd_brl(f[7]) if len(f)>7 else 0.0,
                        "tot_cum":   _efd_brl(f[8]) if len(f)>8 else 0.0,
                        "rec_cum":   _efd_brl(f[11]) if len(f)>11 else 0.0,
                    }
                except Exception: pass
            elif reg == "M600":
                try:
                    out["cofins"] = {
                        "tot_nc":    _efd_brl(f[1]) if len(f)>1 else 0.0,
                        "ded_cred":  _efd_brl(f[2]) if len(f)>2 else 0.0,
                        "ded_outras": _efd_brl(f[6]) if len(f)>6 else 0.0,
                        "rec_nc":    _efd_brl(f[7]) if len(f)>7 else 0.0,
                        "tot_cum":   _efd_brl(f[8]) if len(f)>8 else 0.0,
                        "rec_cum":   _efd_brl(f[11]) if len(f)>11 else 0.0,
                    }
                except Exception: pass
        return out

    consolidado = _parse_m200_m600(lines)

    # Agora processa M205 e M605 (detalhamento por código)
    for ln in lines:
        f = _split(ln)
        if not f: continue
        reg = f[0]

        if reg == "M205":
            # Layout oficial (validado em arquivo real do BX):
            # |M205|NUM_CAMPO|COD_REC|VL_DEBITO| — o v5 lia código/valor invertidos
            num_campo = f[1] if len(f) > 1 else ""
            cod_rec   = (f[2] if len(f) > 2 else "").strip()
            vl_debito = _efd_brl(f[3]) if len(f) > 3 else 0.0
            if not cod_rec or vl_debito <= 0:
                continue
            # Junta com M210 (mesmo NUM_CAMPO) se houver
            m210 = m210_por_codigo.get(num_campo, {})
            base   = m210.get("base", 0.0)
            aliq   = m210.get("aliquota", 0.0)
            acres  = m210.get("ajus_acres", 0.0)
            reduc  = m210.get("ajus_reduc", 0.0)
            # Regime: cumulativo se NUM_CAMPO == "01" (campo do M200), senão não-cumulativo
            regime = "Cumulativo" if num_campo == "01" else "Não-Cumulativo"
            # Calcula recolher proporcional ao débito apurado
            pis_total = consolidado.get("pis", {})
            tot_deb = pis_total.get("tot_cum", 0) + pis_total.get("tot_nc", 0)
            ded_total = pis_total.get("ded_cred", 0) + pis_total.get("ded_outras", 0)
            rec_total = pis_total.get("rec_cum", 0) + pis_total.get("rec_nc", 0)
            if tot_deb > 0:
                frac = vl_debito / tot_deb
                ded_cred_alloc   = pis_total.get("ded_cred", 0) * frac
                ded_outras_alloc = pis_total.get("ded_outras", 0) * frac
            else:
                ded_cred_alloc = ded_outras_alloc = 0.0
            recolher = vl_debito - ded_cred_alloc - ded_outras_alloc

            debitos.append({
                **cabecalho,
                "tributo":            "PIS",
                "codigo_receita":     cod_rec,
                "descricao_codigo":   EFD_CODIGO_DESC.get(cod_rec, ""),
                "regime":             regime,
                "base_calculo":       base,
                "aliquota":           aliq,
                "debito_apurado":     vl_debito,
                "ajuste_acrescimo":   acres,
                "ajuste_reducao":     reduc,
                "contrib_periodo":    vl_debito + acres - reduc,
                "ded_credito":        round(ded_cred_alloc, 2),
                "ded_outras":         round(ded_outras_alloc, 2),
                "contrib_a_recolher": round(recolher, 2),
            })

        elif reg == "M605":
            # |M605|NUM_CAMPO|COD_REC|VL_DEBITO| (mesma correção do M205)
            num_campo = f[1] if len(f) > 1 else ""
            cod_rec   = (f[2] if len(f) > 2 else "").strip()
            vl_debito = _efd_brl(f[3]) if len(f) > 3 else 0.0
            if not cod_rec or vl_debito <= 0:
                continue
            m610 = m610_por_codigo.get(num_campo, {})
            base   = m610.get("base", 0.0)
            aliq   = m610.get("aliquota", 0.0)
            acres  = m610.get("ajus_acres", 0.0)
            reduc  = m610.get("ajus_reduc", 0.0)
            regime = "Cumulativo" if num_campo == "01" else "Não-Cumulativo"
            cof_total = consolidado.get("cofins", {})
            tot_deb = cof_total.get("tot_cum", 0) + cof_total.get("tot_nc", 0)
            if tot_deb > 0:
                frac = vl_debito / tot_deb
                ded_cred_alloc   = cof_total.get("ded_cred", 0) * frac
                ded_outras_alloc = cof_total.get("ded_outras", 0) * frac
            else:
                ded_cred_alloc = ded_outras_alloc = 0.0
            recolher = vl_debito - ded_cred_alloc - ded_outras_alloc

            debitos.append({
                **cabecalho,
                "tributo":            "COFINS",
                "codigo_receita":     cod_rec,
                "descricao_codigo":   EFD_CODIGO_DESC.get(cod_rec, ""),
                "regime":             regime,
                "base_calculo":       base,
                "aliquota":           aliq,
                "debito_apurado":     vl_debito,
                "ajuste_acrescimo":   acres,
                "ajuste_reducao":     reduc,
                "contrib_periodo":    vl_debito + acres - reduc,
                "ded_credito":        round(ded_cred_alloc, 2),
                "ded_outras":         round(ded_outras_alloc, 2),
                "contrib_a_recolher": round(recolher, 2),
            })

    # Sem detalhamento M205/M605 (Bloco M zerado — ex.: vendas 100% suspensas):
    # emite uma linha-resumo zerada por tributo para preservar no banco a
    # competência coberta e o flag de retificadora (insumos do CB-02 e CR-08).
    if not debitos and cabecalho["cnpj"]:
        for tributo in ("PIS", "COFINS"):
            debitos.append({
                **cabecalho,
                "tributo": tributo, "codigo_receita": "", "descricao_codigo": "",
                "regime": "", "base_calculo": 0.0, "aliquota": 0.0,
                "debito_apurado": 0.0, "ajuste_acrescimo": 0.0,
                "ajuste_reducao": 0.0, "contrib_periodo": 0.0,
                "ded_credito": 0.0, "ded_outras": 0.0, "contrib_a_recolher": 0.0,
                "sem_movimento": True,
            })

    return debitos


def build_efd_resumo(rows: list) -> list:
    """Resumo da EFD por código de receita."""
    if not rows:
        return []
    grupos = {}
    for r in rows:
        key = (r.get("tributo", ""), r.get("codigo_receita", ""))
        if key not in grupos:
            grupos[key] = {
                "tributo": r.get("tributo", ""),
                "codigo_receita": r.get("codigo_receita", ""),
                "descricao_codigo": r.get("descricao_codigo", ""),
                "qtd_periodos": 0,
                "total_base": 0.0,
                "total_debito": 0.0,
                "total_deducoes": 0.0,
                "total_recolher": 0.0,
            }
        g = grupos[key]
        g["qtd_periodos"] += 1
        g["total_base"]     += float(r.get("base_calculo", 0) or 0)
        g["total_debito"]   += float(r.get("debito_apurado", 0) or 0)
        g["total_deducoes"] += (float(r.get("ded_credito", 0) or 0)
                                 + float(r.get("ded_outras", 0) or 0))
        g["total_recolher"] += float(r.get("contrib_a_recolher", 0) or 0)
    return sorted(grupos.values(),
                   key=lambda x: (x["tributo"], x["codigo_receita"]))


