"""Ficha de parametrização do engajamento (Anexo A do PT-AF-003) em YAML.

`engajamentos/<cliente>/<CNPJ>/parametros.yaml` é a fonte da verdade sobre o
escopo: período, regime por exercício, procuração, materialidade e eixos.
O regime seleciona o módulo de reperformance (P6: RP × SN são alternativos;
mudança de regime no período segmenta por exercício).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .dominio import REGIMES
from .modelo import normaliza_cnpj

PARAMETROS = "parametros.yaml"

_TEMPLATE = """\
# Ficha de parametrização do engajamento — Anexo A do PT-AF-003
cliente: "{cliente}"
cnpj: "{cnpj}"
razao_social: ""

# Período sob exame (até 5 anos — arts. 150 §4º e 173 do CTN)
periodo:
  inicio: ""        # AAAA-MM
  fim: ""           # AAAA-MM

# Regime por exercício — seleciona RP (Real/Presumido) ou SN (Simples/SIMEI)
# valores aceitos: LUCRO_REAL | LUCRO_PRESUMIDO | SIMPLES_NACIONAL | SIMEI
regime_por_exercicio: {{}}
  # 2022: SIMPLES_NACIONAL
  # 2023: LUCRO_PRESUMIDO

procuracao_ecac:
  vigente: false
  validade: ""      # AAAA-MM-DD
  todos_servicos: true

materialidade: 0.0  # R$; divergências entre bases oficiais importam mesmo abaixo

# Preenchidos durante a coleta
data_base_extracoes: ""
lacunas_bx: []

# Só para Simples Nacional
simples:
  anexo_atual: ""
  sublimite_estadual: 3600000.0

parecer: ""         # SEM_RESSALVAS | COM_RESSALVAS | ADVERSO (ao final)
"""


def criar_engajamento(base_dir: str | Path, cliente: str, cnpj: str) -> Path:
    """Cria a estrutura engajamentos/<cliente>/<CNPJ>/ com a ficha-template."""
    cnpj = normaliza_cnpj(cnpj)
    engaj = Path(base_dir) / cliente / cnpj
    for sub in ("raw/ecac", "raw/bx", "achados", "entregaveis"):
        (engaj / sub).mkdir(parents=True, exist_ok=True)
    ficha = engaj / PARAMETROS
    if not ficha.exists():
        ficha.write_text(_TEMPLATE.format(cliente=cliente, cnpj=cnpj), encoding="utf-8")
    return engaj


def carregar_parametros(engaj_dir: str | Path) -> dict:
    ficha = Path(engaj_dir) / PARAMETROS
    if not ficha.exists():
        raise FileNotFoundError(f"ficha não encontrada: {ficha}")
    params = yaml.safe_load(ficha.read_text(encoding="utf-8")) or {}
    _validar(params)
    return params


def _validar(params: dict) -> None:
    if not params.get("cnpj"):
        raise ValueError("parametros.yaml sem 'cnpj'")
    normaliza_cnpj(str(params["cnpj"]))
    for exercicio, regime in (params.get("regime_por_exercicio") or {}).items():
        if regime not in REGIMES:
            raise ValueError(
                f"regime inválido em {exercicio}: {regime!r} (use {REGIMES})")


def regimes_do_periodo(params: dict) -> set[str]:
    """Conjunto de regimes no período — define quais módulos rodam (P6)."""
    return set((params.get("regime_por_exercicio") or {}).values())
