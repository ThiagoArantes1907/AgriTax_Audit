"""CR-05 — Confissão de dívida × quitação (motor "6 vias" do v5).

Por (CNPJ, código-base, competência):
    CONFISSÃO = DCTF (débito) + DCTFWeb (saldo a pagar) + PGDAS-D (por tributo)
    QUITAÇÃO  = DARF (principal) + DAS (rateado por tributo) + DCOMP ativa (principal)

Somente PRINCIPAL na quitação — multa/juros de mora não fazem parte do débito
declarado (regra do v5 que evita falso "pago a maior").

Saldo = confissão − quitação (tolerância R$ 0,05):
    > 0 → Saldo a Pagar (R2)  |  < 0 → Pago/Compensado a Maior (R7)
    = 0 → Quitado             |  quitação sem confissão → Sem Declaração (R3)

DCOMPs canceladas/retificadas (planilha de status do e-CAC) ficam de fora,
como no v5; só entram pedidos de compensação (não PER puros).
"""
from __future__ import annotations

import re
import sqlite3

from audit.core.modelo import Achado

from .motor import TOL, brl, carregar_lado, dedupe_perdcomp, soma_detalhe

SIT_QUITADO = "Quitado"
SIT_SALDO = "Saldo a Pagar"
SIT_A_MAIOR = "Pago/Compensado a Maior"
SIT_SEM_DECL = "Sem Declaração"

BASE_LEGAL = "IN RFB 2.005/2021; CTN art. 150; LC 123/2006 (Simples)"

_RE_DCOMP = re.compile(r"DCOMP|COMPENSA[CÇ]", re.IGNORECASE)


