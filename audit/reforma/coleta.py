"""Coleta os números reais do engajamento para o simulador da Reforma.

Princípio de qualidade (o diagnóstico não pode ficar pobre por falta de
arquivo): cada medida carrega FONTE e NÍVEL DE CONFIANÇA, e há cascata de
fontes substitutas. O que não existir vira lacuna declarada no relatório —
nunca um número inventado nem um silêncio.

Cascata por medida:
    receita          EFD-Contribuições → ECF (P200) → ECD (DRE)
    PIS/COFINS       EFD (M200/M600) → DARF/DCTF no banco
    ICMS             EFD-ICMS (E110) → (lacuna)
    ISS              ECD (conta de dedução) → (lacuna)
    base creditável  ECD (despesas elegíveis) → EFD entradas (C170)
    perfil B2B       EFD F600 (retenções por CNPJ) → C100/0150 → (lacuna)
"""
from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path

from audit.parsers.central import identificar_tipo
from audit.parsers.efd_contribuicoes import _efd_brl

CONFIRMADO, INDICIARIO, ESTIMADO, LACUNA = (
    "confirmado", "indiciario", "estimado", "lacuna")


@dataclass
class Medida:
    valor: float = 0.0
    fonte: str = ""
    confianca: str = LACUNA

    def __bool__(self) -> bool:
        return self.confianca != LACUNA


@dataclass
class AnoDados:
    ano: str
    receita: Medida = field(default_factory=Medida)
    receita_ecd: Medida = field(default_factory=Medida)   # exercício fechado
    pis_cofins: Medida = field(default_factory=Medida)
    icms: Medida = field(default_factory=Medida)
    icms_debitos: Medida = field(default_factory=Medida)
    iss: Medida = field(default_factory=Medida)
    base_creditavel: Medida = field(default_factory=Medida)
    creditos_atuais: Medida = field(default_factory=Medida)
    receita_b2b: Medida = field(default_factory=Medida)
    creditavel_top: list[tuple[str, float]] = field(default_factory=list)
    meses: int = 0        # competências de EFD-Contribuições no ano
    meses_icms: int = 0   # competências de EFD-ICMS no ano

    @property
    def tributos_consumo(self) -> float:
        return self.pis_cofins.valor + self.icms.valor + self.iss.valor

    @property
    def carga_atual(self) -> float:
        return self.tributos_consumo / self.receita.valor if self.receita.valor else 0.0


@dataclass
class Dados:
    cnpj: str = ""
    nome: str = ""
    uf: str = ""
    cnae: str = ""
    regime: str = ""
    anos: dict[str, AnoDados] = field(default_factory=dict)
    completude: list[tuple[str, bool, str]] = field(default_factory=list)

    def ano_referencia(self) -> AnoDados | None:
        """Ano mais representativo: o de maior cobertura, desempate pelo recente.

        Preferir o ano mais RECENTE seria armadilha — o exercício corrente
        costuma ter poucos meses e distorceria a base anual do cálculo.
        """
        com_receita = [a for a in self.anos.values() if a.receita]
        if not com_receita:
            return None
        return max(com_receita, key=lambda a: (min(a.meses, 12), a.ano))


def _campos(ln: str) -> list[str]:
    return ln.split("|")[1:-1] if ln.endswith("|") else ln.split("|")[1:]


def _linhas(p: Path) -> list[str]:
    return p.read_bytes().decode("latin-1", "replace").split("\r\n")


def _ano(a: AnoDados | None, dados: Dados, ano: str) -> AnoDados:
    if ano not in dados.anos:
        dados.anos[ano] = AnoDados(ano=ano)
    return dados.anos[ano]


