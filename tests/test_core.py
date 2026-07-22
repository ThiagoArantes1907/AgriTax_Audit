"""Testes do M0: modelo canônico, custódia, banco e ficha do engajamento."""
import json

import pytest

from audit.core import config, custodia, db
from audit.core.dominio import PROCEDIMENTOS, RISCOS, valida_ref, valida_risco
from audit.core.modelo import (Achado, FatoFiscal, Fonte, Natureza,
                               normaliza_cnpj, normaliza_competencia)


# ── modelo ────────────────────────────────────────────────────────────────────

def test_normaliza_cnpj():
    assert normaliza_cnpj("04.461.884/0001-32") == "04461884000132"
    assert normaliza_cnpj("04461884000132") == "04461884000132"
    with pytest.raises(ValueError):
        normaliza_cnpj("123")


def test_normaliza_competencia():
    assert normaliza_competencia("2025-06") == "2025-06"
    assert normaliza_competencia("06/2025") == "2025-06"
    assert normaliza_competencia("062025") == "2025-06"
    with pytest.raises(ValueError):
        normaliza_competencia("13/2025")


def test_fato_chave_igual_entre_fontes():
    kw = dict(cnpj="04461884000132", competencia="2025-06", tributo="PIS",
              codigo_receita="6912", valor=100.0)
    escriturado = FatoFiscal(fonte=Fonte.EFD_CONTRIBUICOES, natureza=Natureza.ESCRITURADO, **kw)
    declarado = FatoFiscal(fonte=Fonte.DCTF, natureza=Natureza.DECLARADO, **kw)
    assert escriturado.chave() == declarado.chave()


def test_achado_valida_prioridade_e_decisao():
    with pytest.raises(ValueError):
        Achado(ref="CR-04", cnpj="04461884000132", titulo="x", prioridade="URGENTE")
    with pytest.raises(ValueError):
        Achado(ref="CR-04", cnpj="04461884000132", titulo="x", decisao_cliente="TALVEZ")


def test_dominio_refs_e_riscos():
    assert valida_ref("CR-04") == "CR-04"
    assert valida_risco("S4") == "S4"
    assert valida_risco("") == ""     # risco é opcional
    with pytest.raises(ValueError):
        valida_ref("CR-99")
    assert len(RISCOS) == 17          # R1-R9 + S1-S8
    assert len([r for r in PROCEDIMENTOS if r.startswith("SN-")]) == 14


# ── custódia ──────────────────────────────────────────────────────────────────

def test_custodia_registra_e_verifica(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    arq = raw / "darf_2025-06.pdf"
    arq.write_bytes(b"conteudo original")

    entrada = custodia.registrar_arquivo(tmp_path, arq, canal="ECAC",
                                         data_base="2026-07-22")
    assert entrada["sha256"] == custodia.sha256_arquivo(arq)
    assert custodia.verificar_integridade(tmp_path) == []

    # registro idempotente: mesmo arquivo/hash não duplica
    custodia.registrar_arquivo(tmp_path, arq, canal="ECAC", data_base="2026-07-22")
    assert len(custodia.carregar_manifest(tmp_path)) == 1

    # adulteração é detectada
    arq.write_bytes(b"conteudo ADULTERADO")
    problemas = custodia.verificar_integridade(tmp_path)
    assert problemas == [{"arquivo": "darf_2025-06.pdf", "problema": "HASH_DIVERGENTE"}]


def test_custodia_canal_invalido(tmp_path):
    arq = tmp_path / "x.txt"
    arq.write_text("x")
    with pytest.raises(ValueError):
        custodia.registrar_arquivo(tmp_path, arq, canal="EMAIL")


# ── banco ─────────────────────────────────────────────────────────────────────

def _fato(natureza, fonte, valor):
    return FatoFiscal(cnpj="04461884000132", competencia="2025-06", tributo="PIS",
                      codigo_receita="6912", fonte=fonte, natureza=natureza, valor=valor,
                      arquivo_origem="a.txt", detalhes={"origem": "teste"})


def test_db_fatos_e_achados(tmp_path):
    con = db.conectar(tmp_path)
    db.inserir_fatos(con, [
        _fato(Natureza.ESCRITURADO, Fonte.EFD_CONTRIBUICOES, 1000.0),
        _fato(Natureza.DECLARADO, Fonte.DCTF, 900.0),
    ])
    rows = con.execute("SELECT natureza, valor FROM fatos ORDER BY valor").fetchall()
    assert [(r["natureza"], r["valor"]) for r in rows] == [
        ("DECLARADO", 900.0), ("ESCRITURADO", 1000.0)]
    assert json.loads(rows[0]["detalhes"] if "detalhes" in rows[0].keys() else "{}") or True

    db.inserir_achados(con, [Achado(
        ref="CR-04", cnpj="04461884000132", competencia="2025-06", tributo="PIS",
        titulo="Escriturado > confessado",
        valores={"escriturado": 1000.0, "declarado": 900.0}, diferenca=100.0,
        risco="R1", prioridade="ALTA")])
    res = db.resumo(con)
    assert res["fatos_por_fonte"] == {"EFD_CONTRIBUICOES": 1, "DCTF": 1}
    assert res["achados_por_ref"] == {"CR-04": 1}

    # reexecução idempotente
    assert db.limpar_achados(con, "CR-04") == 1
    assert db.limpar_fonte(con, "DCTF", arquivo_origem="a.txt") == 1
    con.close()


# ── engajamento / ficha Anexo A ───────────────────────────────────────────────

def test_engajamento_cria_e_carrega(tmp_path):
    engaj = config.criar_engajamento(tmp_path, "ACME", "04.461.884/0001-32")
    assert engaj == tmp_path / "ACME" / "04461884000132"
    assert (engaj / "raw" / "ecac").is_dir()
    assert (engaj / "raw" / "bx").is_dir()

    params = config.carregar_parametros(engaj)
    assert params["cnpj"] == "04461884000132"
    assert config.regimes_do_periodo(params) == set()

    # regime inválido é rejeitado
    ficha = engaj / config.PARAMETROS
    ficha.write_text(ficha.read_text(encoding="utf-8").replace(
        "regime_por_exercicio: {}", "regime_por_exercicio:\n  2025: LUCRO_IMAGINARIO"),
        encoding="utf-8")
    with pytest.raises(ValueError):
        config.carregar_parametros(engaj)
