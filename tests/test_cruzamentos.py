"""Testes do M2: CR-04 (confronto EFD × DCTF/DCTFWeb) e CR-05 (confissão × quitação)."""
import pytest

from audit.core import db
from audit.core.modelo import FatoFiscal, Fonte, Natureza
from audit.cruzamentos import cr04, cr05
from audit.cruzamentos.motor import codigo_base
from audit.parsers import fatos as adapt

CNPJ = "04461884000132"


def _fato(fonte, natureza, valor, cod="6912-01", comp="2026.02", tributo="PIS",
          detalhes=None):
    return FatoFiscal(cnpj=CNPJ, competencia=comp, tributo=tributo, fonte=fonte,
                      natureza=natureza, valor=valor, codigo_receita=cod,
                      arquivo_origem="t", detalhes=detalhes or {})


def test_codigo_base():
    assert codigo_base("6912-01") == "6912"
    assert codigo_base("6912") == "6912"
    assert codigo_base("SIMPLES-COFINS") == "SIMPLES-COFINS"
    assert codigo_base("") == ""


# ── CR-04 ─────────────────────────────────────────────────────────────────────

def test_cr04_conforme_nao_gera_achado(tmp_path):
    con = db.conectar(tmp_path)
    db.inserir_fatos(con, [
        _fato(Fonte.EFD_CONTRIBUICOES, Natureza.ESCRITURADO, 1000.0, cod="6912",
              detalhes={"debito_apurado": 1000.0}),
        _fato(Fonte.DCTF, Natureza.DECLARADO, 1000.0, cod="6912-01"),
    ])
    linhas, achados = cr04.run(con)
    assert len(linhas) == 1                      # sub-código casa com a base
    assert linhas[0]["situacao"] == cr04.SIT_OK
    assert achados == []
    con.close()


def test_cr04_escriturado_maior_gera_r1(tmp_path):
    con = db.conectar(tmp_path)
    db.inserir_fatos(con, [
        _fato(Fonte.EFD_CONTRIBUICOES, Natureza.ESCRITURADO, 0, cod="5856",
              tributo="COFINS", detalhes={"debito_apurado": 4600.0}),
        _fato(Fonte.DCTF, Natureza.DECLARADO, 4000.0, cod="5856-01", tributo="COFINS"),
    ])
    linhas, achados = cr04.run(con)
    assert linhas[0]["situacao"] == cr04.SIT_DIVERG
    assert linhas[0]["diferenca"] == 600.0
    a = achados[0]
    assert (a.ref, a.risco, a.prioridade) == ("CR-04", "R1", "ALTA")
    assert a.valores["escriturado"] == 4600.0
    con.close()


def test_cr04_so_efd_e_dctfweb_usa_saldo(tmp_path):
    con = db.conectar(tmp_path)
    db.inserir_fatos(con, [
        _fato(Fonte.EFD_CONTRIBUICOES, Natureza.ESCRITURADO, 500.0, cod="6912",
              detalhes={"debito_apurado": 500.0}),
        # competência diferente: não casa
        _fato(Fonte.DCTFWEB, Natureza.DECLARADO, 999.0, cod="1082-01", comp="2026.03",
              tributo="CP", detalhes={"saldo_pagar": 900.0}),
    ])
    linhas, achados = cr04.run(con)
    por_sit = {l["situacao"]: l for l in linhas}
    assert por_sit[cr04.SIT_SO_EFD]["diferenca"] == 500.0
    assert por_sit[cr04.SIT_SO_DECL]["dctfweb_debito"] == 900.0   # saldo, não débito
    assert {a.risco for a in achados} == {"R1", ""}
    con.close()


# ── CR-05 ─────────────────────────────────────────────────────────────────────

