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


import re

_RE_COD_SUB = re.compile(r"(\d{3,4})\s*[-–]\s*(\d{2})\b")
_RE_COD4 = re.compile(r"(\d{4})")


def _norm_codigo(codigo) -> str:
    """Porta o _conc_norm_codigo do v5: preserva o sub-código quando existe.

    '1082-01 - CP Segurados' → '1082-01' | 'COFINS | 2172 - ...' → '2172'
    Os cruzamentos casam pela base de 4 dígitos; o sub-código preservado
    aqui mantém a informação da fonte.
    """
    s = str(codigo or "").strip()
    m = _RE_COD_SUB.search(s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    if re.fullmatch(r"\d{6}", s):        # COD_REC da EFD: "691201" = 6912-01
        return f"{s[:4]}-{s[4:]}"
    m = _RE_COD4.search(s)
    return m.group(1) if m else ""


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
            codigo_receita=_norm_codigo(r.get("codigo")),
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
            codigo_receita=_norm_codigo(r.get("codigo_receita")),
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
            codigo_receita=_norm_codigo(r.get("codigo_receita")),
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
    """PGDAS-D (semântica do triplo v5): cada tributo segregado vira um
    DECLARADO com pseudo-código 'SIMPLES-<TRIB>' — impede casamento indevido
    com DARF/DCTF de outro regime e faz confissão × quitação do Simples
    casarem entre si. O DAS pago é RATEADO entre os tributos na proporção do
    débito (PAGO, fonte DAS, mesmo pseudo-código). Apurações duplicadas
    (mesmo nº de declaração em arquivos distintos) são descartadas."""
    fatos = []
    vistos: set = set()
    for r in rows:
        cnpj = _cnpj(r)
        if not cnpj:
            continue
        comp = r.get("competencia_teste") or r.get("competencia", "")
        chave_ap = (r.get("num_declaracao") or "").strip() or f"{cnpj}|{comp}"
        if chave_ap in vistos:
            continue
        vistos.add(chave_ap)

        total_deb = _num(r.get("total_debito"))
        das_valor = _num(r.get("das_valor_pago"))
        tem_das = bool(r.get("das_pago")) or das_valor > 0
        detalhes_base = {
            "anexo": r.get("anexo", ""),
            "fator_r": r.get("fator_r", ""),
            "rbt12": _num(r.get("rbt12")),
            "rpa": _num(r.get("rpa")),
            "receita_vendas": _num(r.get("receita_vendas")),
            "receita_servicos": _num(r.get("receita_servicos")),
            "num_declaracao": r.get("num_declaracao", ""),
            "aliquota_efetiva": r.get("aliquota_efetiva", ""),
            "total_debito": total_deb,
        }
        for trib in _TRIBUTOS_SIMPLES:
            deb = _num(r.get(trib))
            if deb <= 0:
                continue
            pseudo = f"SIMPLES-{trib.upper()}"
            fatos.append(FatoFiscal(
                cnpj=cnpj, competencia=comp, tributo=trib.upper(),
                fonte=Fonte.PGDAS_D, natureza=Natureza.DECLARADO,
                valor=deb, codigo_receita=pseudo,
                arquivo_origem=arquivo or r.get("_source", ""),
                detalhes=detalhes_base))
            if tem_das and total_deb > 0:
                fatos.append(FatoFiscal(
                    cnpj=cnpj, competencia=comp, tributo=trib.upper(),
                    fonte=Fonte.DAS, natureza=Natureza.PAGO,
                    valor=round(das_valor * deb / total_deb, 2),
                    codigo_receita=pseudo,
                    arquivo_origem=arquivo or r.get("_source", ""),
                    detalhes={"das_numero": r.get("das_numero", ""),
                              "das_dt_pagamento": r.get("das_dt_pagamento", ""),
                              "rateio_de": das_valor}))
    return fatos


def fatos_efd_contribuicoes(rows: list[dict], arquivo: str = "") -> list[FatoFiscal]:
    """Bloco M (M200/M600): contribuição a recolher = o que DEVE constar na
    DCTF (lado escriturado do CR-04)."""
    fatos = []
    for r in rows:
        cnpj = _cnpj(r)
        if not cnpj:
            continue
        # linhas sem código = resumo zerado (competência sem débitos) — entram
        # para registrar cobertura da série e o flag de retificadora (CR-08)
        fatos.append(FatoFiscal(
            cnpj=cnpj,
            competencia=r.get("competencia_teste", ""),
            tributo=str(r.get("tributo", "")).strip(),
            fonte=Fonte.EFD_CONTRIBUICOES,
            natureza=Natureza.ESCRITURADO,
            valor=_num(r.get("contrib_a_recolher")),
            codigo_receita=_norm_codigo(r.get("codigo_receita")),
            arquivo_origem=arquivo or r.get("_source", ""),
            detalhes={
                "regime": r.get("regime", ""),
                "debito_apurado": _num(r.get("debito_apurado")),
                "contrib_periodo": _num(r.get("contrib_periodo")),
                "ded_credito": _num(r.get("ded_credito")),
                "ded_outras": _num(r.get("ded_outras")),
                "retificadora": bool(r.get("retificadora")),
                "num_rec_anterior": r.get("num_rec_anterior", ""),
                "sem_movimento": bool(r.get("sem_movimento")),
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
                codigo_receita=_norm_codigo(r.get("codigo_credito")),
                arquivo_origem=arquivo or r.get("_source", ""),
                detalhes={
                    "codigo_credito": str(r.get("codigo_credito", "")),
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
            codigo_receita=_norm_codigo(r.get("codigo_receita_debito") or r.get("tipo")),
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
