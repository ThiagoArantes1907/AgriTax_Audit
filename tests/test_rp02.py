"""Testes do RP-02: reperformance da ECF (caminhos de divergência)."""
import pytest

from audit.core import config
from audit.reperformance.rp import checks


def _ecf(p300_15="31496,10", p300_adicional="14997,40", p500_csll="28346,49",
         base_irpj="209973,97", receita_8="2624674,58"):
    return "\n".join([
        "|0000|LECF|0011|40832298000110|EMPRESA P LTDA|4|0|||01102024|31122024|N||0||",
        "|0010||N|5|T|01|000P||C||||2|",
        "|P030|01102024|31122024|T04|",
        f"|P200|4|Receita Bruta Sujeita ao Percentual de 8%|{receita_8}|",
        f"|P300|1|BASE DE CÁLCULO DO IMPOSTO SOBRE O LUCRO PRESUMIDO|{base_irpj}|",
        f"|P300|3|À Alíquota de 15%|{p300_15}|",
        f"|P300|4|Adicional|{p300_adicional}|",
        "|P400|2|Receita Bruta Sujeita ao Percentual de 12%|2624674,58|",
        "|P500|1|BASE DE CÁLCULO DA CSLL|314960,95|",
        f"|P500|2|CSLL Apurada|{p500_csll}|",
        "|9999|11|",
    ]) + "\n"


def _roda(tmp_path, conteudo):
    engaj = config.criar_engajamento(tmp_path, "T", "40.832.298/0001-10")
    (engaj / "raw" / "bx" / "SpedECF-teste.txt").write_text(conteudo, encoding="latin-1")
    return checks.run_rp02(engaj)


def test_rp02_ecf_correta_tudo_conforme(tmp_path):
    linhas, achados = _roda(tmp_path, _ecf())
    assert len(linhas) == 3          # 15%, adicional, CSLL 9%
    assert {l["situacao"] for l in linhas} == {"Conforme"}
    assert achados == []
    # adicional: (209.973,97 − 60.000) × 10% — limite de 3 meses (T04)
    adic = next(l for l in linhas if "Adicional" in l["verificacao"])
    assert adic["esperado"] == round((209973.97 - 60000) * 0.10, 2)


def test_rp02_aliquota_errada_gera_r4(tmp_path):
    linhas, achados = _roda(tmp_path, _ecf(p300_15="20000,00"))
    div = [l for l in linhas if l["situacao"] == "Divergente"]
    assert len(div) == 1 and div[0]["verificacao"] == "IRPJ 15%"
    a = achados[0]
    assert (a.ref, a.risco, a.prioridade) == ("RP-02", "R4", "ALTA")
    assert a.diferenca == round(20000.00 - 209973.97 * 0.15, 2)


def test_rp02_base_menor_que_presuncao(tmp_path):
    # base declarada 100k < presunção mínima 8% × 2.624.674,58 = 209.973,97
    linhas, achados = _roda(tmp_path, _ecf(base_irpj="100000,00",
                                           p300_15="15000,00",
                                           p300_adicional="4000,00"))
    verifs = {l["verificacao"] for l in linhas if l["situacao"] == "Divergente"}
    assert any("presunção mínima" in v for v in verifs)
    assert any("subavaliação" in a.descricao for a in achados)


def test_rp02_trava_30(tmp_path):
    ecf = "\n".join([
        "|0000|LECF|0009|30995994000194|EMPRESA R LTDA|0|0|||01012022|31122022|N||0||",
        "|0010||N|1|T|01|RRRR|||||||",
        "|N030|01012022|31032022|T01|",
        "|N630|1|BASE DE CÁLCULO DO IRPJ|50000,00|",
        # compensou 50k sobre base antes de 100k (máximo seria 30k)
        "|N630|5|(-) Compensação de Prejuízos Fiscais de Períodos Anteriores|50000,00|",
        "|N630|3|À Alíquota de 15%|7500,00|",
        "|9999|7|",
    ]) + "\n"
    engaj = config.criar_engajamento(tmp_path, "T", "30.995.994/0001-94")
    (engaj / "raw" / "bx" / "SpedECF-real.txt").write_text(ecf, encoding="latin-1")
    linhas, achados = checks.run_rp02(engaj)
    trava = [l for l in linhas if "Trava" in l["verificacao"]]
    assert len(trava) == 1 and trava[0]["situacao"] == "Divergente"
    assert trava[0]["esperado"] == 30000.00      # 30% de 100k
    assert any("Trava de 30%" in a.titulo for a in achados)


def test_rp02_duas_versoes_usa_mais_recente(tmp_path):
    engaj = config.criar_engajamento(tmp_path, "T", "40.832.298/0001-10")
    (engaj / "raw" / "bx" / "SpedECF-a.txt").write_text(
        _ecf(p300_15="20000,00"), encoding="latin-1")     # antiga, errada
    (engaj / "raw" / "bx" / "SpedECF-b.txt").write_text(
        _ecf(), encoding="latin-1")                        # retificadora, correta
    linhas, achados = checks.run_rp02(engaj)
    assert {l["situacao"] for l in linhas} == {"Conforme"}
    assert achados == []
