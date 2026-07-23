"""Testes do M5: módulo Simples Nacional (SN-01/02/04/11)."""
import pytest

from audit.core import db
from audit.parsers import fatos as adapt
from audit.reperformance.sn import checks
from audit.reperformance.sn.base import carregar_apuracoes, rbt12_recalculada

CNPJ = "04.461.884/0001-32"


def _row(comp_mm_aaaa, rpa="100.000,00", rbt12="1.200.000,00", anexo="I",
         fator_r="", total="9.067,00", das_pago=True, das_valor=None, **debitos):
    base = {"cnpj": CNPJ, "competencia": comp_mm_aaaa,
            "competencia_teste": f"{comp_mm_aaaa[3:]}.{comp_mm_aaaa[:2]}",
            "anexo": anexo, "fator_r": fator_r, "rbt12": rbt12, "rpa": rpa,
            "num_declaracao": f"AP{comp_mm_aaaa}", "total_debito": total,
            "das_pago": das_pago,
            "das_valor_pago": das_valor if das_valor is not None else total,
            "pis": "270,00", "cofins": "1.247,00", "icms": "3.400,00",
            "cpp": "4.150,00"}
    base.update(debitos)
    return base


def _seed(tmp_path, rows):
    con = db.conectar(tmp_path)
    db.inserir_fatos(con, adapt.fatos_simples(rows))
    return con


def test_carregar_apuracoes_reconstroi_extrato(tmp_path):
    con = _seed(tmp_path, [_row("02/2026")])
    aps = carregar_apuracoes(con)
    assert len(aps) == 1
    ap = aps[0]
    assert ap["competencia"] == "2026.02"
    assert ap["rbt12"] == 1_200_000.0
    assert ap["debitos"]["ICMS"] == 3400.0
    assert ap["das_pago"] == 9067.0
    con.close()


def test_rbt12_recalculada_exige_serie_completa(tmp_path):
    rows = [_row(f"{m:02d}/2025", rpa="100.000,00") for m in range(1, 13)]
    rows.append(_row("01/2026", rpa="100.000,00", rbt12="1.150.000,00"))
    con = _seed(tmp_path, rows)
    aps = carregar_apuracoes(con)
    idx = next(i for i, a in enumerate(aps) if a["competencia"] == "2026.01")
    assert rbt12_recalculada(aps, idx) == 1_200_000.0    # 12 × 100k
    # sem os 12 anteriores completos → None
    idx0 = next(i for i, a in enumerate(aps) if a["competencia"] == "2025.06")
    assert rbt12_recalculada(aps, idx0) is None
    con.close()


def test_sn01_estouro_e_divergencia(tmp_path):
    rows = [_row(f"{m:02d}/2025", rpa="450.000,00") for m in range(1, 13)]
    # jan/2026: RBT12 real = 5,4mi (estouro >20%? 5,4/4,8 = 12,5% → ≤20%)
    rows.append(_row("01/2026", rbt12="5.400.000,00", rpa="450.000,00"))
    con = _seed(tmp_path, rows)
    aps = carregar_apuracoes(con)
    achados = checks.run_sn01(aps)
    estouros = [a for a in achados if "acima do limite" in a.titulo]
    assert len(estouros) == 1
    a = estouros[0]
    assert (a.ref, a.risco, a.prioridade) == ("SN-01", "S1", "ALTA")
    assert a.valores["excesso"] == 600_000.0
    assert "ano seguinte" in a.descricao        # excesso ≤ 20%
    con.close()


def test_sn01_excesso_acima_de_20pct():
    aps = [{"cnpj": "1", "competencia": "2026.01", "rbt12": 6_000_000.0,
            "rpa": 0, "anexo": "", "fator_r": None, "total_debito": 0,
            "debitos": {}, "das_pago": 0, "num_declaracao": ""}]
    achados = checks.run_sn01(aps)
    assert "MÊS SEGUINTE" in achados[0].descricao   # 25% > 20%


def test_sn02_icms_no_das_acima_do_sublimite(tmp_path):
    con = _seed(tmp_path, [_row("02/2026", rbt12="3.700.000,00")])
    aps = carregar_apuracoes(con)
    achados = checks.run_sn02(aps)
    assert len(achados) == 1
    assert achados[0].risco == "S1"
    assert achados[0].valores == {"ICMS": 3400.0}
    # sublimite maior (estado sem sublimite reduzido): sem achado
    assert checks.run_sn02(aps, sublimite=4_800_000.0) == []
    con.close()


def test_sn04_anexo_incompativel_com_fator_r(tmp_path):
    rows = [_row("01/2026", anexo="V", fator_r="30,00%"),    # r≥28% → III: a maior
            _row("02/2026", anexo="III", fator_r="20,00%"),  # r<28% → V: a menor
            _row("03/2026", anexo="III", fator_r="28,00%"),  # correto
            _row("04/2026", anexo="I", fator_r="10,00%")]    # anexo sem fator r
    con = _seed(tmp_path, rows)
    aps = carregar_apuracoes(con)
    achados = {a.competencia: a for a in checks.run_sn04(aps)}
    assert set(achados) == {"2026.01", "2026.02"}
    assert "restituição" in achados["2026.01"].acao_proposta
    assert "recolher a diferença" in achados["2026.02"].acao_proposta
    assert all(a.risco == "S2" for a in achados.values())
    con.close()


def test_sn11_das_aberto_e_pago_a_maior(tmp_path):
    rows = [_row("01/2026", das_pago=False, das_valor="0,00"),        # em aberto
            _row("02/2026", das_valor="9.500,00"),                     # a maior
            _row("03/2026")]                                           # quitado
    con = _seed(tmp_path, rows)
    aps = carregar_apuracoes(con)
    achados = {a.competencia: a for a in checks.run_sn11(aps)}
    assert set(achados) == {"2026.01", "2026.02"}
    aberto = achados["2026.01"]
    assert (aberto.risco, aberto.prioridade) == ("S3", "ALTA")
    assert aberto.diferenca == 9067.0
    a_maior = achados["2026.02"]
    assert (a_maior.risco, a_maior.prioridade) == ("R7", "MEDIA")
    assert a_maior.diferenca == -433.0
    con.close()


def test_run_todos_grava_por_ref(tmp_path):
    con = _seed(tmp_path, [_row("02/2026", rbt12="5.000.000,00")])
    aps, por_ref = checks.run_todos(con)
    assert set(por_ref) == {"SN-01", "SN-02", "SN-04", "SN-11"}
    assert len(por_ref["SN-01"]) == 1      # estouro
    assert len(por_ref["SN-02"]) == 1      # ICMS no DAS acima do sublimite
    con.close()
