"""Orquestra a fase 'reforma': coleta → simulação → relatório."""
from __future__ import annotations

from pathlib import Path

from . import coleta, parametros, relatorio, simulador


def simular_engajamento(engaj: Path, params: dict | None = None,
                        cnae: str = "", aliquota: float | None = None,
                        perfil: str = "", ano_base: str = "",
                        gerar_pdf: bool = True) -> dict:
    params = params or {}
    conf = params.get("reforma") or {}
    cnae = cnae or str(conf.get("cnae") or params.get("cnae") or "")

    dados = coleta.coletar(engaj, cnae=cnae)
    if not dados.cnae:
        dados.cnae = cnae

    prem = parametros.Premissas(perfil=parametros.perfil_por_cnae(dados.cnae))
    if perfil:
        if perfil not in parametros.PERFIS:
            raise ValueError(f"perfil desconhecido: {perfil} "
                             f"(use um de {sorted(parametros.PERFIS)})")
        prem.perfil = parametros.PERFIS[perfil]
    ref = aliquota if aliquota is not None else conf.get("aliquota_referencia")
    if ref:
        ref = float(ref)
        proporcao = parametros.CBS_REFERENCIA / parametros.aliquota_referencia()
        prem.cbs, prem.ibs = ref * proporcao, ref * (1 - proporcao)
    if conf.get("aproveitamento_credito"):
        prem.aproveitamento_credito = float(conf["aproveitamento_credito"])

    res = simulador.simular(dados, prem, ano_base=ano_base or str(conf.get("ano_base") or ""))
    if res is None:
        return {"erro": "sem receita identificável no acervo — rode 'estruturar' "
                        "e confira se há EFD/ECD/ECF em raw/bx",
                "completude": dados.completude}

    saida = {
        "ano_base": res.base.ano,
        "receita": res.receita,
        "carga_hoje": res.carga_hoje_valor,
        "carga_hoje_pct": res.carga_hoje_pct,
        "carga_plena": res.pleno.total,
        "carga_plena_pct": res.carga_plena_pct,
        "delta_anual": res.delta_anual,
        "pct_b2b": res.pct_b2b,
        "impacto_economico": res.impacto_economico,
        "perfil": res.premissas.perfil.rotulo,
        "resumo": simulador.resumo_texto(res),
        "alertas": res.alertas,
        "oportunidades": res.oportunidades,
        "completude": dados.completude,
        "projecao": [{"ano": p.ano, "total": p.total,
                      "pct": p.total / res.receita if res.receita else 0.0}
                     for p in res.projecao],
    }
    if gerar_pdf:
        saida["pdf"] = str(relatorio.gerar_pdf(
            engaj, res, cliente=str(params.get("cliente") or "")))
    return saida
