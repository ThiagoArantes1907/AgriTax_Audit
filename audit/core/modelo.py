"""Modelo canônico da plataforma AgriTax Audit (decisões D3/D4 da arquitetura).

FatoFiscal — a menor unidade comparável: um valor (escriturado, declarado, pago ou
compensado) de um tributo, numa competência, vindo de uma fonte oficial. Todos os
cruzamentos CR-01..08 são comparações de FatoFiscal sobre a mesma chave.

Achado — uma divergência ou oportunidade já no formato da matriz decisória
(seção 9 do PT-AF-003): referência do procedimento, valores, risco, base legal,
ação proposta e campo de decisão do contador.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum


class Fonte(str, Enum):
    """Canal oficial de onde o dado foi extraído (matriz da seção 2.1)."""

    ECD = "ECD"
    ECF = "ECF"
    EFD_CONTRIBUICOES = "EFD_CONTRIBUICOES"
    EFD_ICMS_IPI = "EFD_ICMS_IPI"
    DCTF = "DCTF"
    DCTFWEB = "DCTFWEB"
    PGDAS_D = "PGDAS_D"
    DEFIS = "DEFIS"
    DARF = "DARF"
    DAS = "DAS"
    PERDCOMP = "PERDCOMP"
    SITUACAO_FISCAL = "SITUACAO_FISCAL"
    DTE = "DTE"
    CADASTRO = "CADASTRO"


class Natureza(str, Enum):
    """Papel do valor no ciclo escriturado → declarado → pago → compensado."""

    ESCRITURADO = "ESCRITURADO"
    DECLARADO = "DECLARADO"
    PAGO = "PAGO"
    COMPENSADO = "COMPENSADO"


@dataclass
class FatoFiscal:
    cnpj: str
    competencia: str            # "AAAA-MM"
    tributo: str                # "PIS", "COFINS", "IRPJ", "CSLL", "CP", "SIMPLES"...
    fonte: Fonte
    natureza: Natureza
    valor: float
    codigo_receita: str = ""    # código DARF/DCTF quando aplicável
    arquivo_origem: str = ""    # nome do arquivo no raw/ (rastreabilidade → custódia)
    detalhes: dict = field(default_factory=dict)

    def chave(self) -> tuple:
        """Chave de conciliação: mesma chave em fontes distintas = mesma obrigação."""
        return (self.cnpj, self.tributo, self.codigo_receita, self.competencia)

    def to_row(self) -> tuple:
        return (
            self.cnpj, self.competencia, self.tributo, self.codigo_receita,
            self.fonte.value, self.natureza.value, self.valor,
            self.arquivo_origem, json.dumps(self.detalhes, ensure_ascii=False),
        )


PRIORIDADES = ("ALTA", "MEDIA", "BAIXA")
DECISOES = ("PENDENTE", "APROVADO", "REJEITADO", "AVALIAR")


@dataclass
class Achado:
    ref: str                    # procedimento de origem: "CR-04", "SN-08", "PE-01"...
    cnpj: str
    titulo: str
    descricao: str = ""
    competencia: str = ""       # vazia quando o achado não é mensal
    tributo: str = ""
    valores: dict = field(default_factory=dict)   # {"escriturado": x, "declarado": y, ...}
    diferenca: float | None = None
    risco: str = ""             # R1..R9 / S1..S8 (audit.core.dominio.RISCOS)
    base_legal: str = ""
    acao_proposta: str = ""
    prioridade: str = "MEDIA"
    decadencia: str = ""        # "AAAA-MM" limite para recuperar/retificar, se houver
    decisao_cliente: str = "PENDENTE"
    justificativa: str = ""

    def __post_init__(self):
        if self.prioridade not in PRIORIDADES:
            raise ValueError(f"prioridade inválida: {self.prioridade}")
        if self.decisao_cliente not in DECISOES:
            raise ValueError(f"decisão inválida: {self.decisao_cliente}")

    def to_row(self) -> tuple:
        return (
            self.ref, self.cnpj, self.competencia, self.tributo, self.titulo,
            self.descricao, json.dumps(self.valores, ensure_ascii=False),
            self.diferenca, self.risco, self.base_legal, self.acao_proposta,
            self.prioridade, self.decadencia, self.decisao_cliente, self.justificativa,
        )

    def to_dict(self) -> dict:
        return asdict(self)


def normaliza_cnpj(cnpj: str) -> str:
    """Somente dígitos; aceita formatado ou não. Não valida DV (dado oficial da RFB)."""
    digitos = "".join(c for c in cnpj if c.isdigit())
    if len(digitos) != 14:
        raise ValueError(f"CNPJ inválido: {cnpj!r}")
    return digitos


def normaliza_competencia(comp: str) -> str:
    """Aceita 'AAAA-MM', 'MM/AAAA' ou 'MMAAAA' e devolve 'AAAA-MM'."""
    c = comp.strip()
    if len(c) == 7 and c[4] == "-":
        ano, mes = c[:4], c[5:7]
    elif len(c) == 7 and c[2] == "/":
        mes, ano = c[:2], c[3:7]
    elif len(c) == 6 and c.isdigit():
        mes, ano = c[:2], c[2:6]
    else:
        raise ValueError(f"competência inválida: {comp!r}")
    if not (ano.isdigit() and mes.isdigit() and 1 <= int(mes) <= 12):
        raise ValueError(f"competência inválida: {comp!r}")
    return f"{ano}-{mes}"
