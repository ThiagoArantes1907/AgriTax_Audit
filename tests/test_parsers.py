"""Testes do M1: adaptadores → FatoFiscal, classificador e estruturação.

PDFs reais não entram no repo (dados de cliente); os parsers de PDF são a
lógica intacta do v5 (validada em produção na GUI). Aqui se testa o que o M1
introduziu: adaptação para o modelo canônico e a carga end-to-end com SPED
sintético (EFD-Contribuições, formato texto).
"""
import pytest

from audit.core import config, custodia, db
from audit.parsers import central, fatos
from audit.parsers._util import format_competencia_teste, parse_brl

CNPJ = "04.461.884/0001-32"
CNPJ_LIMPO = "04461884000132"


# ── utilidades compartilhadas (extraídas do v5) ───────────────────────────────

def test_format_competencia_teste():
    assert format_competencia_teste("28/02/2026") == "2026.02"
    assert format_competencia_teste("02/2026") == "2026.02"
    assert format_competencia_teste("Fevereiro de 2026") == "2026.02"
    assert format_competencia_teste("1º Trimestre/2024") == "2024.1T"
    assert format_competencia_teste("2024") == "2024"
    assert format_competencia_teste("") == ""


def test_parse_brl():
    assert parse_brl("1.234,56") == 1234.56
    assert parse_brl("0,00") == 0.0


# ── adaptadores rows → FatoFiscal ─────────────────────────────────────────────

def test_fatos_darf():
    rows = [{"cnpj": CNPJ, "tipo_doc": "DARF", "numero_doc": "07.19.66086.6103381-5",
             "periodo": "28/02/2026", "competencia_teste": "2026.02",
             "dt_arrecadacao": "10/03/2026", "codigo": "6912-01",
             "descricao": "PIS NAO CUMULATIVO", "principal": "1.000,00",
             "multa": "10,00", "juros": "5,00", "total_item": "1.015,00",
             "_source": "darf.pdf"}]
    f = fatos.fatos_darf(rows)
    assert len(f) == 1
    assert f[0].cnpj == CNPJ_LIMPO
    assert f[0].natureza.value == "PAGO"
    assert f[0].fonte.value == "DARF"
    assert f[0].valor == 1000.0
    assert f[0].codigo_receita == "6912"
    assert f[0].detalhes["total_item"] == 1015.0


def test_fatos_darf_das_e_linhas_invalidas():
    rows = [{"cnpj": CNPJ, "tipo_doc": "DAS", "codigo": "0001",
             "competencia_teste": "2026.02", "principal": 500.0},
            {"cnpj": "", "codigo": "6912"},        # sem CNPJ
            {"cnpj": CNPJ, "codigo": ""}]           # sem código
    f = fatos.fatos_darf(rows)
    assert len(f) == 1 and f[0].fonte.value == "DAS"


def test_fatos_dctf_e_chave_casa_com_efd():
    dctf_rows = [{"cnpj": CNPJ, "codigo_receita": "6912-01", "grupo_tributo": "PIS",
                  "competencia_teste": "2026.02", "debito_apurado": "1.000,00",
                  "credito_pagamento": "900,00", "saldo_pagar": "100,00",
                  "numero_declaracao": "100", "retificadora": "Não"}]
    efd_rows = [{"cnpj": CNPJ, "codigo_receita": "6912", "tributo": "PIS",
                 "competencia_teste": "2026.02", "contrib_a_recolher": 1000.0,
                 "regime": "Não-Cumulativo", "debito_apurado": 1200.0}]
    fd = fatos.fatos_dctf(dctf_rows)[0]
    fe = fatos.fatos_efd_contribuicoes(efd_rows)[0]
    # base do CR-04: mesmo código-base e competência dos dois lados
    assert (fd.cnpj, fd.codigo_receita, fd.competencia) == \
           (fe.cnpj, fe.codigo_receita, fe.competencia)
    assert fd.natureza.value == "DECLARADO" and fe.natureza.value == "ESCRITURADO"


def test_fatos_simples_segrega_por_tributo():
    rows = [{"cnpj": CNPJ, "competencia": "02/2026", "competencia_teste": "2026.02",
             "anexo": "I", "fator_r": "", "rbt12": "1.200.000,00", "rpa": "100.000,00",
             "pis": "270,00", "cofins": "1.247,00", "icms": "3.400,00",
             "irpj": 0, "csll": 0, "cpp": "4.150,00", "ipi": 0, "iss": 0,
             "das_valor_pago": "9.067,00", "das_numero": "123", "total_debito": "9.067,00"}]
    f = fatos.fatos_simples(rows)
    tributos = {x.tributo: x for x in f}
    assert set(tributos) == {"PIS", "COFINS", "ICMS", "CPP", "SIMPLES_DAS"}
    assert tributos["ICMS"].natureza.value == "DECLARADO"
    assert tributos["ICMS"].fonte.value == "PGDAS_D"
    assert tributos["ICMS"].detalhes["anexo"] == "I"
    assert tributos["SIMPLES_DAS"].natureza.value == "PAGO"
    assert tributos["SIMPLES_DAS"].valor == 9067.0


