"""Testes do parser de ECF (layout validado em 20 arquivos reais do BX)."""
import pytest

from audit.core import db
from audit.parsers import central, fatos
from audit.parsers.ecf import extract_ecf

ECF_PRESUMIDO = "\n".join([
    "|0000|LECF|0011|40832298000110|EMPRESA PRESUMIDO LTDA|4|0|||01102024|31122024|S|RECIBOANT123|0||",
    "|0001|0|",
    "|0010||N|5|T|01|000P||C||||2|",
    "|0990|4|",
    "|P001|0|",
    "|P030|01102024|31122024|T04|",
    "|P200|4|Receita Bruta Sujeita ao Percentual de 8%|2624674,58|",
    "|P300|1|BASE DE CÁLCULO DO IMPOSTO SOBRE O LUCRO PRESUMIDO|209973,97|",
    "|P300|3|À Alíquota de 15%|31496,10|",
    "|P300|15|IMPOSTO DE RENDA A PAGAR|9525,26|",
    "|P500|1|BASE DE CÁLCULO DA CSLL|314960,95|",
    "|P500|13|CSLL A PAGAR|3700,99|",
    "|P990|8|",
    "|9999|14|",
]) + "\n"

ECF_REAL_TRIMESTRAL = "\n".join([
    "|0000|LECF|0009|30995994000194|EMPRESA REAL LTDA|0|0|||01012022|31122022|N||0||",
    "|0010||N|1|T|01|RRRR|||||||",
    "|N001|0|",
    "|N030|01012022|31032022|T01|",
    "|N630|1|BASE DE CÁLCULO DO IRPJ|97895,74|",
    "|N630|26|IMPOSTO DE RENDA A PAGAR|18473,93|",
    "|N670|1|BASE DE CÁLCULO DA CSLL|97895,74|",
    "|N670|21|CSLL A PAGAR|8810,62|",
    "|N030|01042022|30062022|T02|",
    "|N630|26|IMPOSTO DE RENDA A PAGAR|6064,29|",
    "|N670|21|CSLL A PAGAR|3638,57|",
    "|N990|10|",
    "|9999|12|",
]) + "\n"


def test_ecf_presumido(tmp_path):
    arq = tmp_path / "ecf_pres.txt"
    arq.write_text(ECF_PRESUMIDO, encoding="latin-1")
    rows = extract_ecf(arq)
    por = {(r["periodo_apuracao"], r["tributo"]): r for r in rows}
    irpj = por[("T04", "IRPJ")]
    assert irpj["competencia_teste"] == "2024.4T"
    assert irpj["forma_trib"] == "LUCRO_PRESUMIDO"
    assert irpj["codigo_receita"] == "2089"
    assert irpj["valor_apurado"] == 9525.26          # a pagar, não o bruto de 15%
    assert irpj["base_calculo"] == 209973.97
    assert irpj["retificadora"] is True
    assert irpj["num_rec_anterior"] == "RECIBOANT123"
    csll = por[("T04", "CSLL")]
    assert (csll["codigo_receita"], csll["valor_apurado"]) == ("2372", 3700.99)


def test_ecf_real_trimestral(tmp_path):
    arq = tmp_path / "ecf_real.txt"
    arq.write_text(ECF_REAL_TRIMESTRAL, encoding="latin-1")
    rows = extract_ecf(arq)
    por = {(r["periodo_apuracao"], r["tributo"]): r for r in rows}
    assert por[("T01", "IRPJ")]["codigo_receita"] == "0220"
    assert por[("T01", "IRPJ")]["tipo_apuracao"] == "real_trimestral"
    assert por[("T01", "IRPJ")]["valor_apurado"] == 18473.93
    assert por[("T02", "CSLL")]["competencia_teste"] == "2022.2T"
    assert por[("T02", "CSLL")]["valor_apurado"] == 3638.57
    assert por[("T01", "CSLL")]["retificadora"] is False


def test_ecf_classificador_e_fatos(tmp_path):
    arq = tmp_path / "SpedECF-teste.txt"
    arq.write_text(ECF_PRESUMIDO, encoding="latin-1")
    assert central.identificar_tipo(arq) == "ecf"
    tipo, rows, fs = central.processar_arquivo(arq)
    assert tipo == "ecf" and len(fs) == 2
    f = {x.tributo: x for x in fs}
    assert f["IRPJ"].fonte.value == "ECF"
    assert f["IRPJ"].natureza.value == "ESCRITURADO"
    assert f["IRPJ"].codigo_receita == "2089"
    assert f["IRPJ"].competencia == "2024.4T"
    assert f["IRPJ"].detalhes["retificadora"] is True
    assert f["CSLL"].valor == 3700.99


def test_ecf_alimenta_cr04(tmp_path):
    """IRPJ da ECF × DCTF: com a mesma competência e código, o CR-04 casa."""
    from audit.core.modelo import FatoFiscal, Fonte, Natureza
    from audit.cruzamentos import cr04
    arq = tmp_path / "ecf.txt"
    arq.write_text(ECF_PRESUMIDO, encoding="latin-1")
    _, _, fs = central.processar_arquivo(arq)
    con = db.conectar(tmp_path)
    db.inserir_fatos(con, fs)
    db.inserir_fatos(con, [FatoFiscal(
        cnpj="40832298000110", competencia="2024.4T", tributo="IRPJ",
        fonte=Fonte.DCTF, natureza=Natureza.DECLARADO, valor=9000.00,
        codigo_receita="2089-01", arquivo_origem="dctf.pdf")])
    linhas, achados = cr04.run(con)
    irpj = next(l for l in linhas if l["codigo_receita"] == "2089")
    # ECF não traz debito_apurado em detalhes → cai no fallback (valor apurado)
    assert irpj["efd_debito"] == 9525.26
    assert irpj["situacao"] == cr04.SIT_DIVERG
    assert any(a.risco == "R1" for a in achados)
    con.close()