# ── EFD-Contribuições ───────────────────────────────────────────────────────
def _efd_contribuicoes(engaj: Path, dados: Dados) -> None:
    """Versão ativa por competência → receita, PIS/COFINS devidos e B2B."""
    ativos: dict[str, Path] = {}
    for p in sorted((engaj / "raw" / "bx").glob("*.txt")):
        if identificar_tipo(p) != "efd_contribuicoes":
            continue
        c = _campos(_linhas(p)[0])
        if len(c) < 7:
            continue
        comp = c[6][4:8] + "." + c[6][2:4]
        if comp not in ativos or p.name > ativos[comp].name:
            ativos[comp] = p
        if not dados.cnpj and len(c) > 8:
            dados.cnpj, dados.nome = c[8].strip(), c[7].strip()
            dados.uf = c[9].strip() if len(c) > 9 else ""
    if not ativos:
        dados.completude.append(("EFD-Contribuições", False, "nenhum arquivo no acervo"))
        return

    receita: dict[str, float] = defaultdict(float)
    contrib: dict[str, float] = defaultdict(float)
    creditos: dict[str, float] = defaultdict(float)
    entradas: dict[str, float] = defaultdict(float)
    b2b: dict[str, float] = defaultdict(float)
    meses: dict[str, int] = defaultdict(int)
    for comp, p in sorted(ativos.items()):
        ano = comp[:4]
        meses[ano] += 1
        ind_oper = None
        for ln in _linhas(p):
            f = _campos(ln)
            if not f:
                continue
            reg = f[0]
            if reg == "F550" and len(f) > 1:            # cumulativo consolidado
                receita[ano] += _efd_brl(f[1])
            elif reg == "C100" and len(f) > 11:
                ind_oper = f[1].strip()
                if ind_oper == "1":
                    receita[ano] += _efd_brl(f[11])
                else:
                    entradas[ano] += _efd_brl(f[11])
            elif reg == "A100" and len(f) > 12:          # serviços (NFS-e)
                if f[1].strip() == "1":
                    receita[ano] += _efd_brl(f[12])
            elif reg == "M200" and len(f) > 8:
                contrib[ano] += _efd_brl(f[4]) + _efd_brl(f[8])
                creditos[ano] += _efd_brl(f[2])
            elif reg == "M600" and len(f) > 8:
                contrib[ano] += _efd_brl(f[4]) + _efd_brl(f[8])
                creditos[ano] += _efd_brl(f[2])
            elif reg == "F600" and len(f) > 4:
                # base das retenções = receita faturada contra PJ (só PJ retém)
                b2b[ano] += _efd_brl(f[3])
    for ano in sorted(set(receita) | set(contrib)):
        a = _ano(None, dados, ano)
        a.meses = meses[ano]
        if receita[ano]:
            a.receita = Medida(round(receita[ano], 2), "EFD-Contribuições", CONFIRMADO)
        if contrib[ano]:
            a.pis_cofins = Medida(round(contrib[ano], 2),
                                  "EFD-Contribuições (M200/M600)", CONFIRMADO)
        if creditos[ano]:
            a.creditos_atuais = Medida(round(creditos[ano], 2),
                                       "EFD-Contribuições (créditos descontados)",
                                       CONFIRMADO)
        if entradas[ano]:
            a.base_creditavel = Medida(round(entradas[ano], 2),
                                       "EFD-Contribuições (entradas C100)", CONFIRMADO)
        if b2b[ano]:
            a.receita_b2b = Medida(round(b2b[ano], 2),
                                   "EFD F600 (base das retenções PJ)", CONFIRMADO)
    dados.completude.append(
        ("EFD-Contribuições", True,
         f"{len(ativos)} competência(s): {min(ativos)} a {max(ativos)}"))


# ── EFD-ICMS/IPI ────────────────────────────────────────────────────────────
def _efd_icms(engaj: Path, dados: Dados) -> None:
    ativos: dict[str, Path] = {}
    for p in sorted((engaj / "raw" / "bx").glob("*.txt")):
        if identificar_tipo(p) != "efd_icms":
            continue
        c = _campos(_linhas(p)[0])
        if len(c) < 4:
            continue
        comp = c[3][4:8] + "." + c[3][2:4]
        retif = c[2].strip() == "1"
        atual = ativos.get(comp)
        if atual is None or (retif, p.name) > (atual[1], atual[0].name):
            ativos[comp] = (p, retif)
    if not ativos:
        dados.completude.append(("EFD-ICMS/IPI", False, "nenhum arquivo no acervo"))
        return
    rec: dict[str, float] = defaultdict(float)
    deb: dict[str, float] = defaultdict(float)
    meses: dict[str, int] = defaultdict(int)
    for comp, (p, _r) in sorted(ativos.items()):
        ano = comp[:4]
        meses[ano] += 1
        for ln in _linhas(p):
            f = _campos(ln)
            if f and f[0] == "E110" and len(f) > 13:
                deb[ano] += _efd_brl(f[1])
                rec[ano] += _efd_brl(f[12])
    for ano in sorted(rec):
        a = _ano(None, dados, ano)
        a.meses_icms = meses[ano]
        a.icms = Medida(round(rec[ano], 2), "EFD-ICMS (E110)", CONFIRMADO)
        a.icms_debitos = Medida(round(deb[ano], 2), "EFD-ICMS (E110)", CONFIRMADO)
    dados.completude.append(
        ("EFD-ICMS/IPI", True,
         f"{len(ativos)} competência(s): {min(ativos)} a {max(ativos)}"))


