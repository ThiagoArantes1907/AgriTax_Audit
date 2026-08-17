"""Parâmetros da Reforma Tributária do Consumo — EC 132/2023 e LC 214/2025.

Tudo aqui é PARAMETRIZÁVEL de propósito: a alíquota de referência do IBS e da
CBS ainda será fixada por resolução do Senado / lei ordinária (CF, art. 156-A,
§1º, XII e art. 195, V). Trabalhamos com a estimativa oficial divulgada pelo
Ministério da Fazenda (26,5% somados) e deixamos explícito no relatório que é
projeção, não alíquota vigente.

Calendário da transição (EC 132/2023, art. 125 e seguintes do ADCT; LC 214/2025):
    2026  ano-teste: CBS 0,9% + IBS 0,1%, compensáveis com PIS/COFINS devidos
          (efeito financeiro praticamente nulo para quem cumpre a acessória)
    2027  CBS integral; PIS e COFINS EXTINTOS; IPI reduzido a zero (salvo ZFM);
          Imposto Seletivo em vigor; IBS segue em 0,1%
    2028  igual a 2027
    2029  IBS a 1/10 da referência e ICMS/ISS a 90%
    2030  IBS 2/10 / ICMS-ISS 80%
    2031  IBS 3/10 / ICMS-ISS 70%
    2032  IBS 4/10 / ICMS-ISS 60%
    2033  IBS integral; ICMS e ISS EXTINTOS
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ── alíquotas de referência (estimativa MF; ajustáveis por engajamento) ──────
CBS_REFERENCIA = 0.088
IBS_REFERENCIA = 0.177


def aliquota_referencia() -> float:
    return CBS_REFERENCIA + IBS_REFERENCIA


@dataclass(frozen=True)
class AnoTransicao:
    ano: int
    frac_cbs: float        # fração da alíquota CBS de referência aplicada
    frac_ibs: float        # fração da alíquota IBS de referência aplicada
    frac_pis_cofins: float  # fração do PIS/COFINS do regime atual ainda devida
    frac_icms_iss: float   # fração do ICMS/ISS atuais ainda devida
    compensavel: bool = False   # tributo novo abatido do antigo (ano-teste)
    nota: str = ""


CALENDARIO: tuple[AnoTransicao, ...] = (
    AnoTransicao(2026, 0.009 / CBS_REFERENCIA, 0.001 / IBS_REFERENCIA, 1.0, 1.0,
                 compensavel=True,
                 nota="Ano-teste: CBS 0,9% e IBS 0,1% compensáveis com PIS/COFINS"),
    AnoTransicao(2027, 1.0, 0.001 / IBS_REFERENCIA, 0.0, 1.0,
                 nota="CBS integral; PIS/COFINS extintos; IPI zerado (salvo ZFM)"),
    AnoTransicao(2028, 1.0, 0.001 / IBS_REFERENCIA, 0.0, 1.0,
                 nota="Mantém 2027"),
    AnoTransicao(2029, 1.0, 0.10, 0.0, 0.90, nota="IBS 1/10 · ICMS/ISS a 90%"),
    AnoTransicao(2030, 1.0, 0.20, 0.0, 0.80, nota="IBS 2/10 · ICMS/ISS a 80%"),
    AnoTransicao(2031, 1.0, 0.30, 0.0, 0.70, nota="IBS 3/10 · ICMS/ISS a 70%"),
    AnoTransicao(2032, 1.0, 0.40, 0.0, 0.60, nota="IBS 4/10 · ICMS/ISS a 60%"),
    AnoTransicao(2033, 1.0, 1.0, 0.0, 0.0,
                 nota="Regime pleno: ICMS e ISS extintos"),
)


# ── regimes diferenciados (LC 214/2025) ─────────────────────────────────────
@dataclass(frozen=True)
class Perfil:
    chave: str
    rotulo: str
    redutor: float          # 0,60 = redução de 60% da alíquota
    base_legal: str
    confianca: str = "confirmado"   # confirmado | indiciario | estimado
    observacao: str = ""


PADRAO = Perfil("padrao", "Regime regular (alíquota cheia)", 0.0,
                "LC 214/2025 — alíquota de referência")

PERFIS: dict[str, Perfil] = {
    "padrao": PADRAO,
    "reg60_agro": Perfil(
        "reg60_agro", "Redução de 60% — produtos agropecuários e insumos", 0.60,
        "LC 214/2025, arts. 138 e 145 (Anexos VII a IX)"),
    "reg60_saude": Perfil(
        "reg60_saude", "Redução de 60% — serviços de saúde", 0.60,
        "LC 214/2025, art. 128 e Anexo III"),
    "reg60_educacao": Perfil(
        "reg60_educacao", "Redução de 60% — serviços de educação", 0.60,
        "LC 214/2025, art. 128 e Anexo II"),
    "reg30_profissional": Perfil(
        "reg30_profissional", "Redução de 30% — sociedade de profissão regulamentada",
        0.30, "LC 214/2025, art. 127",
        confianca="indiciario",
        observacao="Depende de a sociedade ser composta por profissionais "
                   "habilitados na atividade-fim (uniprofissional). Confirmar "
                   "no contrato social e no registro do conselho."),
    "zero_cesta": Perfil(
        "zero_cesta", "Alíquota zero — Cesta Básica Nacional", 1.0,
        "LC 214/2025, art. 125 e Anexo I"),
}

# prefixo de CNAE → perfil presumido. Bússola para o diagnóstico rápido: o
# enquadramento definitivo é por NBS/NCM da operação, não por CNAE.
CNAE_PARA_PERFIL: tuple[tuple[str, str], ...] = (
    ("01", "reg60_agro"), ("02", "reg60_agro"), ("03", "reg60_agro"),
    ("86", "reg60_saude"),
    ("85", "reg60_educacao"),
    # profissões regulamentadas (art. 127): contábil, jurídica, engenharia,
    # arquitetura, publicidade, química/farmácia, veterinária, TI-tecnologia
    ("691", "reg30_profissional"), ("692", "reg30_profissional"),
    ("711", "reg30_profissional"), ("712", "reg30_profissional"),
    ("713", "reg30_profissional"), ("7311", "reg30_profissional"),
    ("7490", "reg30_profissional"), ("750", "reg30_profissional"),
)


def perfil_por_cnae(cnae: str) -> Perfil:
    """CNAE (só dígitos, ex.: '7120100') → perfil presumido de tributação."""
    dig = "".join(c for c in str(cnae or "") if c.isdigit())
    for prefixo, chave in sorted(CNAE_PARA_PERFIL, key=lambda x: -len(x[0])):
        if dig.startswith(prefixo):
            return PERFIS[chave]
    return PADRAO


# ── o que gera crédito no regime novo (crédito financeiro amplo) ────────────
# Diferença central para hoje: no IBS/CBS credita-se TODA aquisição de bens e
# serviços usada na atividade econômica (LC 214/2025, art. 47), inclusive uso e
# consumo, energia, fretes, comunicação e ativo imobilizado (crédito imediato),
# hoje vedados no ICMS (LC 87/96, art. 33) e restritos no PIS/COFINS.
CREDITO_NOVO_LIBERADO = (
    "material de uso e consumo",
    "energia elétrica (integral, sem prova de aplicação industrial)",
    "serviços tomados de terceiros em geral",
    "ativo imobilizado (crédito integral e imediato, sem CIAP 1/48)",
    "fretes e armazenagem",
    "comunicação, software e licenças",
)

# despesas que NÃO geram crédito ou seguem regime próprio
SEM_CREDITO = (
    "folha de pagamento e encargos (não há incidência)",
    "tributos e multas",
    "despesas financeiras (regime específico — LC 214/2025, arts. 182 e ss.)",
    "depreciação (o crédito ocorre na aquisição do bem, não na despesa)",
    "aquisições de pessoa física não contribuinte (salvo crédito presumido)",
)


@dataclass
class Premissas:
    """Premissas do cenário — todas visíveis no relatório."""
    cbs: float = CBS_REFERENCIA
    ibs: float = IBS_REFERENCIA
    perfil: Perfil = field(default_factory=lambda: PADRAO)
    # fração da base creditável que efetivamente vira crédito (conservador:
    # nem toda despesa vem de fornecedor contribuinte no regime regular)
    aproveitamento_credito: float = 0.90
    # alíquota média dos fornecedores (padrão = referência cheia)
    aliquota_fornecedor: float | None = None

    @property
    def referencia(self) -> float:
        return self.cbs + self.ibs

    @property
    def efetiva(self) -> float:
        """Alíquota aplicável às saídas, já com o redutor do perfil."""
        return self.referencia * (1 - self.perfil.redutor)

    @property
    def efetiva_entrada(self) -> float:
        """Alíquota média presumida das aquisições (gera o crédito)."""
        return self.aliquota_fornecedor if self.aliquota_fornecedor is not None \
            else self.referencia