def run(con: sqlite3.Connection, status_map: dict | None = None
        ) -> tuple[list[dict], list[Achado]]:
    """Retorna (linhas do triplo — todas; achados). status_map = planilha de
    situação do e-CAC ({num_perdcomp: status}), para excluir DCOMP inativa."""
    status_map = status_map or {}

    dctf = carregar_lado(con, "DCTF", "DECLARADO")
    dctfweb = carregar_lado(con, "DCTFWEB", "DECLARADO")
    pgdas = carregar_lado(con, "PGDAS_D", "DECLARADO")
    darf = carregar_lado(con, "DARF", "PAGO")
    das = carregar_lado(con, "DAS", "PAGO")
    dcomp = _dcomps_ativas(dedupe_perdcomp(
        carregar_lado(con, "PERDCOMP", "COMPENSADO")), status_map)

    linhas, achados = [], []
    todas = set(dctf) | set(dctfweb) | set(pgdas) | set(darf) | set(das) | set(dcomp)
    for chave in sorted(todas):
        cnpj, cod, comp = chave
        g_dctf, g_web, g_pgdas = dctf.get(chave), dctfweb.get(chave), pgdas.get(chave)
        g_darf, g_das, g_dcomp = darf.get(chave), das.get(chave), dcomp.get(chave)

        dctf_deb = g_dctf["valor"] if g_dctf else 0.0
        web_deb = soma_detalhe(g_web, "saldo_pagar")
        if g_web and web_deb == 0.0:
            web_deb = g_web["valor"]
        simples_deb = g_pgdas["valor"] if g_pgdas else 0.0
        confissao = dctf_deb + web_deb + simples_deb

        darf_pago = g_darf["valor"] if g_darf else 0.0        # já é principal
        das_pago = g_das["valor"] if g_das else 0.0           # rateado no adapter
        dcomp_comp = soma_detalhe(g_dcomp, "valor_principal") # somente principal
        quitacao = darf_pago + das_pago + dcomp_comp

        tem_confissao = bool(g_dctf or g_web or g_pgdas)
        tem_quitacao = bool(g_darf or g_das or g_dcomp)
        if not tem_confissao and tem_quitacao:
            saldo = -quitacao
            situacao = SIT_SEM_DECL
        else:
            saldo = round(confissao - quitacao, 2)
            if abs(saldo) <= TOL:
                situacao = SIT_QUITADO
            elif saldo > 0:
                situacao = SIT_SALDO
            else:
                situacao = SIT_A_MAIOR

        grupos = [g for g in (g_dctf, g_web, g_pgdas, g_darf, g_das, g_dcomp) if g]
        tributo = next((g["fatos"][0]["tributo"] for g in grupos
                        if g["fatos"][0].get("tributo")), "")

        linhas.append({
            "cnpj": cnpj, "codigo_receita": cod, "competencia": comp,
            "tributo": tributo, "dctf_debito": dctf_deb, "dctfweb_debito": web_deb,
            "simples_debito": simples_deb, "total_declarado": confissao,
            "darf_pago": darf_pago, "das_pago": das_pago,
            "dcomp_compensado": dcomp_comp, "total_quitacao": quitacao,
            "saldo_final": saldo, "situacao": situacao,
        })

        if situacao == SIT_QUITADO:
            continue
        risco, prioridade, acao = _classifica(situacao)
        achados.append(Achado(
            ref="CR-05", cnpj=cnpj, competencia=comp, tributo=tributo,
            titulo=f"{situacao}: {tributo or cod} {comp}",
            descricao=(f"Confissão R$ {brl(confissao)} (DCTF {brl(dctf_deb)} | "
                       f"DCTFWeb {brl(web_deb)} | Simples {brl(simples_deb)}) × "
                       f"quitação R$ {brl(quitacao)} (DARF {brl(darf_pago)} | "
                       f"DAS {brl(das_pago)} | DCOMP {brl(dcomp_comp)}). "
                       f"Saldo R$ {brl(saldo)}. Código {cod}."),
            valores={"declarado": confissao, "pago": darf_pago + das_pago,
                     "compensado": dcomp_comp},
            diferenca=saldo,
            risco=risco, base_legal=BASE_LEGAL, acao_proposta=acao,
            prioridade=prioridade,
        ))

    _ord = {SIT_SALDO: 0, SIT_SEM_DECL: 1, SIT_A_MAIOR: 2, SIT_QUITADO: 3}
    linhas.sort(key=lambda r: (_ord[r["situacao"]], r["cnpj"], r["competencia"],
                               r["codigo_receita"]))
    return linhas, achados


def _dcomps_ativas(grupos: dict, status_map: dict) -> dict:
    """Filtra fatos PERDCOMP: só declarações de compensação ATIVAS
    (não canceladas/retificadas na planilha de status do e-CAC)."""
    from audit.parsers.perdcomp import _is_cancelled, _is_retified
    ativos: dict = {}
    for chave, g in grupos.items():
        fatos = []
        for f in g["fatos"]:
            det = f["detalhes"]
            if not _RE_DCOMP.search(str(det.get("tipo_pedido", ""))):
                continue
            num = str(det.get("numero_perdcomp", "")).strip()
            if _is_cancelled(num, status_map) or _is_retified(num, status_map):
                continue
            fatos.append(f)
        if fatos:
            ativos[chave] = {"valor": sum(f["valor"] for f in fatos), "fatos": fatos}
    return ativos


def _classifica(situacao: str) -> tuple[str, str, str]:
    if situacao == SIT_SALDO:
        return ("R2", "ALTA",
                "Pagar/parcelar o saldo (ou verificar DARF mal alocado antes — "
                "código/período errado geram falso saldo)")
    if situacao == SIT_A_MAIOR:
        return ("R7", "MEDIA",
                "Avaliar restituição/compensação do pagamento a maior "
                "(PER/DCOMP) dentro do prazo decadencial")
    # SIT_SEM_DECL
    return ("R3", "ALTA",
            "Quitação sem confissão: verificar se a declaração da competência "
            "foi entregue (omissão gera multa e trava CND)")