# ── ECD: ISS, base creditável e receita de reserva ──────────────────────────
_RE_ISS = re.compile(r"\bI\.?\s?S\.?\s?S\b", re.I)
_RE_NAO_CREDITA = re.compile(
    r"SAL[ÁA]RIO|PESSOAL|F[ÉE]RIAS|13|INSS|FGTS|PR[ÓO][ -]?LABORE|ENCARGO|"
    r"VALE |PLANO DE SA[ÚU]DE|ODONTOL|P\.A\.T|TRANSPORTE\b|INDENIZA|HORA EXTRA|"
    r"D\.S\.R|DEPRECIA|AMORTIZ|PROVIS[ÃA]O|IMPOSTO|TRIBUT|TAXA|IPTU|IPVA|IOF|"
    r"JUROS|MULTA|BANC[ÁA]RIA|FINANCEIR|CONTRIBUI[ÇC][ÃA]O|CONSELHO|"
    r"ASSOCIA[ÇC][ÕO]ES|CURSO|EXAMES M[ÉE]DICOS|"
    # deduções da receita (tributos sobre vendas) não são aquisições
    r"DEDU[ÇC][ÃAÕO]|COFINS|\bPIS\b|ICMS|SIMPLES NACIONAL|DEVOLU[ÇC]|"
    # mecânica do CMV: estoque inicial/final não é aquisição do período
    r"ESTOQUE", re.I)

# receitas fora da base do IBS/CBS padrão (regime específico ou não operacional)
_RE_NAO_OPERACIONAL = re.compile(
    r"FINANCEIR|JUROS|DESCONTO|APLICA[ÇC][ÃAÕO]|RENDIMENTO|VARIA[ÇC][ÃAÕO]|"
    r"REVERS[ÃA]O|GANHO|SUBVEN[ÇC]|ALIENA[ÇC]", re.I)


