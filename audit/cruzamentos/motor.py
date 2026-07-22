"""Base comum dos cruzamentos CR (M2): carga de fatos e chave de conciliação.

Semântica portada dos motores do v5 (run_confronto_efd_dctf e
run_triplo_dctf_darf_dcomp):
- chave de conciliação = (CNPJ, código-base, competência canônica);
- código-base = 4 dígitos (ou pseudo-código 'SIMPLES-<TRIB>'). O v5 preservava
  o sub-código na chave, mas EFD e DARFs antigos não o carregam — casar pela
  base e somar os dois lados evita falsos "só de um lado";
- tolerância de R$ 0,05 em toda comparação.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict

TOL = 0.05

_RE_BASE4 = re.compile(r"\d{4}")


def codigo_base(codigo: str) -> str:
    """'6912-01' → '6912' | 'SIMPLES-COFINS' → 'SIMPLES-COFINS' | '' → ''."""
    s = str(codigo or "").strip()
    if s.upper().startswith("SIMPLES-"):
        return s.upper()
    m = _RE_BASE4.search(s)
    return m.group(0) if m else s


def carregar_lado(con: sqlite3.Connection, fonte: str, natureza: str) -> dict:
    """Fatos de uma (fonte, natureza) agrupados pela chave de conciliação.

    Retorna {(cnpj, codigo_base, competencia): {"valor": soma, "fatos": [rows]}}.
    Cada row tem os campos da tabela + detalhes já desserializado.
    """
    grupos: dict = defaultdict(lambda: {"valor": 0.0, "fatos": []})
    cur = con.execute(
        "SELECT * FROM fatos WHERE fonte=? AND natureza=?", (fonte, natureza))
    for r in cur:
        row = dict(r)
        row["detalhes"] = json.loads(row.get("detalhes") or "{}")
        chave = (row["cnpj"], codigo_base(row["codigo_receita"]), row["competencia"])
        if not all(chave[i] for i in (0, 2)):
            continue
        grupos[chave]["valor"] += row["valor"]
        grupos[chave]["fatos"].append(row)
    return dict(grupos)


def soma_detalhe(grupo: dict | None, campo: str) -> float:
    """Soma um campo numérico de detalhes em todos os fatos do grupo."""
    if not grupo:
        return 0.0
    return sum(float(f["detalhes"].get(campo, 0) or 0) for f in grupo["fatos"])


def brl(v: float) -> str:
    """1234.5 → '1.234,50' (formato do v5 para observações)."""
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