def test_cr05_quitado_darf_mais_dcomp(tmp_path):
    con = db.conectar(tmp_path)
    db.inserir_fatos(con, [
        _fato(Fonte.DCTF, Natureza.DECLARADO, 1000.0),
        _fato(Fonte.DARF, Natureza.PAGO, 700.0),
        _fato(Fonte.PERDCOMP, Natureza.COMPENSADO, 300.0,
              detalhes={"valor_principal": 300.0, "tipo_pedido":
                        "Declaração de Compensação", "numero_perdcomp": "N1"}),
    ])
    linhas, achados = cr05.run(con)
    assert linhas[0]["situacao"] == cr05.SIT_QUITADO
    assert achados == []
    con.close()


def test_cr05_saldo_a_pagar_r2(tmp_path):
    con = db.conectar(tmp_path)
    db.inserir_fatos(con, [
        _fato(Fonte.DCTF, Natureza.DECLARADO, 1000.0),
        _fato(Fonte.DARF, Natureza.PAGO, 600.0,
              detalhes={"multa": 50.0, "juros": 10.0}),   # acréscimos não contam
    ])
    linhas, achados = cr05.run(con)
    assert linhas[0]["situacao"] == cr05.SIT_SALDO
    assert linhas[0]["saldo_final"] == 400.0
    assert (achados[0].risco, achados[0].prioridade) == ("R2", "ALTA")
    con.close()


def test_cr05_pago_a_maior_e_sem_declaracao(tmp_path):
    con = db.conectar(tmp_path)
    db.inserir_fatos(con, [
        # pago a maior
        _fato(Fonte.DCTF, Natureza.DECLARADO, 500.0),
        _fato(Fonte.DARF, Natureza.PAGO, 800.0),
        # quitação sem confissão (outra competência)
        _fato(Fonte.DARF, Natureza.PAGO, 200.0, comp="2026.03"),
    ])
    linhas, achados = cr05.run(con)
    por_sit = {l["situacao"]: l for l in linhas}
    assert por_sit[cr05.SIT_A_MAIOR]["saldo_final"] == -300.0
    assert por_sit[cr05.SIT_SEM_DECL]["saldo_final"] == -200.0
    riscos = {a.risco for a in achados}
    assert riscos == {"R7", "R3"}
    con.close()


def test_cr05_dcomp_cancelada_fica_fora(tmp_path):
    con = db.conectar(tmp_path)
    db.inserir_fatos(con, [
        _fato(Fonte.DCTF, Natureza.DECLARADO, 1000.0),
        _fato(Fonte.PERDCOMP, Natureza.COMPENSADO, 1000.0,
              detalhes={"valor_principal": 1000.0, "tipo_pedido":
                        "Declaração de Compensação", "numero_perdcomp": "NCANC"}),
    ])
    # sem status: quitado
    linhas, _ = cr05.run(con)
    assert linhas[0]["situacao"] == cr05.SIT_QUITADO
    # com status cancelado: volta a ser saldo a pagar
    linhas, achados = cr05.run(con, status_map={"NCANC": {"situacao": "Cancelado a pedido"}})
    assert linhas[0]["situacao"] == cr05.SIT_SALDO
    assert achados[0].risco == "R2"
    con.close()


def test_cr05_per_puro_nao_quita(tmp_path):
    con = db.conectar(tmp_path)
    db.inserir_fatos(con, [
        _fato(Fonte.DCTF, Natureza.DECLARADO, 1000.0),
        _fato(Fonte.PERDCOMP, Natureza.COMPENSADO, 1000.0,
              detalhes={"valor_principal": 1000.0, "tipo_pedido":
                        "Pedido de Ressarcimento", "numero_perdcomp": "N2"}),
    ])
    linhas, _ = cr05.run(con)
    assert linhas[0]["situacao"] == cr05.SIT_SALDO   # PER puro não é quitação
    con.close()