def _ecd(engaj: Path, dados: Dados) -> None:
    ecds: dict[str, Path] = {}
    for p in sorted((engaj / "raw" / "bx").glob("*.txt")):
        if identificar_tipo(p) != "ecd":
            continue
        c = _campos(_linhas(p)[0])
        if len(c) < 4:
            continue
        ano = c[3][4:8]
        if ano not in ecds or p.name > ecds[ano].name:
            ecds[ano] = p
    if not ecds:
        dados.completude.append(("ECD", False, "nenhum arquivo no acervo"))
        return
    for ano, p in sorted(ecds.items()):
        linhas = _linhas(p)
        plano: dict[str, tuple[str, str, str, str]] = {}
        for ln in linhas:
            f = _campos(ln)
            if f and f[0] == "I050" and len(f) > 7:
                plano[f[5].strip()] = ((f[7] or "").strip(), f[2].strip(),
                                       f[4].strip(), (f[6] or "").strip())

        def contexto(cod: str) -> str:
            """Nome da conta + do pai direto.

            Deliberadamente NÃO sobe até a raiz: planos de conta costumam ter
            um sintético de topo tipo 'CUSTOS, DESPESAS E IMPOSTOS', cuja
            palavra contaminaria a classificação de todos os descendentes.
            """
            nome, _nat, _niv, sup = plano.get(cod, ("", "", "", ""))
            return f"{nome} {plano.get(sup, ('', '', '', ''))[0]}"

        b350 = [ln for ln in linhas if ln.startswith("|I350|")]
        ultimo350 = b350[-1] if b350 else ""
        dentro = not b350
        iss = credit = receita = 0.0
        componentes: list[tuple[str, float]] = []
        for ln in linhas:
            f = _campos(ln)
            if not f:
                continue
            if f[0] == "I350":
                dentro = (ln == ultimo350)
            elif f[0] == "I355" and dentro and len(f) > 4:
                cod = f[1].strip()
                nome = plano.get(cod, ("", "", "", ""))[0]
                v = abs(_efd_brl(f[3]))
                credora = f[4].strip() == "C"
                if credora:
                    # só receita operacional entra na base do IBS/CBS: resultado
                    # financeiro tem regime específico (LC 214/2025, arts. 182+)
                    if not _RE_NAO_OPERACIONAL.search(contexto(cod)):
                        receita += v
                    continue
                if _RE_ISS.search(nome):
                    iss += v
                elif not _RE_NAO_CREDITA.search(contexto(cod)):
                    credit += v
                    componentes.append((nome[:44], round(v, 2)))
        a = _ano(None, dados, ano)
        if componentes:
            a.creditavel_top = sorted(componentes, key=lambda x: -x[1])[:8]
        if iss:
            a.iss = Medida(round(iss, 2), "ECD (conta de dedução ISS)", CONFIRMADO)
        if credit and not a.base_creditavel:
            a.base_creditavel = Medida(
                round(credit, 2), "ECD (despesas/custos elegíveis a crédito)", ESTIMADO)
        if receita:
            a.receita_ecd = Medida(round(receita, 2), "ECD (contas de receita)",
                                   CONFIRMADO)
            if not a.receita:
                a.receita = replace(a.receita_ecd, confianca=INDICIARIO)
    dados.completude.append(("ECD", True, f"exercício(s): {', '.join(sorted(ecds))}"))


# ── ECF: regime tributário ──────────────────────────────────────────────────
def _ecf(engaj: Path, dados: Dados) -> None:
    achou = []
    for p in sorted((engaj / "raw" / "bx").glob("*.txt")):
        if identificar_tipo(p) != "ecf":
            continue
        achou.append(p.name)
        for ln in _linhas(p)[:400]:
            f = _campos(ln)
            if f and f[0] == "0010" and len(f) > 1:
                dados.regime = {"1": "Lucro Real", "2": "Lucro Real/Arbitrado",
                                "3": "Lucro Presumido/Real", "4": "Presumido/Arbitrado",
                                "5": "Lucro Presumido", "6": "Lucro Arbitrado",
                                "7": "Imune do IRPJ", "8": "Isenta do IRPJ"
                                }.get(f[1].strip(), dados.regime)
                break
    dados.completude.append(("ECF", bool(achou),
                             f"{len(achou)} arquivo(s)" if achou else "ausente"))


# ── fallback: tributos pagos no banco de fatos ──────────────────────────────
_COD_PIS_COFINS = ("8109", "6912", "2172", "5856", "5979", "5960")


def _banco(engaj: Path, dados: Dados) -> None:
    caminho = engaj / "engajamento.db"
    if not caminho.exists():
        return
    con = sqlite3.connect(caminho)
    try:
        cur = con.execute(
            "SELECT substr(competencia,1,4) ano, SUM(valor) FROM fatos "
            "WHERE fonte='DARF' AND natureza='PAGO' AND ("
            + " OR ".join("codigo_receita LIKE ?" for _ in _COD_PIS_COFINS)
            + ") GROUP BY 1", tuple(f"{c}%" for c in _COD_PIS_COFINS))
        for ano, total in cur:
            if not ano:
                continue
            a = _ano(None, dados, ano)
            if not a.pis_cofins and total:
                a.pis_cofins = Medida(round(total, 2), "DARF pagos (banco)", INDICIARIO)
    finally:
        con.close()


def coletar(engaj: Path, cnae: str = "") -> Dados:
    dados = Dados(cnae=cnae)
    _efd_contribuicoes(engaj, dados)
    _efd_icms(engaj, dados)
    _ecd(engaj, dados)
    _ecf(engaj, dados)
    _banco(engaj, dados)
    return dados
