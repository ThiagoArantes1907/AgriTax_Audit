"""RP-02 — Reperformance da ECF: reexecutar a apuração de IRPJ/CSLL.

Recálculos por período de apuração, lidos direto das ECFs do raw/:

Presumido (blocos P):
  • base mínima de presunção: Σ (receita da linha "Percentual de X%" × X%)
    do P200 (IRPJ) e P400 (CSLL). Base declarada MENOR que a presunção
    mínima = subavaliação (achado ALTA); maior é normal (ganhos de capital
    e demais receitas somam à base).
  • IRPJ: linha "À Alíquota de 15%" = base × 15%; "Adicional" = 10% sobre o
    que exceder R$ 20.000 × meses do período (art. 3º, Lei 9.249/95).
  • CSLL: "CSLL Apurada" = base × 9%.

Real (blocos N):
  • mesmas conferências de alíquota/adicional sobre N630/N670 (trimestre ou
    ajuste anual);
  • trava de 30% (arts. 15/16, Lei 9.065/95): compensação de prejuízos no
    demonstrativo ≤ 30% da base ANTES da compensação.

A ECF vem do PVA (que calcula sozinho) — divergência aqui indica arquivo
montado fora do validador, versão adulterada ou preenchimento manual errado.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from audit.core.modelo import Achado
from audit.cruzamentos.motor import brl
from audit.parsers.ecf import extract_linhas_demonstrativo

TOL = 0.10
_RE_PERCENTUAL = re.compile(r"PERCENTUAL DE ([\d.,]+)\s*%")

BASE_PRESUMIDO = "Lei 9.249/95, arts. 15, 20 e 3º; RIR/2018 arts. 591-600"
BASE_REAL = "Lei 9.065/95, arts. 15-16 (trava); Lei 9.249/95, art. 3º"


def _norm(s: str) -> str:
    return s.upper().translate(str.maketrans("ÁÀÂÃÉÊÍÓÔÕÚÜÇ", "AAAAEEIOOOUUC")).strip()


def _meses(dt_ini: str, dt_fin: str) -> int:
    if len(dt_ini) == 8 and len(dt_fin) == 8:
        return (int(dt_fin[4:8]) - int(dt_ini[4:8])) * 12 \
            + int(dt_fin[2:4]) - int(dt_ini[2:4]) + 1
    return 3


def run_rp02(engaj_dir: str | Path) -> tuple[list[dict], list[Achado]]:
    from audit.parsers.central import identificar_tipo
    engaj_dir = Path(engaj_dir)
    linhas, achados = [], []
    vistos: set = set()
    for arq in sorted((engaj_dir / "raw").rglob("*.txt"), reverse=True):
        if identificar_tipo(arq) != "ecf":
            continue
        demonstrativos = extract_linhas_demonstrativo(arq)
        if not demonstrativos:
            continue
        chave_arq = (demonstrativos[0]["cnpj"],
                     demonstrativos[0]["competencia_teste"][:4])
        if chave_arq in vistos:   # nome maior = versão mais recente (ordem reversa)
            continue
        vistos.add(chave_arq)
        ls, As = _verifica_ecf(demonstrativos, arq.name)
        linhas += ls
        achados += As
    ordem = {"Divergente": 0, "Conforme": 1}
    linhas.sort(key=lambda r: (ordem.get(r["situacao"], 2), r["cnpj"],
                               r["competencia"], r["verificacao"]))
    return linhas, achados


def _verifica_ecf(demo: list[dict], arquivo: str) -> tuple[list, list]:
    por_periodo: dict = defaultdict(lambda: defaultdict(dict))
    for d in demo:
        por_periodo[(d["periodo_apuracao"], d["competencia_teste"],
                     d["dt_ini"], d["dt_fin"])][d["registro"]][_norm(d["descricao"])] = d["valor"]
    cnpj = demo[0]["cnpj"]
    linhas, achados = [], []

    def confere(comp, verificacao, esperado, declarado, base_legal, contexto=""):
        dif = round(declarado - esperado, 2)
        situacao = "Conforme" if abs(dif) <= TOL else "Divergente"
        linhas.append({"cnpj": cnpj, "competencia": comp,
                       "verificacao": verificacao, "esperado": round(esperado, 2),
                       "declarado": round(declarado, 2), "diferenca": dif,
                       "situacao": situacao, "arquivo": arquivo})
        if situacao == "Divergente":
            achados.append(Achado(
                ref="RP-02", cnpj=cnpj, competencia=comp, tributo="IRPJ/CSLL",
                titulo=f"{verificacao} divergente em {comp}",
                descricao=(f"Recalculado R$ {brl(esperado)} × declarado na ECF "
                           f"R$ {brl(declarado)} (Δ R$ {brl(abs(dif))}). "
                           f"{contexto}Arquivo: {arquivo[:45]}."),
                valores={"escriturado": round(declarado, 2)},
                diferenca=dif, risco="R4", base_legal=base_legal,
                acao_proposta=("Refazer a apuração do período e retificar a ECF "
                               "(e reflexos em DCTF) se confirmado o erro"),
                prioridade="ALTA"))

    for (per, comp, dt_i, dt_f), regs in sorted(por_periodo.items()):
        meses = _meses(dt_i, dt_f)
        limite_adicional = 20_000.0 * meses

        # ── Presumido ────────────────────────────────────────────────────
        if "P300" in regs:
            base = regs["P300"].get("BASE DE CALCULO DO IMPOSTO SOBRE O LUCRO PRESUMIDO")
            presuncao = _presuncao_minima(regs.get("P200", {}))
            if base is not None and presuncao is not None \
                    and base < presuncao - TOL:
                confere(comp, "Base IRPJ ≥ presunção mínima (P200)",
                        presuncao, base, BASE_PRESUMIDO,
                        "Base declarada MENOR que a presunção sobre a receita "
                        "bruta informada — subavaliação. ")
            _confere_irpj(confere, comp, regs["P300"], base, limite_adicional,
                          BASE_PRESUMIDO)
        if "P500" in regs:
            base_c = regs["P500"].get("BASE DE CALCULO DA CSLL")
            presuncao_c = _presuncao_minima(regs.get("P400", {}))
            if base_c is not None and presuncao_c is not None \
                    and base_c < presuncao_c - TOL:
                confere(comp, "Base CSLL ≥ presunção mínima (P400)",
                        presuncao_c, base_c, BASE_PRESUMIDO,
                        "Base declarada MENOR que a presunção. ")
            apurada = regs["P500"].get("CSLL APURADA")
            if base_c is not None and apurada is not None and base_c > 0:
                confere(comp, "CSLL 9% (P500)", base_c * 0.09, apurada,
                        BASE_PRESUMIDO)

        # ── Real ─────────────────────────────────────────────────────────
        if "N630" in regs:
            base_r = regs["N630"].get("BASE DE CALCULO DO IRPJ")
            _confere_irpj(confere, comp, regs["N630"], base_r, limite_adicional,
                          BASE_REAL)
            _confere_trava(confere, comp, regs["N630"], "IRPJ", BASE_REAL)
        if "N670" in regs:
            base_c = regs["N670"].get("BASE DE CALCULO DA CSLL")
            apurada = _primeiro(regs["N670"], ("CSLL APURADA",
                                               "CONTRIBUICAO SOCIAL SOBRE O LUCRO LIQUIDO POR ATIVIDADE"))
            if base_c is not None and apurada is not None and base_c > 0:
                confere(comp, "CSLL 9% (N670)", base_c * 0.09, apurada, BASE_REAL)
            _confere_trava(confere, comp, regs["N670"], "CSLL", BASE_REAL)

    return linhas, achados


def _presuncao_minima(reg: dict) -> float | None:
    """Σ receita × percentual das linhas 'Receita ... Percentual de X%'."""
    total, achou = 0.0, False
    for desc, valor in reg.items():
        m = _RE_PERCENTUAL.search(desc)
        if m and valor:
            perc = float(m.group(1).replace(".", "").replace(",", "."))
            total += valor * perc / 100.0
            achou = True
    return round(total, 2) if achou else None


def _confere_irpj(confere, comp, reg: dict, base, limite_adicional: float,
                  base_legal: str):
    if base is None or base <= 0:
        return
    aliq15 = _primeiro(reg, ("A ALIQUOTA DE 15%",))
    if aliq15 is not None:
        confere(comp, "IRPJ 15%", base * 0.15, aliq15, base_legal)
    adicional = reg.get("ADICIONAL")
    if adicional is not None:
        confere(comp, f"Adicional 10% (> R$ {limite_adicional:,.0f})",
                max(0.0, base - limite_adicional) * 0.10, adicional, base_legal)


def _confere_trava(confere, comp, reg: dict, tributo: str, base_legal: str):
    """Compensação de prejuízos/base negativa ≤ 30% da base antes da compensação."""
    base_apos = _primeiro(reg, ("BASE DE CALCULO DO IRPJ", "BASE DE CALCULO DA CSLL"))
    compensacao = None
    for desc, v in reg.items():
        if "COMPENSA" in desc and ("PREJU" in desc or "BASE DE CALCULO NEGATIVA" in desc):
            compensacao = abs(v or 0.0)
            break
    if not compensacao or base_apos is None:
        return
    base_antes = base_apos + compensacao
    maximo = round(base_antes * 0.30, 2)
    if compensacao > maximo + TOL:
        confere(comp, f"Trava de 30% ({tributo})", maximo, compensacao,
                base_legal,
                f"Compensação de R$ {brl(compensacao)} excede 30% da base "
                f"antes da compensação (R$ {brl(base_antes)}). ")


def _primeiro(reg: dict, chaves: tuple) -> float | None:
    for k in chaves:
        if k in reg:
            return reg[k]
    return None
