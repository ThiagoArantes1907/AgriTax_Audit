"""Fase `reperformar`: seleciona o módulo pelo regime (P6 do PT-AF-003).

M5: módulo SN (Simples) — SN-01/02/04/11 a partir do PGDAS-D.
RP (Real/Presumido) chega no M6; quando houver mudança de regime no período,
cada módulo roda sobre seus próprios fatos (PGDAS-D só existe nos anos de
Simples, EFD/ECF nos demais) — a segmentação é natural por fonte.
"""
from __future__ import annotations

import json
from pathlib import Path

from audit.core import config, db

from .sn import checks
from .sn.base import SUBLIMITE_PADRAO


def reperformar_engajamento(engaj_dir: str | Path) -> dict:
    engaj_dir = Path(engaj_dir)
    try:
        params = config.carregar_parametros(engaj_dir)
    except (FileNotFoundError, ValueError):
        params = {}
    sublimite = float(((params.get("simples") or {})
                       .get("sublimite_estadual")) or SUBLIMITE_PADRAO)

    con = db.conectar(engaj_dir)
    resumo: dict = {}
    try:
        apuracoes, por_ref = checks.run_todos(con, sublimite=sublimite)
        if not apuracoes:
            resumo["SN"] = "sem apurações PGDAS-D no banco (regime não-Simples ou " \
                           "extratos ainda não estruturados)"
        else:
            saida = engaj_dir / "achados"
            saida.mkdir(exist_ok=True)
            (saida / "SN_apuracoes.json").write_text(
                json.dumps(apuracoes, ensure_ascii=False, indent=1), encoding="utf-8")
            for ref, achados in por_ref.items():
                db.limpar_achados(con, ref)
                db.inserir_achados(con, achados)
                resumo[ref] = len(achados)
            resumo["apuracoes"] = len(apuracoes)
    finally:
        con.close()
    return resumo
