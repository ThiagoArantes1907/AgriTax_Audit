"""Procedimentos SN do eixo A e SN-11 (M5) sobre as apurações do PGDAS-D.

SN-01  RBT12 × limite de R$ 4,8 mi (LC 123, art. 3º): estouro ≤ 20% produz
       efeitos no ano seguinte; > 20% exclui no mês seguinte ao excesso.
       Também recalcula a RBT12 rolling quando a série de 12 meses está
       completa e aponta divergência com o RBT12 informado no extrato.
SN-02  Sublimite estadual (R$ 3,6 mi, art. 19/20): acima dele ICMS/ISS saem
       do DAS — débito de ICMS/ISS no PGDAS-D após o estouro é achado.
SN-04  Fator "r" (art. 18, §5º-J): r ≥ 28% → Anexo III; r < 28% → Anexo V.
       Anexo em desacordo com o fator r declarado = DAS a maior (restituição)
       ou a menor (autuação).
SN-11  DAS declarado × pago por competência: débito sem pagamento (risco de
       exclusão por débitos) e pagamento acima do declarado (restituição).
"""
from __future__ import annotations

import sqlite3

from audit.core.modelo import Achado
from audit.cruzamentos.motor import TOL, brl

from .base import (FATOR_R_MINIMO, LIMITE_SIMPLES, SUBLIMITE_PADRAO,
                   carregar_apuracoes, rbt12_recalculada)

BASE_LC123 = "LC 123/2006, art. 3º; Resolução CGSN 140/2018"


def run_sn01(apuracoes: list[dict]) -> list[Achado]:
    achados = []
    for i, ap in enumerate(apuracoes):
        rbt12 = ap["rbt12"]
        if rbt12 > LIMITE_SIMPLES:
            excesso = rbt12 - LIMITE_SIMPLES
            pct = excesso / LIMITE_SIMPLES
            acima_20 = pct > 0.20
            achados.append(Achado(
                ref="SN-01", cnpj=ap["cnpj"], competencia=ap["competencia"],
                tributo="SIMPLES",
                titulo=f"RBT12 acima do limite em {ap['competencia']}",
                descricao=(f"RBT12 R$ {brl(rbt12)} > limite R$ {brl(LIMITE_SIMPLES)} "
                           f"(excesso R$ {brl(excesso)}, {pct:.1%}). "
                           + ("Excesso > 20%: exclusão no MÊS SEGUINTE ao estouro."
                              if acima_20 else
                              "Excesso ≤ 20%: efeitos a partir do ano seguinte.")),
                valores={"rbt12": rbt12, "limite": LIMITE_SIMPLES, "excesso": excesso},
                diferenca=excesso, risco="S1", base_legal=BASE_LC123,
                acao_proposta=("Verificar comunicação de exclusão/migração de regime "
                               "no prazo — sem comunicação, exclusão de ofício "
                               "retroativa com recálculo integral"),
                prioridade="ALTA"))

        recalc = rbt12_recalculada(apuracoes, i)
        if recalc is not None and abs(recalc - rbt12) > 1.00:
            achados.append(Achado(
                ref="SN-01", cnpj=ap["cnpj"], competencia=ap["competencia"],
                tributo="SIMPLES",
                titulo=f"RBT12 informada difere da recalculada em {ap['competencia']}",
                descricao=(f"Extrato informa RBT12 R$ {brl(rbt12)}; soma das RPAs dos "
                           f"12 meses anteriores = R$ {brl(recalc)} "
                           f"(Δ R$ {brl(abs(recalc - rbt12))})."),
                valores={"rbt12_informada": rbt12, "rbt12_recalculada": recalc},
                diferenca=round(recalc - rbt12, 2), risco="", base_legal=BASE_LC123,
                acao_proposta=("Conferir receitas por competência — RBT12 errada "
                               "distorce alíquota efetiva e enquadramento"),
                prioridade="MEDIA"))
    return achados


