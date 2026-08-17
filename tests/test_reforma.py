"""Testes do simulador da Reforma Tributária (M8)."""
from audit.reforma import parametros as prm
from audit.reforma.coleta import AnoDados, Dados, Medida
from audit.reforma.simulador import simular


def _dados(**kw) -> Dados:
    ano = AnoDados(
        ano=kw.get("ano", "2025"),
        receita=Medida(kw.get("receita", 1_000_000.0), "EFD-Contribuições", "confirmado"),
        pis_cofins=Medida(kw.get("pis_cofins", 36_500.0), "EFD-Contribuições (M200/M600)",
                          "confirmado"),
        iss=Medida(kw.get("iss", 50_000.0), "ECD (conta de dedução ISS)", "confirmado"),
        base_creditavel=Medida(kw.get("creditavel", 200_000.0), "ECD", "estimado"),
        meses=kw.get("meses", 12))
    d = Dados(cnpj="00000000000191", nome="TESTE", cnae=kw.get("cnae", ""))
    d.anos[ano.ano] = ano
    return d


def test_calendario_cobre_2026_a_2033():
    anos = [t.ano for t in prm.CALENDARIO]
    assert anos == list(range(2026, 2034))
    assert prm.CALENDARIO[-1].frac_icms_iss == 0.0     # ICMS/ISS extintos
    assert prm.CALENDARIO[-1].frac_ibs == 1.0
    assert prm.CALENDARIO[1].frac_pis_cofins == 0.0    # extintos em 2027


def test_perfil_por_cnae():
    assert prm.perfil_por_cnae("7120100").chave == "reg30_profissional"
    assert prm.perfil_por_cnae("0111301").chave == "reg60_agro"
    assert prm.perfil_por_cnae("8610101").chave == "reg60_saude"
    assert prm.perfil_por_cnae("4711302").chave == "padrao"
    assert prm.perfil_por_cnae("").chave == "padrao"


def test_ano_teste_2026_nao_gera_custo_adicional():
    res = simular(_dados())
    p2026 = res.projecao[0]
    # tributo novo é compensado com o PIS/COFINS devido
    assert p2026.novo_liquido == 0.0
    assert p2026.total < res.base.pis_cofins.valor + res.base.iss.valor


def test_pis_cofins_extintos_a_partir_de_2027():
    res = simular(_dados())
    for p in res.projecao[1:]:
        assert p.legado_pis_cofins == 0.0


def test_carga_plena_usa_aliquota_cheia_menos_creditos():
    res = simular(_dados(receita=1_000_000.0, creditavel=200_000.0))
    esperado = (1_000_000.0 * prm.aliquota_referencia()
                - 200_000.0 * prm.aliquota_referencia() * 0.90)
    assert abs(res.pleno.total - esperado) < 0.01
    assert res.pleno.legado_icms_iss == 0.0


def test_redutor_reduz_debito_e_gera_cenario_alternativo():
    res = simular(_dados(cnae="7120100"))
    assert res.premissas.perfil.redutor == 0.30
    assert res.alternativo is not None                  # perfil indiciário
    assert res.alternativo.pleno.total > res.pleno.total
    # o cenário alternativo não recursiona
    assert res.alternativo.alternativo is None


def test_anualiza_serie_incompleta():
    res = simular(_dados(receita=500_000.0, meses=6))
    assert abs(res.fator_anualizacao - 2.0) < 1e-9
    assert abs(res.receita - 1_000_000.0) < 0.01
    assert any("anualizados" in a for a in res.alertas)


def test_sem_receita_retorna_none():
    d = Dados()
    d.anos["2025"] = AnoDados(ano="2025")
    assert simular(d) is None


def test_ano_referencia_prefere_cobertura_e_nao_recencia():
    d = _dados(ano="2024", meses=12)
    parcial = AnoDados(ano="2025", meses=3,
                       receita=Medida(90_000.0, "EFD-Contribuições", "confirmado"))
    d.anos["2025"] = parcial
    assert d.ano_referencia().ano == "2024"


def test_base_atipica_gera_alerta():
    res = simular(_dados(receita=1_000_000.0, creditavel=900_000.0))
    assert any("ramp-up" in a or "atípico" in a for a in res.alertas)