def test_cr05_simples_rateio_quitado(tmp_path):
    rows = [{"cnpj": CNPJ, "competencia": "02/2026", "competencia_teste": "2026.02",
             "anexo": "I", "num_declaracao": "AP1", "total_debito": "1.000,00",
             "pis": "100,00", "cofins": "300,00", "icms": "600,00",
             "das_pago": True, "das_valor_pago": "1.000,00"},
            # mesmo nº de apuração em outro arquivo → deduplicado
            {"cnpj": CNPJ, "competencia": "02/2026", "competencia_teste": "2026.02",
             "num_declaracao": "AP1", "total_debito": "1.000,00",
             "pis": "100,00", "cofins": "300,00", "icms": "600,00",
             "das_pago": True, "das_valor_pago": "1.000,00"}]
    f = adapt.fatos_simples(rows)
    assert len(f) == 6   # 3 tributos × (declarado + pago rateado), sem duplicata
    con = db.conectar(tmp_path)
    db.inserir_fatos(con, f)
    linhas, achados = cr05.run(con)
    assert len(linhas) == 3
    assert {l["situacao"] for l in linhas} == {cr05.SIT_QUITADO}
    assert achados == []
    icms = next(l for l in linhas if l["codigo_receita"] == "SIMPLES-ICMS")
    assert icms["das_pago"] == 600.0   # rateio proporcional ao débito
    con.close()


# ── CR-08 e arquivo ativo ─────────────────────────────────────────────────────

def _fato_efd(valor, arquivo, retif=False, cod="6912", comp="2026.02"):
    return FatoFiscal(cnpj=CNPJ, competencia=comp, tributo="PIS",
                      fonte=Fonte.EFD_CONTRIBUICOES, natureza=Natureza.ESCRITURADO,
                      valor=valor, codigo_receita=cod, arquivo_origem=arquivo,
                      detalhes={"debito_apurado": valor, "retificadora": retif})


def test_cr04_usa_so_arquivo_ativo(tmp_path):
    from audit.cruzamentos import cr08
    con = db.conectar(tmp_path)
    db.inserir_fatos(con, [
        _fato_efd(1000.0, "PISCOFINS_202602_Original.txt"),
        _fato_efd(800.0, "PISCOFINS_202602_Retificadora.txt", retif=True),
        _fato(Fonte.DCTF, Natureza.DECLARADO, 800.0, cod="6912-01"),
    ])
    linhas, achados = cr04.run(con)
    # sem o arquivo ativo, EFD somaria 1800 e divergiria; com ele, confere
    assert len(linhas) == 1
    assert linhas[0]["efd_debito"] == 800.0
    assert linhas[0]["situacao"] == cr04.SIT_OK
    assert achados == []

    # CR-08 vê a história: retificadora reduziu o débito em 200
    linhas8, achados8 = cr08.run(con)
    assert len(linhas8) == 1 and linhas8[0]["versoes"] == 2
    assert linhas8[0]["diferencas_por_codigo"] == {"6912": -200.0}
    a = achados8[0]
    assert (a.ref, a.risco, a.prioridade) == ("CR-08", "R9", "ALTA")
    con.close()


def test_cr08_sem_diferenca_nao_gera_achado(tmp_path):
    from audit.cruzamentos import cr08
    con = db.conectar(tmp_path)
    db.inserir_fatos(con, [
        _fato_efd(1000.0, "a_original.txt"),
        _fato_efd(1000.0, "b_retificadora.txt", retif=True),
    ])
    linhas, achados = cr08.run(con)
    assert linhas[0]["situacao"] == "Sem diferenças"
    assert achados == []
    con.close()


def test_cr08_versao_unica_fica_fora(tmp_path):
    from audit.cruzamentos import cr08
    con = db.conectar(tmp_path)
    db.inserir_fatos(con, [_fato_efd(1000.0, "unico.txt")])
    linhas, achados = cr08.run(con)
    assert linhas == [] and achados == []
    con.close()


# ── CR-06 ─────────────────────────────────────────────────────────────────────

def _credito(valor, comp, trib="COFINS", arquivo="efd.txt"):
    return FatoFiscal(cnpj=CNPJ, competencia=comp, tributo=trib,
                      fonte=Fonte.EFD_CONTRIBUICOES, natureza=Natureza.CREDITO,
                      valor=valor, codigo_receita="CRED-101",
                      arquivo_origem=arquivo, detalhes={"sld_cred": valor})


