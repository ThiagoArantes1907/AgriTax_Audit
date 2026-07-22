"""SQLite por engajamento (decisão D5): fatos, achados e espelho da custódia.

Um arquivo `engajamento.db` por CNPJ. Sem servidor, sem dependência externa —
os cruzamentos CR rodam como consultas sobre a tabela `fatos`.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .modelo import Achado, FatoFiscal

DB_NOME = "engajamento.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fatos (
    id              INTEGER PRIMARY KEY,
    cnpj            TEXT NOT NULL,
    competencia     TEXT NOT NULL,
    tributo         TEXT NOT NULL,
    codigo_receita  TEXT NOT NULL DEFAULT '',
    fonte           TEXT NOT NULL,
    natureza        TEXT NOT NULL,
    valor           REAL NOT NULL,
    arquivo_origem  TEXT NOT NULL DEFAULT '',
    detalhes        TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_fatos_chave
    ON fatos (cnpj, tributo, codigo_receita, competencia);
CREATE INDEX IF NOT EXISTS idx_fatos_fonte ON fatos (fonte, natureza);

CREATE TABLE IF NOT EXISTS achados (
    id              INTEGER PRIMARY KEY,
    ref             TEXT NOT NULL,
    cnpj            TEXT NOT NULL,
    competencia     TEXT NOT NULL DEFAULT '',
    tributo         TEXT NOT NULL DEFAULT '',
    titulo          TEXT NOT NULL,
    descricao       TEXT NOT NULL DEFAULT '',
    valores         TEXT NOT NULL DEFAULT '{}',
    diferenca       REAL,
    risco           TEXT NOT NULL DEFAULT '',
    base_legal      TEXT NOT NULL DEFAULT '',
    acao_proposta   TEXT NOT NULL DEFAULT '',
    prioridade      TEXT NOT NULL DEFAULT 'MEDIA',
    decadencia      TEXT NOT NULL DEFAULT '',
    decisao_cliente TEXT NOT NULL DEFAULT 'PENDENTE',
    justificativa   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_achados_ref ON achados (ref, cnpj, competencia);
"""


def conectar(engaj_dir: str | Path) -> sqlite3.Connection:
    """Abre (criando se preciso) o banco do engajamento, com o schema aplicado."""
    path = Path(engaj_dir) / DB_NOME
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    return con


def inserir_fatos(con: sqlite3.Connection, fatos: list[FatoFiscal]) -> int:
    con.executemany(
        "INSERT INTO fatos (cnpj, competencia, tributo, codigo_receita, fonte,"
        " natureza, valor, arquivo_origem, detalhes) VALUES (?,?,?,?,?,?,?,?,?)",
        [f.to_row() for f in fatos])
    con.commit()
    return len(fatos)


def limpar_fonte(con: sqlite3.Connection, fonte: str, arquivo_origem: str = "") -> int:
    """Remove fatos de uma fonte (reimportação idempotente por arquivo ou total)."""
    if arquivo_origem:
        cur = con.execute("DELETE FROM fatos WHERE fonte=? AND arquivo_origem=?",
                          (fonte, arquivo_origem))
    else:
        cur = con.execute("DELETE FROM fatos WHERE fonte=?", (fonte,))
    con.commit()
    return cur.rowcount


def inserir_achados(con: sqlite3.Connection, achados: list[Achado]) -> int:
    con.executemany(
        "INSERT INTO achados (ref, cnpj, competencia, tributo, titulo, descricao,"
        " valores, diferenca, risco, base_legal, acao_proposta, prioridade,"
        " decadencia, decisao_cliente, justificativa)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [a.to_row() for a in achados])
    con.commit()
    return len(achados)


def limpar_achados(con: sqlite3.Connection, ref: str) -> int:
    """Reexecução idempotente de um procedimento: apaga os achados daquele ref."""
    cur = con.execute("DELETE FROM achados WHERE ref=?", (ref,))
    con.commit()
    return cur.rowcount


def resumo(con: sqlite3.Connection) -> dict:
    fatos = {r["fonte"]: r["n"] for r in con.execute(
        "SELECT fonte, COUNT(*) n FROM fatos GROUP BY fonte")}
    achados = {r["ref"]: r["n"] for r in con.execute(
        "SELECT ref, COUNT(*) n FROM achados GROUP BY ref")}
    return {"fatos_por_fonte": fatos, "achados_por_ref": achados}
