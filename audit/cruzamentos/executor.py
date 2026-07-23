"""Fase `cruzar` do pipeline: roda os CR implementados e grava os achados.

Os resultados completos (inclusive linhas conformes) são salvos em
achados/<ref>.json — insumo da matriz decisória (M7). No banco ficam só os
achados (divergências), com reexecução idempotente por ref.
"""
from __future__ import annotations

import json
from pathlib import Path

from audit.core import db

from . import cr01, cr04, cr05, cr06, cr08


def _status_map(engaj_dir: Path) -> dict:
    """Procura planilhas de situação de PER/DCOMP do e-CAC no raw/ (.xlsx)."""
    from audit.parsers.perdcomp import parse_status_excel
    status: dict = {}
    for xlsx in sorted((engaj_dir / "raw").rglob("*.xlsx")):
        try:
            status.update(parse_status_excel(str(xlsx)))
        except Exception:
            continue  # xlsx que não é planilha de status
    return status


def cruzar_engajamento(engaj_dir: str | Path) -> dict:
    engaj_dir = Path(engaj_dir)
    con = db.conectar(engaj_dir)
    resumo = {}
    try:
        status = _status_map(engaj_dir)
        execucoes = {
            "CR-01": lambda: cr01.run(engaj_dir),
            "CR-04": lambda: cr04.run(con),
            "CR-05": lambda: cr05.run(con, status_map=status),
            "CR-06": lambda: cr06.run(con, status_map=status),
            "CR-08": lambda: cr08.run(con),
        }
        saida = engaj_dir / "achados"
        saida.mkdir(exist_ok=True)
        for ref, roda in execucoes.items():
            linhas, achados = roda()
            db.limpar_achados(con, ref)
            db.inserir_achados(con, achados)
            (saida / f"{ref}.json").write_text(
                json.dumps(linhas, ensure_ascii=False, indent=1), encoding="utf-8")
            por_situacao: dict = {}
            for ln in linhas:
                por_situacao[ln["situacao"]] = por_situacao.get(ln["situacao"], 0) + 1
            resumo[ref] = {"linhas": len(linhas), "achados": len(achados),
                           "por_situacao": por_situacao}
        if status:
            resumo["status_perdcomp"] = len(status)
    finally:
        con.close()
    return resumo