def test_fatos_perdcomp_debitos_e_creditos():
    rows = [{"tipo_registro": "Crédito", "cnpj": CNPJ, "tipo": "Saldo Negativo",
             "periodo_apuracao": "1º Trimestre/2022", "competencia_teste": "2022.1T",
             "valor_original": "38.590,45", "valor_utilizado": "0,00"},
            {"tipo_registro": "Débito", "cnpj": CNPJ, "tipo": "COFINS",
             "codigo_receita_debito": "5856-01", "competencia_teste": "2026.01",
             "valor_original": "800,00", "valor_multa": "0,00", "valor_juros": "0,00",
             "valor_total": "800,00", "numero_perdcomp": "12345.67890.123456.1.3.02-4560"}]
    f = fatos.fatos_perdcomp(rows)
    por_nat = {x.natureza.value: x for x in f}
    assert set(por_nat) == {"PLEITEADO", "COMPENSADO"}
    assert por_nat["PLEITEADO"].valor == 38590.45
    assert por_nat["PLEITEADO"].competencia == "2022.1T"
    assert por_nat["COMPENSADO"].codigo_receita == "5856"
    assert por_nat["COMPENSADO"].valor == 800.0


def test_fatos_dctfweb():
    rows = [{"cnpj": CNPJ, "codigo_receita": "1082", "grupo_tributo": "CP",
             "categoria": "40", "competencia_teste": "2026.02",
             "debito_apurado": "10.000,00", "cred_compensacao": "2.000,00",
             "saldo_pagar": "8.000,00", "numero_recibo": "R1"}]
    f = fatos.fatos_dctfweb(rows)
    assert len(f) == 1 and f[0].fonte.value == "DCTFWEB"
    assert f[0].detalhes["cred_compensacao"] == 2000.0


# ── EFD sintética + classificador + estruturar end-to-end ─────────────────────

EFD_SINTETICA = "\n".join([
    "|0000|006|0|01022026|28022026|EMPRESA TESTE LTDA|04461884000132|GO||5208707||00|1|",
    "|0001|0|",
    "|M001|0|",
    "|M200|1000,00|0,00|0,00|0,00|0,00|0,00|1000,00|0,00|0,00|0,00|0,00|",
    "|M205|02|1000,00|6912|",
    "|M210|01|100000,00|100000,00|0,00|0,00|100000,00|1,6500|||1000,00|0,00|0,00|1000,00|",
    "|M600|4600,00|0,00|0,00|0,00|0,00|0,00|4600,00|0,00|0,00|0,00|0,00|",
    "|M605|02|4600,00|5856|",
    "|M610|01|100000,00|100000,00|0,00|0,00|100000,00|7,6000|||4600,00|0,00|0,00|4600,00|",
    "|M990|7|",
    "|9999|11|",
]) + "\n"

ECD_CABECALHO = "|0000|LECD|01012025|31122025|EMPRESA TESTE LTDA|04461884000132|GO|123|5208707||0|\n|9999|2|\n"


def _cria_engajamento_com_efd(tmp_path):
    engaj = config.criar_engajamento(tmp_path, "TESTE", CNPJ)
    (engaj / "raw" / "bx" / "efd_022026.txt").write_text(EFD_SINTETICA, encoding="latin-1")
    return engaj


def test_identificar_tipo_sped(tmp_path):
    efd = tmp_path / "efd.txt"
    efd.write_text(EFD_SINTETICA, encoding="latin-1")
    ecd = tmp_path / "ecd.txt"
    ecd.write_text(ECD_CABECALHO, encoding="latin-1")
    outro = tmp_path / "outro.txt"
    outro.write_text("não é SPED", encoding="latin-1")
    assert central.identificar_tipo(efd) == "efd_contribuicoes"
    assert central.identificar_tipo(ecd) == "ecd"
    assert central.identificar_tipo(outro) == "desconhecido"


def test_extract_efd_sintetica(tmp_path):
    arq = tmp_path / "efd.txt"
    arq.write_text(EFD_SINTETICA, encoding="latin-1")
    rows = central.processar_arquivo(arq)[1]
    por_cod = {r["codigo_receita"]: r for r in rows}
    assert set(por_cod) == {"6912", "5856"}
    assert por_cod["6912"]["tributo"] == "PIS"
    assert por_cod["6912"]["competencia_teste"] == "2026.02"
    assert por_cod["5856"]["contrib_a_recolher"] == 4600.0


def test_estruturar_engajamento_end_to_end(tmp_path):
    engaj = _cria_engajamento_com_efd(tmp_path)
    res = central.estruturar_engajamento(engaj, data_base="2026-07-22")
    assert res["erros"] == []
    assert res["fatos_gravados"] == 2   # PIS + COFINS

    # custódia registrada com canal BX
    manifest = custodia.carregar_manifest(engaj)
    assert len(manifest) == 1 and manifest[0]["canal"] == "RECEITANETBX"

    # fatos no banco
    con = db.conectar(engaj)
    r = db.resumo(con)
    assert r["fatos_por_fonte"] == {"EFD_CONTRIBUICOES": 2}

    # reimportação idempotente: rodar de novo não duplica
    res2 = central.estruturar_engajamento(engaj, data_base="2026-07-22")
    assert res2["erros"] == []
    con2 = db.conectar(engaj)
    assert db.resumo(con2)["fatos_por_fonte"] == {"EFD_CONTRIBUICOES": 2}
    con.close(); con2.close()
    assert len(custodia.carregar_manifest(engaj)) == 1