def _pedido(valor, comp, tipo="Cofins Não-Cumulativa - Ressarc/Compens", num="P1"):
    return FatoFiscal(cnpj=CNPJ, competencia=comp, tributo=tipo,
                      fonte=Fonte.PERDCOMP, natureza=Natureza.PLEITEADO,
                      valor=valor, codigo_receita="101", arquivo_origem="per.pdf",
                      detalhes={"numero_perdcomp": num})


def test_cr06_pedido_com_e_sem_lastro(tmp_path):
    from audit.cruzamentos import cr06
    con = db.conectar(tmp_path)
    db.inserir_fatos(con, [
        # 1T/2022: lastro 30k (3 meses × 10k) × pedido 25k → com lastro
        _credito(10000.0, "2022.01"), _credito(10000.0, "2022.02"),
        _credito(10000.0, "2022.03"),
        _pedido(25000.0, "2022.1T", num="PA"),
        # 2T/2022: lastro 5k × pedido 20k → SEM lastro (excesso 15k)
        _credito(5000.0, "2022.04"),
        _pedido(20000.0, "2022.2T", num="PB"),
    ])
    linhas, achados = cr06.run(con)
    por_sit = {l["situacao"]: l for l in linhas}
    assert por_sit[cr06.SIT_COM_LASTRO]["lastro_escriturado"] == 30000.0
    sem = por_sit[cr06.SIT_SEM_LASTRO]
    assert sem["excesso"] == 15000.0
    a = next(a for a in achados if a.risco == "R6")
    assert a.prioridade == "ALTA" and "PB" in a.descricao
    con.close()


def test_cr06_credito_sem_pedido_gera_r7_com_decadencia(tmp_path):
    from audit.cruzamentos import cr06
    con = db.conectar(tmp_path)
    db.inserir_fatos(con, [_credito(8000.0, "2023.05", trib="PIS")])
    linhas, achados = cr06.run(con)
    assert linhas[0]["situacao"] == cr06.SIT_SEM_PEDIDO
    assert linhas[0]["competencia"] == "2023.2T"
    a = achados[0]
    assert (a.risco, a.decadencia) == ("R7", "2028-06")
    assert a.diferenca == -8000.0
    con.close()


def test_cr06_pedido_retificado_fica_fora(tmp_path):
    from audit.cruzamentos import cr06
    con = db.conectar(tmp_path)
    db.inserir_fatos(con, [
        _credito(10000.0, "2022.01"),
        _pedido(50000.0, "2022.1T", num="PRETIF"),
    ])
    linhas, achados = cr06.run(con)
    assert any(l["situacao"] == cr06.SIT_SEM_LASTRO for l in linhas)
    # retificado na planilha de status: pedido some, sobra crédito sem pedido
    linhas, achados = cr06.run(con, status_map={"PRETIF": {"situacao": "Retificado"}})
    assert all(l["situacao"] != cr06.SIT_SEM_LASTRO for l in linhas)
    assert any(l["situacao"] == cr06.SIT_SEM_PEDIDO for l in linhas)
    con.close()


# ── CR-01 ─────────────────────────────────────────────────────────────────────

ECD_MIN = "\n".join([
    "|0000|LECD|01012022|31122022|EMPRESA REAL LTDA|30995994000194|BA|150|2919553||0|",
    "|J005|01012022|31032022|1||",
    "|J150|1|RESUL|T|1||RESULTADO DO EXERCÍCIO|40403,2|C|70611,18|C|R||",
    "|J005|01042022|31122022|1||",
    "|J150|1|RESUL|T|1||RESULTADO DO EXERCÍCIO|70611,18|C|256871,02|C|R||",
    "|J005|01012022|31122022|1||",
    "|J150|1|RESUL|T|1||RESULTADO DO EXERCÍCIO|70821,54|C|327482,2|C|R||",
    "|9999|8|",
]) + "\n"

