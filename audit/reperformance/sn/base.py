"""Base do módulo Simples Nacional: reconstrói as apurações do PGDAS-D.

Os fatos PGDAS_D/DAS gravados pelo M1 carregam o extrato inteiro em detalhes
(RBT12, RPA, anexo, fator r, total do débito). Aqui eles voltam a ser uma
apuração por (CNPJ, competência) — insumo dos procedimentos SN-01..14.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict

LIMITE_SIMPLES = 4_800_000.00      # LC 123/2006, art. 3º, II
SUBLIMITE_PADRAO = 3_600_000.00    # LC 123/2006, art. 19 (ICMS/ISS)
FATOR_R_MINIMO = 0.28              # LC 123/2006, art. 18, §5º-J (Anexo III × V)


def carregar_apuracoes(con: sqlite3.Connection) -> list[dict]:
    """Uma apuração por (cnpj, competência mensal), ordenada por competência.

    {"cnpj", "competencia", "rbt12", "rpa", "anexo", "fator_r",
     "total_debito", "debitos": {tributo: valor}, "das_pago": soma,
     "num_declaracao"}
    """
    apuracoes: dict = {}
    debitos: dict = defaultdict(dict)
    pagos: dict = defaultdict(float)

    for r in con.execute(
            "SELECT * FROM fatos WHERE fonte='PGDAS_D' AND natureza='DECLARADO'"):
        det = json.loads(r["detalhes"] or "{}")
        chave = (r["cnpj"], r["competencia"])
        debitos[chave][r["tributo"]] = r["valor"]
        if chave not in apuracoes:
            apuracoes[chave] = {
                "cnpj": r["cnpj"], "competencia": r["competencia"],
                "rbt12": float(det.get("rbt12", 0) or 0),
                "rpa": float(det.get("rpa", 0) or 0),
                "anexo": str(det.get("anexo", "")).strip(),
                "fator_r": _fator_r(det.get("fator_r", "")),
                "total_debito": float(det.get("total_debito", 0) or 0),
                "num_declaracao": det.get("num_declaracao", ""),
            }

    for r in con.execute(
            "SELECT cnpj, competencia, SUM(valor) v FROM fatos "
            "WHERE fonte='DAS' AND natureza='PAGO' GROUP BY cnpj, competencia"):
        pagos[(r["cnpj"], r["competencia"])] = float(r["v"] or 0)

    saida = []
    for chave, ap in apuracoes.items():
        ap["debitos"] = debitos.get(chave, {})
        ap["das_pago"] = pagos.get(chave, 0.0)
        saida.append(ap)
    # DAS pago sem apuração correspondente ainda aparece no CR-05 (Sem Declaração)
    saida.sort(key=lambda a: (a["cnpj"], a["competencia"]))
    return saida


def _fator_r(v) -> float | None:
    """'28,00%' / '0,28' / 0.28 → 0.28; vazio → None (extrato sem fator r)."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
    else:
        s = str(v).replace("%", "").strip().replace(".", "").replace(",", ".")
        try:
            f = float(s)
        except ValueError:
            return None
    return f / 100 if f > 1 else f


def rbt12_recalculada(apuracoes: list[dict], indice: int) -> float | None:
    """Soma das RPAs dos 12 meses ANTERIORES à competência (rolling).

    Retorna None quando a série não tem os 12 meses anteriores completos —
    sem série completa o recálculo não é comparável ao RBT12 informado.
    """
    alvo = apuracoes[indice]
    anteriores = _meses_anteriores(alvo["competencia"], 12)
    por_comp = {a["competencia"]: a for a in apuracoes if a["cnpj"] == alvo["cnpj"]}
    soma = 0.0
    for comp in anteriores:
        if comp not in por_comp:
            return None
        soma += por_comp[comp]["rpa"]
    return round(soma, 2)


def _meses_anteriores(comp: str, n: int) -> list[str]:
    """'2026.02' → ['2026.01', '2025.12', ...] (n meses para trás)."""
    ano, mes = int(comp[:4]), int(comp[5:7])
    saida = []
    for _ in range(n):
        mes -= 1
        if mes == 0:
            ano, mes = ano - 1, 12
        saida.append(f"{ano}.{mes:02d}")
    return saida