def run_sn02(apuracoes: list[dict], sublimite: float = SUBLIMITE_PADRAO) -> list[Achado]:
    achados = []
    for ap in apuracoes:
        if ap["rbt12"] <= sublimite:
            continue
        icms_iss = {t: v for t, v in ap["debitos"].items()
                    if t in ("ICMS", "ISS") and v > TOL}
        if icms_iss:
            total = sum(icms_iss.values())
            achados.append(Achado(
                ref="SN-02", cnpj=ap["cnpj"], competencia=ap["competencia"],
                tributo="/".join(sorted(icms_iss)),
                titulo=f"ICMS/ISS no DAS acima do sublimite em {ap['competencia']}",
                descricao=(f"RBT12 R$ {brl(ap['rbt12'])} > sublimite R$ {brl(sublimite)}, "
                           f"mas o PGDAS-D ainda segrega "
                           + ", ".join(f"{t} R$ {brl(v)}" for t, v in sorted(icms_iss.items()))
                           + ". Acima do sublimite, ICMS/ISS devem ser recolhidos "
                             "fora do DAS."),
                valores=icms_iss, diferenca=round(total, 2),
                risco="S1", base_legal="LC 123/2006, arts. 19-20",
                acao_proposta=("Verificar impedimento no PGDAS-D e apuração de "
                               "ICMS/ISS pelo regime normal desde o estouro"),
                prioridade="ALTA"))
    return achados


def run_sn04(apuracoes: list[dict]) -> list[Achado]:
    achados = []
    for ap in apuracoes:
        r, anexo = ap["fator_r"], ap["anexo"].upper().replace("ANEXO", "").strip()
        if r is None or anexo not in ("III", "V"):
            continue
        esperado = "III" if r >= FATOR_R_MINIMO else "V"
        if anexo == esperado:
            continue
        das_a_maior = anexo == "V"   # deveria ser III (alíquotas menores)
        achados.append(Achado(
            ref="SN-04", cnpj=ap["cnpj"], competencia=ap["competencia"],
            tributo="SIMPLES",
            titulo=f"Anexo {anexo} incompatível com fator r em {ap['competencia']}",
            descricao=(f"Fator r declarado {r:.2%} → Anexo {esperado}, mas a apuração "
                       f"usou Anexo {anexo}. "
                       + ("DAS recolhido A MAIOR → restituição/compensação."
                          if das_a_maior else
                          "DAS recolhido A MENOR → diferença com multa.")),
            valores={"fator_r": r, "total_debito": ap["total_debito"]},
            risco="S2", base_legal="LC 123/2006, art. 18, §§5º-I/J/M",
            acao_proposta=("Retificar PGDAS-D e pedir restituição eletrônica"
                           if das_a_maior else
                           "Retificar PGDAS-D e recolher a diferença (denúncia "
                           "espontânea, CTN art. 138)"),
            prioridade="ALTA"))
    return achados


def run_sn11(apuracoes: list[dict]) -> list[Achado]:
    achados = []
    for ap in apuracoes:
        declarado = ap["total_debito"] or sum(ap["debitos"].values())
        pago = ap["das_pago"]
        if declarado <= TOL and pago <= TOL:
            continue
        saldo = round(declarado - pago, 2)
        if abs(saldo) <= TOL:
            continue
        em_aberto = saldo > 0
        achados.append(Achado(
            ref="SN-11", cnpj=ap["cnpj"], competencia=ap["competencia"],
            tributo="SIMPLES",
            titulo=(f"DAS {'em aberto' if em_aberto else 'pago a maior'} "
                    f"em {ap['competencia']}"),
            descricao=(f"Declarado no PGDAS-D R$ {brl(declarado)} × DAS pago "
                       f"R$ {brl(pago)} (saldo R$ {brl(saldo)})."),
            valores={"declarado": declarado, "pago": pago}, diferenca=saldo,
            risco="S3" if em_aberto else "R7",
            base_legal="LC 123/2006, art. 17, V (débitos); art. 21 (restituição)",
            acao_proposta=("Pagar/parcelar — débito em aberto no Simples é causa de "
                           "exclusão de ofício (Termo de Exclusão via DTE)"
                           if em_aberto else
                           "Avaliar restituição eletrônica do Simples "
                           "(pagamento acima do declarado)"),
            prioridade="ALTA" if em_aberto else "MEDIA"))
    return achados


def run_todos(con: sqlite3.Connection, sublimite: float = SUBLIMITE_PADRAO
              ) -> tuple[list[dict], dict[str, list[Achado]]]:
    """Roda SN-01/02/04/11. Retorna (apurações, {ref: achados})."""
    apuracoes = carregar_apuracoes(con)
    return apuracoes, {
        "SN-01": run_sn01(apuracoes),
        "SN-02": run_sn02(apuracoes, sublimite),
        "SN-04": run_sn04(apuracoes),
        "SN-11": run_sn11(apuracoes),
    }