ECF_MIN = "\n".join([
    "|0000|LECF|0009|30995994000194|EMPRESA REAL LTDA|0|0|||01012022|31122022|N||0||",
    "|0010||N|1|T|01|RRRR|||||||",
    "|L030|01012022|31032022|T01|",
    "|L300|3|RESULTADO LÍQUIDO DO PERÍODO|S|1|04||70611,18|C|",
    "|L030|01042022|31122022|T02|",
    "|L300|3|RESULTADO LÍQUIDO DO PERÍODO|S|1|04||256871,02|C|",
    "|9999|7|",
]) + "\n"


def test_cr01_resultado_ecd_escolhe_lado_que_fecha(tmp_path):
    from audit.cruzamentos.cr01 import resultado_ecd
    arq = tmp_path / "ecd.txt"
    arq.write_text(ECD_MIN, encoding="latin-1")
    r = resultado_ecd(arq)
    # lado 2 fecha (70611,18 + 256871,02 = 327482,20); lado 1 é o ano anterior
    assert r == {"cnpj": "30995994000194", "exercicio": "2022",
                 "resultado": 327482.20}


def test_cr01_resultado_ecf_soma_trimestres(tmp_path):
    from audit.cruzamentos.cr01 import resultado_ecf
    arq = tmp_path / "ecf.txt"
    arq.write_text(ECF_MIN, encoding="latin-1")
    r = resultado_ecf(arq)
    assert r["resultado"] == 327482.20
    assert r["periodo"].startswith("Σ")


def test_cr01_run_conforme_e_divergente(tmp_path):
    from audit.cruzamentos import cr01
    from audit.core import config
    engaj = config.criar_engajamento(tmp_path, "T", "30.995.994/0001-94")
    (engaj / "raw" / "bx" / "ecd_2022.txt").write_text(ECD_MIN, encoding="latin-1")
    (engaj / "raw" / "bx" / "ecf_2022.txt").write_text(ECF_MIN, encoding="latin-1")
    linhas, achados = cr01.run(engaj)
    assert linhas[0]["situacao"] == "Conforme"
    assert achados == []
    # ECF divergente (resultado adulterado)
    (engaj / "raw" / "bx" / "ecf_2022.txt").write_text(
        ECF_MIN.replace("256871,02", "999999,99"), encoding="latin-1")
    linhas, achados = cr01.run(engaj)
    assert linhas[0]["situacao"] == "Divergente"
    a = achados[0]
    assert a.ref == "CR-01" and a.prioridade == "ALTA"
    assert a.valores["escriturado"] == 327482.20


def test_cr01_serie_incompleta(tmp_path):
    from audit.cruzamentos import cr01
    from audit.core import config
    engaj = config.criar_engajamento(tmp_path, "T", "30.995.994/0001-94")
    (engaj / "raw" / "bx" / "ecd_2022.txt").write_text(ECD_MIN, encoding="latin-1")
    linhas, achados = cr01.run(engaj)
    assert linhas[0]["situacao"] == "ECD sem ECF"
    assert achados[0].risco == "R3"


def test_cr06_lastro_nao_mistura_pis_e_cofins(tmp_path):
    """M100 (PIS) e M500 (COFINS) usam o mesmo código de crédito (101) —
    o lastro deve separar por tributo, não pelo primeiro fato do grupo."""
    from audit.cruzamentos import cr06
    con = db.conectar(tmp_path)
    db.inserir_fatos(con, [
        _credito(100.0, "2022.01", trib="PIS"),
        _credito(400.0, "2022.01", trib="COFINS"),   # mesmo CRED-101
        _pedido(400.0, "2022.1T", tipo="Cofins Não-Cumulativa - Ressarc", num="PC"),
        _pedido(100.0, "2022.1T", tipo="PIS/Pasep Não-Cumul - Ressarc", num="PP"),
    ])
    linhas, achados = cr06.run(con)
    por_trib = {l["tributo"]: l for l in linhas}
    assert por_trib["COFINS"]["lastro_escriturado"] == 400.0
    assert por_trib["PIS"]["lastro_escriturado"] == 100.0
    assert {l["situacao"] for l in linhas} == {cr06.SIT_COM_LASTRO}
    assert achados == []
    con.close()
