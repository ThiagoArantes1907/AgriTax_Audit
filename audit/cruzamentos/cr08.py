"""CR-08 — Retificadoras × versão anterior, por competência.

O que revela (PT-AF-003): retificação sem reflexo na declaração; sequência
errada de retificação (risco R9 — cobrança indevida ou supressão de crédito).

Compara, por (CNPJ, competência) da EFD-Contribuições com mais de uma versão
no raw/, os débitos por código entre a versão ATIVA e a imediatamente
anterior. Diferenças relevantes viram achado: se a retificadora mudou o
débito, a DCTF da competência precisa refletir (o CR-04 mede o estado final;
aqui aparece a HISTÓRIA da mudança e a checagem de sequência).
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict

from audit.core.modelo import Achado

from .motor import TOL, brl, codigo_base

BASE_LEGAL = "IN RFB 2.121/2022 (retificação da EFD); IN RFB 2.005/2021 (DCTF)"


def run(con: sqlite3.Connection) -> tuple[list[dict], list[Achado]]:
    # versões por (cnpj, competência): {arquivo: {"retif": bool, "debitos": {cod: v}}}
    versoes: dict = defaultdict(dict)
    for r in con.execute(
            "SELECT * FROM fatos WHERE fonte='EFD_CONTRIBUICOES' "
            "AND natureza='ESCRITURADO'"):
        det = json.loads(r["detalhes"] or "{}")
        v = versoes[(r["cnpj"], r["competencia"])].setdefault(
            r["arquivo_origem"], {"retif": False, "num_rec": "", "debitos": {}})
        v["retif"] = v["retif"] or bool(det.get("retificadora"))
        v["num_rec"] = v["num_rec"] or det.get("num_rec_anterior", "")
        cod = codigo_base(r["codigo_receita"])
        if cod:
            v["debitos"][cod] = v["debitos"].get(cod, 0.0) + float(
                det.get("debito_apurado", 0) or 0)

    linhas, achados = [], []
    for (cnpj, comp), arquivos in sorted(versoes.items()):
        if len(arquivos) < 2:
            continue
        # ordem de transmissão: originais antes de retificadoras; entre iguais,
        # nome (timestamp embutido nos nomes reais do BX/e-CAC)
        ordenados = sorted(arquivos.items(), key=lambda kv: (kv[1]["retif"], kv[0]))
        anterior_nome, anterior = ordenados[-2]
        ativa_nome, ativa = ordenados[-1]

        difs = {}
        for cod in sorted(set(anterior["debitos"]) | set(ativa["debitos"])):
            d = round(ativa["debitos"].get(cod, 0.0)
                      - anterior["debitos"].get(cod, 0.0), 2)
            if abs(d) > TOL:
                difs[cod] = d
        sequencia_ok = ativa["retif"] or not anterior["retif"]

        linhas.append({
            "cnpj": cnpj, "competencia": comp, "versoes": len(arquivos),
            "arquivo_ativo": ativa_nome, "arquivo_anterior": anterior_nome,
            "ativa_retificadora": ativa["retif"],
            "diferencas_por_codigo": difs,
            "situacao": "Com diferenças" if difs else "Sem diferenças",
        })

        if not difs and sequencia_ok:
            continue
        total = round(sum(difs.values()), 2)
        detalhe = "; ".join(f"{cod}: {'+' if d > 0 else '−'}R$ {brl(abs(d))}"
                            for cod, d in difs.items()) or "sem mudança de débitos"
        alerta_seq = ("" if sequencia_ok else
                      " ATENÇÃO: a versão ativa não é retificadora — sequência "
                      "de transmissão suspeita.")
        achados.append(Achado(
            ref="CR-08", cnpj=cnpj, competencia=comp, tributo="PIS/COFINS",
            titulo=f"Retificação da EFD em {comp} ({len(arquivos)} versões)",
            descricao=(f"Versão ativa '{ativa_nome}' × anterior '{anterior_nome}': "
                       f"{detalhe}.{alerta_seq}"),
            valores={cod: d for cod, d in difs.items()},
            diferenca=total if difs else None,
            risco="R9", base_legal=BASE_LEGAL,
            acao_proposta=("Conferir se DCTF e PER/DCOMP da competência refletem a "
                           "versão retificada (retificação sem reflexo gera cobrança "
                           "indevida ou supressão de crédito)"),
            prioridade="ALTA" if difs and total < 0 else "MEDIA",
        ))
    return linhas, achados
