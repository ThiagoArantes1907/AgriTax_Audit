"""Adaptadores: linhas dos parsers do AgriTax Audit v5 → FatoFiscal (CB-06).

Cada parser mantém sua saída original (compatível com a GUI v5 e com os
motores de conciliação a absorver no M2). Aqui essas linhas são traduzidas
para o modelo canônico e gravadas no SQLite do engajamento.

Convenções:
- competencia = código canônico do v5 ("AAAA.MM", "AAAA.NT" ou "AAAA"),
  o mesmo usado pelos motores de cruzamento (competencia_teste);
- codigo_receita normalizado para os 4 dígitos base (join do CR-04/05);
- valores monetários aceitam float ou string BRL ("1.234,56").
"""
from __future__ import annotations

from audit.core.modelo import FatoFiscal, Fonte, Natureza, normaliza_cnpj

from ._util import parse_brl


def _num(v) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(parse_brl(str(v)))
    except (ValueError, TypeError):
        return 0.0


def _cnpj(row: dict) -> str:
    try:
        return normaliza_cnpj(str(row.get("cnpj", "")))
    except ValueError:
        return ""


def _cod4(codigo) -> str:
    """'6912-01' / '6912 01' / '6912' → '6912' (base de join do CR-04/05)."""
    digitos = "".join(c for c in str(codigo or "") if c.isdigit())
    return digitos[:4]


def fatos_darf(rows: list[dict], arquivo: str = "") -> list[FatoFiscal]:
    """Pagamentos (DARF/DAS): natureza PAGO; valor = principal (baixa do débito);
    multa/juros/total preservados em detalhes."""
    fatos = []
    for r in rows:
        cnpj = _cnpj(r)
        if not cnpj or not r.get("codigo"):
            continue
        eh_das = "DAS" in str(r.get("tipo_doc", "")).upper()
        fatos.append(FatoFiscal(
            cnpj=cnpj,
            competencia=r.get("competencia_teste", ""),
            tributo=str(r.get("descricao", "")).strip(),
            fonte=Fonte.DAS if eh_das else Fonte.DARF,
            natureza=Natureza.PAGO,
            valor=_num(r.get("principal")),
            codigo_receita=_cod4(r.get("codigo")),
            arquivo_origem=arquivo or r.get("_source", ""),
            detalhes={
                "numero_doc": r.get("numero_doc", ""),
                "dt_arrecadacao": r.get("dt_arrecadacao", ""),
                "periodo": r.get("periodo", ""),
                "multa": _num(r.get("multa")),
                "juros": _num(r.get("juros")),
                "total_item": _num(r.get("total_item")),
            }))
    return fatos


def fatos_dctf(rows: list[dict], arquivo: str = "") -> list[FatoFiscal]:
    """Débitos confessados na DCTF clássica: natureza DECLARADO."""
    fatos = []
    for r in rows:
        cnpj = _cnpj(r)
        if not cnpj or not r.get("codigo_receita"):
            continue
        fatos.append(FatoFiscal(
            cnpj=cnpj,
            competencia=r.get("competencia_teste", ""),
            tributo=str(r.get("grupo_tributo", "")).strip(),
            fonte=Fonte.DCTF,
            natureza=Natureza.DECLARADO,
            valor=_num(r.get("debito_apurado")),
            codigo_receita=_cod4(r.get("codigo_receita")),
            arquivo_origem=arquivo or r.get("_source", ""),
            detalhes={
                "numero_declaracao": r.get("numero_declaracao", ""),
                "retificadora": r.get("retificadora", ""),
                "periodo_apuracao": r.get("periodo_apuracao", ""),
                "credito_pagamento": _num(r.get("credito_pagamento")),
                "credito_compensacoes": _num(r.get("credito_compensacoes")),
                "credito_parcelamento": _num(r.get("credito_parcelamento")),
                "credito_suspensao": _num(r.get("credito_suspensao")),
                "saldo_pagar": _num(r.get("saldo_pagar")),
            }))
    return fatos


def fatos_dctfweb(rows: list[dict], arquivo: str = "") -> list[FatoFiscal]:
    """Débitos da DCTFWeb (previdenciário/IRRF): natureza DECLARADO."""
    fatos = []
    for r in rows:
        cnpj = _cnpj(r)
        if not cnpj or not r.get("codigo_receita"):
            continue
        fatos.append(FatoFiscal(
            cnpj=cnpj,
            competencia=r.get("competencia_teste", ""),
            tributo=str(r.get("grupo_tributo", "") or r.get("descricao", "")).strip(),
            fonte=Fonte.DCTFWEB,
            natureza=Natureza.DECLARADO,
            valor=_num(r.get("debito_apurado")),
            codigo_receita=_cod4(r.get("codigo_receita")),
            arquivo_origem=arquivo or r.get("_source", ""),
            detalhes={
                "categoria": r.get("categoria", ""),
                "numero_recibo": r.get("numero_recibo", ""),
                "retificadora": r.get("retificadora", ""),
                "cred_pagamento": _num(r.get("cred_pagamento")),
                "cred_compensacao": _num(r.get("cred_compensacao")),
                "cred_suspensao": _num(r.get("cred_suspensao")),
                "saldo_pagar": _num(r.get("saldo_pagar")),
                "ausencia_fatos": r.get("ausencia_fatos", ""),
            }))
    return fatos


# PGDAS-D: colunas de tributo → nome canônico
_TRIBUTOS_SIMPLES = ("irpj", "csll", "cofins", "pis", "cpp", "icms", "ipi", "iss")


