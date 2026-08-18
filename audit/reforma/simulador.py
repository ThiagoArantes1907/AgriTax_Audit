"""Projeção da carga CBS/IBS ano a ano a partir dos números reais do cliente.

Método (tudo a preços de hoje, sem projeção de crescimento — o objetivo é
isolar o efeito tributário):

    carga do ano = tributos legados × fração ainda devida
                 + (receita × alíquota nova efetiva − créditos ampliados)

A alíquota nova efetiva já considera o redutor do regime diferenciado; os
créditos usam a alíquota de referência das aquisições, com fator de
aproveitamento conservador. No ano-teste (2026) o tributo novo é compensável
com o PIS/COFINS devido — modelado como abatimento, não como custo adicional.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from dataclasses import replace

from .coleta import AnoDados, Dados, Medida
from .parametros import CALENDARIO, PADRAO, Premissas, perfil_por_cnae


def _anualizar(base: AnoDados) -> tuple[AnoDados, float]:
    """Leva medidas de ano incompleto à escala anual.

    Só escala o que vem de escrituração mensal (EFD / DARF do banco): ECD e
    ECF já são anuais por natureza. Cada bloco usa a própria cobertura — a
    EFD-ICMS costuma começar em mês diferente da EFD-Contribuições.
    """
    fator = 12 / base.meses if 0 < base.meses < 12 else 1.0
    fator_icms = 12 / base.meses_icms if 0 < base.meses_icms < 12 else 1.0
    if fator == 1.0 and fator_icms == 1.0:
        return base, 1.0

    def esc(m: Medida, f: float) -> Medida:
        if not m or f == 1.0:
            return m
        return Medida(round(m.valor * f, 2),
                      f"{m.fonte} — anualizado", "estimado")

    def mensal(m: Medida, f: float) -> Medida:
        """Escala só o que nasce de escrituração mensal."""
        return esc(m, f) if m.fonte.startswith(("EFD", "DARF")) else m

    novo = replace(
        base,
        receita=mensal(base.receita, fator),
        pis_cofins=mensal(base.pis_cofins, fator),
        icms=mensal(base.icms, fator_icms),
        icms_debitos=mensal(base.icms_debitos, fator_icms),
        creditos_atuais=mensal(base.creditos_atuais, fator),
        receita_b2b=mensal(base.receita_b2b, fator),
        compras_pf=mensal(base.compras_pf, fator),
        base_creditavel=mensal(base.base_creditavel, fator))
    # exercício fechado da ECD vale mais que extrapolação de série incompleta
    if fator != 1.0 and base.receita_ecd:
        novo.receita = base.receita_ecd
    return novo, fator


@dataclass
class ProjecaoAno:
    ano: int
    legado_pis_cofins: float
    legado_icms_iss: float
    debito_novo: float
    credito_novo: float
    nota: str = ""

    @property
    def novo_liquido(self) -> float:
        return max(self.debito_novo - self.credito_novo, 0.0)

    @property
    def total(self) -> float:
        return self.legado_pis_cofins + self.legado_icms_iss + self.novo_liquido


@dataclass
class Resultado:
    dados: Dados
    base: AnoDados               # já anualizada, pronta para o cálculo
    premissas: Premissas
    base_bruta: AnoDados | None = None   # como veio do acervo
    fator_anualizacao: float = 1.0
    projecao: list[ProjecaoAno] = field(default_factory=list)
    alertas: list[str] = field(default_factory=list)
    oportunidades: list[str] = field(default_factory=list)
    # cenário sem o regime diferenciado, quando o enquadramento é apenas
    # indiciário: o cliente precisa ver os dois lados da aposta
    alternativo: "Resultado | None" = None

    # ── medidas de referência ──
    @property
    def receita(self) -> float:
        return self.base.receita.valor

    @property
    def carga_hoje_valor(self) -> float:
        return self.base.tributos_consumo

    @property
    def carga_hoje_pct(self) -> float:
        return self.base.carga_atual

    @property
    def pleno(self) -> ProjecaoAno:
        return self.projecao[-1]

    @property
    def carga_plena_pct(self) -> float:
        return self.pleno.total / self.receita if self.receita else 0.0

    @property
    def delta_anual(self) -> float:
        return self.pleno.total - self.carga_hoje_valor

    @property
    def pct_b2b(self) -> float:
        """Fração da receita faturada contra PJ que tomará crédito."""
        if not self.base.receita_b2b or not self.receita:
            return 0.0
        return min(self.base.receita_b2b.valor / self.receita, 1.0)

    @property
    def impacto_economico(self) -> float:
        """Parcela do aumento que NÃO se resolve com crédito do cliente.

        Na receita B2B o adquirente credita integralmente o IBS/CBS destacado:
        o aumento é neutro na cadeia DESDE QUE o preço seja renegociado. A
        parcela para consumidor final/não contribuinte é impacto direto.
        """
        return self.delta_anual * (1 - self.pct_b2b)


def _aliquotas(t, p: Premissas) -> tuple[float, float]:
    """(alíquota de saída no ano, alíquota média das entradas no ano)."""
    saida = (p.cbs * t.frac_cbs + p.ibs * t.frac_ibs) * (1 - p.perfil.redutor)
    entrada = (p.cbs * t.frac_cbs + p.ibs * t.frac_ibs)
    if p.aliquota_fornecedor is not None and p.referencia:
        entrada *= p.aliquota_fornecedor / p.referencia
    return saida, entrada


def simular(dados: Dados, premissas: Premissas | None = None,
            ano_base: str = "", com_alternativo: bool = True) -> Resultado | None:
    base = dados.anos.get(ano_base) if ano_base else dados.ano_referencia()
    if base is None or not base.receita.valor:
        return None
    if premissas is None:
        premissas = Premissas(perfil=perfil_por_cnae(dados.cnae))

    bruta = base
    base, fator = _anualizar(base)
    res = Resultado(dados=dados, base=base, premissas=premissas,
                    base_bruta=bruta, fator_anualizacao=fator)
    for t in CALENDARIO:
        aliq_saida, aliq_entrada = _aliquotas(t, premissas)
        debito = base.receita.valor * aliq_saida
        # aquisições de PF não contribuinte não geram crédito cheio
        cred_pj = max(base.base_creditavel.valor - base.compras_pf.valor, 0.0)
        credito = (cred_pj * aliq_entrada * premissas.aproveitamento_credito
                   + base.compras_pf.valor * aliq_entrada
                   * premissas.credito_presumido_pf)
        legado_pc = base.pis_cofins.valor * t.frac_pis_cofins
        legado_ii = (base.icms.valor + base.iss.valor) * t.frac_icms_iss
        if t.compensavel:
            # ano-teste: o novo tributo é abatido do PIS/COFINS devido
            abatido = min(max(debito - credito, 0.0), legado_pc)
            legado_pc -= abatido
            debito, credito = 0.0, 0.0
        res.projecao.append(ProjecaoAno(
            ano=t.ano, legado_pis_cofins=round(legado_pc, 2),
            legado_icms_iss=round(legado_ii, 2), debito_novo=round(debito, 2),
            credito_novo=round(credito, 2), nota=t.nota))

    if (com_alternativo and premissas.perfil.redutor > 0
            and premissas.perfil.confianca != "confirmado"):
        prem_padrao = Premissas(cbs=premissas.cbs, ibs=premissas.ibs,
                                perfil=PADRAO,
                                aproveitamento_credito=premissas.aproveitamento_credito,
                                aliquota_fornecedor=premissas.aliquota_fornecedor)
        res.alternativo = simular(dados, prem_padrao, ano_base=base.ano,
                                  com_alternativo=False)

    _analisar(res)
    return res


def _analisar(res: Resultado) -> None:
    """Gera alertas e oportunidades a partir do perfil real do cliente."""
    b, p = res.base, res.premissas
    receita = res.receita

    # 1. sentido e tamanho do impacto
    if res.delta_anual > 0:
        res.alertas.append(
            f"Carga sobre consumo sobe de {res.carga_hoje_pct*100:.1f}% para "
            f"{res.carga_plena_pct*100:.1f}% da receita no regime pleno — "
            f"R$ {res.delta_anual:,.2f} por ano a mais, a preços de hoje.")
    else:
        res.oportunidades.append(
            f"Carga sobre consumo CAI de {res.carga_hoje_pct*100:.1f}% para "
            f"{res.carga_plena_pct*100:.1f}% da receita — o crédito amplo do "
            f"novo modelo mais do que compensa a alíquota maior "
            f"(R$ {abs(res.delta_anual):,.2f}/ano a menos).")

    # 1b. o ano-base representa a operação em regime?
    if b.base_creditavel.valor > receita * 0.80:
        res.alertas.append(
            f"Atenção à leitura: no exercício-base ({b.ano}) as aquisições "
            f"(R$ {b.base_creditavel.valor:,.2f}) superam 80% da receita "
            f"(R$ {receita:,.2f}) — operação em implantação/ramp-up ou ano "
            f"atípico. Como o novo modelo credita quase tudo, a projeção fica "
            f"artificialmente favorável: refazer com um exercício em regime "
            f"normal de operação antes de decidir.")
    if res.fator_anualizacao != 1.0:
        res.alertas.append(
            f"O exercício-base tem série fiscal incompleta no acervo "
            f"({b.meses} mês(es) de EFD): os valores mensais foram anualizados "
            f"(fator {res.fator_anualizacao:.2f}). Completar a série pelo "
            f"ReceitaNetBX eleva a precisão da projeção.")

    # 2. quem é o cliente decide se o aumento é repassável
    if b.receita_b2b:
        res.oportunidades.append(
            f"{res.pct_b2b*100:.0f}% da receita é faturada contra pessoa "
            f"jurídica (base das retenções na EFD): esses clientes tomarão "
            f"crédito integral do IBS/CBS, então o aumento é neutro na cadeia "
            f"— desde que os contratos sejam renegociados. O impacto econômico "
            f"real concentra-se nos {100-res.pct_b2b*100:.0f}% restantes: "
            f"≈ R$ {res.impacto_economico:,.2f}/ano.")
        res.alertas.append(
            "Contratos de prazo longo com preço fechado precisam de cláusula "
            "de revisão tributária ANTES de 2027 — sem ela, o aumento vira "
            "perda de margem mesmo em operação B2B.")
    else:
        res.alertas.append(
            "Não foi possível medir a fatia B2B da receita no acervo. Como o "
            "aumento só é neutro quando o cliente credita, essa segregação "
            "(faturamento por tipo de adquirente) é o primeiro dado a levantar.")

    # 3. crédito amplo — o ganho estrutural
    if b.base_creditavel:
        credito_pleno = (b.base_creditavel.valor * p.referencia
                         * p.aproveitamento_credito)
        ganho = credito_pleno - b.creditos_atuais.valor
        if ganho > 0:
            res.oportunidades.append(
                f"O crédito financeiro amplo (LC 214/2025, art. 47) passa a "
                f"alcançar uso e consumo, energia, fretes, serviços e ativo "
                f"imobilizado — hoje vedados ou restritos. Sobre a base atual "
                f"de R$ {b.base_creditavel.valor:,.2f}, o crédito estimado sobe "
                f"para R$ {credito_pleno:,.2f} (hoje: "
                f"R$ {b.creditos_atuais.valor:,.2f}) — "
                f"R$ {ganho:,.2f}/ano de crédito novo.")

    # 3b. compras de pessoa física — o ponto cego do agro
    if b.compras_pf:
        pct = b.compras_pf.valor / b.base_creditavel.valor if b.base_creditavel else 0
        res.alertas.append(
            f"R$ {b.compras_pf.valor:,.2f} das aquisições ({pct*100:.0f}% da "
            f"base) vêm de PESSOA FÍSICA — produtor rural não contribuinte. No "
            f"IBS/CBS essas compras NÃO geram crédito cheio: a LC 214/2025 "
            f"prevê crédito presumido, mas o percentual depende de "
            f"regulamentação. Esta projeção adota a hipótese conservadora "
            f"(crédito zero sobre elas); cada 10 pontos de crédito presumido "
            f"que vierem a ser concedidos valem "
            f"R$ {b.compras_pf.valor * p.referencia * 0.10:,.2f}/ano para a "
            f"empresa. É a variável que mais afeta o resultado — acompanhar a "
            f"regulamentação e, desde já, mapear quais fornecedores podem "
            f"optar por ser contribuintes.")

    # 4. redutor de regime diferenciado
    if p.perfil.redutor > 0:
        economia = receita * p.referencia * p.perfil.redutor
        texto = (f"{p.perfil.rotulo}: reduz a alíquota de "
                 f"{p.referencia*100:.1f}% para {p.efetiva*100:.2f}% — "
                 f"≈ R$ {economia:,.2f}/ano. Base: {p.perfil.base_legal}.")
        if p.perfil.confianca != "confirmado":
            texto += " " + (p.perfil.observacao or
                            "Enquadramento a confirmar documentalmente.")
        res.oportunidades.append(texto)
        if res.alternativo is not None:
            alt = res.alternativo
            res.alertas.append(
                f"Esse enquadramento é a decisão mais valiosa da transição para "
                f"a empresa: COM o redutor a carga vai a "
                f"{res.carga_plena_pct*100:.1f}% da receita; SEM ele, sobe para "
                f"{alt.carga_plena_pct*100:.1f}% "
                f"(R$ {alt.delta_anual:,.2f}/ano a mais que hoje). A diferença "
                f"entre os dois cenários é de R$ "
                f"{alt.pleno.total - res.pleno.total:,.2f} por ano — vale "
                f"estruturar contrato social e documentação para sustentar o "
                f"enquadramento antes de 2027.")
    else:
        res.oportunidades.append(
            "Mapear se alguma linha de receita se enquadra em regime "
            "diferenciado (reduções de 60%/30% ou alíquota zero da LC "
            "214/2025): o enquadramento é por operação (NBS/NCM), não por "
            "CNAE — receitas acessórias podem ter tratamento próprio.")

    # 5. benefício estadual de ICMS que se extingue
    if b.icms_debitos and b.icms_debitos.valor > 0:
        aproveitado = 1 - (b.icms.valor / b.icms_debitos.valor)
        if aproveitado > 0.30:
            res.alertas.append(
                f"A empresa hoje recolhe apenas {(1-aproveitado)*100:.0f}% do "
                f"ICMS debitado (R$ {b.icms.valor:,.2f} de "
                f"R$ {b.icms_debitos.valor:,.2f}) — há benefício/crédito "
                f"outorgado relevante. Benefícios de ICMS se extinguem com o "
                f"imposto até 2032 (o Fundo de Compensação da EC 132/2023 "
                f"cobre apenas parte e exige habilitação): esse ganho precisa "
                f"ser reposto no planejamento.")

    # 6. caixa e split payment
    res.alertas.append(
        "Split payment: o IBS/CBS passa a ser retido na liquidação financeira "
        "da operação (LC 214/2025). Quem hoje financia capital de giro com o "
        "prazo entre faturar e recolher perde esse fôlego — simular o efeito "
        "no fluxo de caixa é parte do plano de transição.")

    # 7. CAPEX
    res.oportunidades.append(
        "Investimentos em ativo imobilizado passam a gerar crédito integral e "
        "imediato (sem CIAP 1/48 e sem as restrições do PIS/COFINS): o "
        "cronograma de CAPEX deve considerar essa mudança na comparação entre "
        "investir antes ou depois de 2027.")


def resumo_texto(res: Resultado) -> list[str]:
    linhas = [
        f"Base do cálculo: {res.base.ano} — receita R$ {res.receita:,.2f} "
        f"({res.base.receita.fonte})",
        f"Carga hoje: R$ {res.carga_hoje_valor:,.2f} "
        f"({res.carga_hoje_pct*100:.2f}% da receita)",
        f"Regime pleno (2033): R$ {res.pleno.total:,.2f} "
        f"({res.carga_plena_pct*100:.2f}%) → variação de "
        f"R$ {res.delta_anual:,.2f}/ano",
    ]
    if res.base.receita_b2b:
        linhas.append(f"Receita B2B: {res.pct_b2b*100:.0f}% → impacto econômico "
                      f"estimado R$ {res.impacto_economico:,.2f}/ano")
    return linhas