def fatos_simples(rows: list[dict], arquivo: str = "") -> list[FatoFiscal]:
    """PGDAS-D: um DECLARADO por tributo segregado no DAS + um PAGO quando há
    DAS pago. O detalhamento (anexo, fator r, RBT12) alimenta SN-01..04."""
    fatos = []
    for r in rows:
        cnpj = _cnpj(r)
        if not cnpj:
            continue
        comp = r.get("competencia_teste") or r.get("competencia", "")
        detalhes_base = {
            "anexo": r.get("anexo", ""),
            "fator_r": r.get("fator_r", ""),
            "rbt12": _num(r.get("rbt12")),
            "rpa": _num(r.get("rpa")),
            "receita_vendas": _num(r.get("receita_vendas")),
            "receita_servicos": _num(r.get("receita_servicos")),
            "num_declaracao": r.get("num_declaracao", ""),
            "aliquota_efetiva": r.get("aliquota_efetiva", ""),
        }
        for trib in _TRIBUTOS_SIMPLES:
            valor = _num(r.get(trib))
            if valor:
                fatos.append(FatoFiscal(
                    cnpj=cnpj, competencia=comp, tributo=trib.upper(),
                    fonte=Fonte.PGDAS_D, natureza=Natureza.DECLARADO,
                    valor=valor, arquivo_origem=arquivo or r.get("_source", ""),
                    detalhes=detalhes_base))
        das_pago = _num(r.get("das_valor_pago"))
        if das_pago:
            fatos.append(FatoFiscal(
                cnpj=cnpj, competencia=comp, tributo="SIMPLES_DAS",
                fonte=Fonte.DAS, natureza=Natureza.PAGO,
                valor=das_pago, arquivo_origem=arquivo or r.get("_source", ""),
                detalhes={"das_numero": r.get("das_numero", ""),
                          "das_dt_pagamento": r.get("das_dt_pagamento", ""),
                          "total_debito": _num(r.get("total_debito"))}))
    return fatos


def fatos_efd_contribuicoes(rows: list[dict], arquivo: str = "") -> list[FatoFiscal]:
    """Bloco M (M200/M600): contribuição a recolher = o que DEVE constar na
    DCTF (lado escriturado do CR-04)."""
    fatos = []
    for r in rows:
        cnpj = _cnpj(r)
        if not cnpj or not r.get("codigo_receita"):
            continue
        fatos.append(FatoFiscal(
            cnpj=cnpj,
            competencia=r.get("competencia_teste", ""),
            tributo=str(r.get("tributo", "")).strip(),
            fonte=Fonte.EFD_CONTRIBUICOES,
            natureza=Natureza.ESCRITURADO,
            valor=_num(r.get("contrib_a_recolher")),
            codigo_receita=_cod4(r.get("codigo_receita")),
            arquivo_origem=arquivo or r.get("_source", ""),
            detalhes={
                "regime": r.get("regime", ""),
                "debito_apurado": _num(r.get("debito_apurado")),
                "contrib_periodo": _num(r.get("contrib_periodo")),
                "ded_credito": _num(r.get("ded_credito")),
                "ded_outras": _num(r.get("ded_outras")),
            }))
    return fatos


def fatos_perdcomp(rows: list[dict], arquivo: str = "") -> list[FatoFiscal]:
    """PER/DCOMP: débitos compensados → COMPENSADO (quitação sem caixa, CR-05);
    créditos pedidos → PLEITEADO (pedido × lastro escriturado, CR-06)."""
    fatos = []
    for r in rows:
        cnpj = _cnpj(r)
        if not cnpj:
            continue
        if r.get("tipo_registro") == "Crédito":
            fatos.append(FatoFiscal(
                cnpj=cnpj,
                competencia=r.get("competencia_teste", ""),
                tributo=str(r.get("tipo", "")).strip(),
                fonte=Fonte.PERDCOMP,
                natureza=Natureza.PLEITEADO,
                valor=_num(r.get("valor_original")),
                codigo_receita=_cod4(r.get("codigo_credito")),
                arquivo_origem=arquivo or r.get("_source", ""),
                detalhes={
                    "numero_perdcomp": r.get("numero_perdcomp", ""),
                    "tipo_pedido": r.get("tipo_pedido", ""),
                    "data_transmissao": r.get("data_transmissao", ""),
                    "periodo_apuracao": r.get("periodo_apuracao", ""),
                    "valor_utilizado": _num(r.get("valor_utilizado")),
                    "numero_pedido_vinculado": r.get("numero_pedido_vinculado", ""),
                    "retificador": r.get("retificador", ""),
                }))
            continue
        if r.get("tipo_registro") != "Débito":
            continue
        fatos.append(FatoFiscal(
            cnpj=cnpj,
            competencia=r.get("competencia_teste", ""),
            tributo=str(r.get("tipo", "")).strip(),
            fonte=Fonte.PERDCOMP,
            natureza=Natureza.COMPENSADO,
            valor=_num(r.get("valor_total")) or _num(r.get("valor_original")),
            codigo_receita=_cod4(r.get("codigo_receita_debito")),
            arquivo_origem=arquivo or r.get("_source", ""),
            detalhes={
                "numero_perdcomp": r.get("numero_perdcomp", ""),
                "tipo_pedido": r.get("tipo_pedido", ""),
                "data_transmissao": r.get("data_transmissao", ""),
                "valor_principal": _num(r.get("valor_original")),
                "valor_multa": _num(r.get("valor_multa")),
                "valor_juros": _num(r.get("valor_juros")),
                "numero_pedido_vinculado": r.get("numero_pedido_vinculado", ""),
            }))
    return fatos
