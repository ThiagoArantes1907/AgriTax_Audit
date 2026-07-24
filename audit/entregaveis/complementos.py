"""Entregáveis complementares da seção 9 do PT-AF-003 (M7 final).

- Mapa de créditos recuperáveis por competência, com prazo decadencial;
- Plano de regularização SEQUENCIADO (retificar escrituração → retificar
  declaração → pagar/parcelar → restituir/compensar);
- Relatório PDF do diagnóstico (metodologia, data-base, achados por risco,
  limitação de escopo padrão).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from audit.core.dominio import RISCOS

# risco → (etapa do plano, ordem) — sequência da seção 9 do programa
_ETAPAS = {
    "R9": ("1. Retificar escrituração", 1),
    "R4": ("1. Retificar escrituração", 1),
    "R5": ("1. Retificar escrituração", 1),
    "S5": ("1. Retificar escrituração", 1),
    "R1": ("2. Retificar declaração", 2),
    "R3": ("2. Retificar declaração", 2),
    "S1": ("2. Retificar declaração", 2),
    "S2": ("2. Retificar declaração", 2),
    "S6": ("2. Retificar declaração", 2),
    "R2": ("3. Pagar/parcelar", 3),
    "S3": ("3. Pagar/parcelar", 3),
    "R8": ("3. Pagar/parcelar", 3),
    "R6": ("3. Pagar/parcelar", 3),
    "R7": ("4. Restituir/compensar", 4),
    "S4": ("4. Restituir/compensar", 4),
    "S7": ("5. Monitorar", 5),
    "S8": ("5. Monitorar", 5),
    "": ("5. Monitorar", 5),
}


def _achados(con: sqlite3.Connection) -> list[dict]:
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM achados ORDER BY competencia, ref")]
    for a in rows:
        a["valores"] = json.loads(a.get("valores") or "{}")
    return rows


def mapa_creditos(con: sqlite3.Connection) -> list[dict]:
    """Créditos/valores recuperáveis (R7/S4) por competência com decadência."""
    mapa = []
    for a in _achados(con):
        if a["risco"] not in ("R7", "S4"):
            continue
        valor = abs(a.get("diferenca") or 0) or max(
            (v for v in a["valores"].values() if isinstance(v, (int, float))),
            default=0)
        mapa.append({
            "competencia": a["competencia"], "tributo": a["tributo"],
            "origem": a["ref"], "titulo": a["titulo"],
            "valor_recuperavel": round(valor, 2),
            "decadencia": a.get("decadencia") or "",
            "via": ("Restituição eletrônica do Simples" if a["ref"].startswith("SN")
                    else "PER/DCOMP"),
        })
    mapa.sort(key=lambda m: (m["decadencia"] or "9999", m["competencia"]))
    return mapa


def plano_regularizacao(con: sqlite3.Connection) -> list[dict]:
    """Achados pendentes ordenados pela sequência segura de regularização."""
    plano = []
    for a in _achados(con):
        if a["decisao_cliente"] == "REJEITADO":
            continue
        etapa, ordem = _ETAPAS.get(a["risco"], ("5. Monitorar", 5))
        plano.append({
            "etapa": etapa, "_ordem": ordem,
            "prioridade": a["prioridade"], "risco": a["risco"],
            "risco_descricao": RISCOS.get(a["risco"], ""),
            "competencia": a["competencia"], "tributo": a["tributo"],
            "origem": a["ref"], "titulo": a["titulo"],
            "acao": a["acao_proposta"],
            "valor": abs(a.get("diferenca") or 0),
        })
    plano.sort(key=lambda p: (p["_ordem"],
                              0 if p["prioridade"] == "ALTA" else 1,
                              -p["valor"]))
    for p in plano:
        p.pop("_ordem")
    return plano


# ── Relatório PDF ─────────────────────────────────────────────────────────────

_LIMITACAO = (
    "Limitação de escopo (permanente): auditoria de consistência e conformidade "
    "declaratória, apoiada exclusivamente nas bases oficiais e-CAC e ReceitaNetBX. "
    "Os testes validam a coerência entre escriturado, declarado e pago e a correção "
    "técnica das apurações; NÃO testam a existência das operações contra "
    "documentos-fonte (XMLs, contratos, laudos). Os saldos do e-CAC mudam "
    "diariamente — as conclusões valem para a data-base das extrações. "
    "A decisão tributária é sempre do contador responsável."
)


def gerar_relatorio_pdf(engaj_dir: str | Path, con: sqlite3.Connection,
                        parametros: dict | None = None) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer,
                                    Table, TableStyle)

    engaj_dir = Path(engaj_dir)
    parametros = parametros or {}
    achados = _achados(con)
    verde = colors.HexColor("#1F6E43")

    destino = engaj_dir / "entregaveis" / "Relatorio_Diagnostico.pdf"
    destino.parent.mkdir(exist_ok=True)
    doc = SimpleDocTemplate(str(destino), pagesize=A4,
                            leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm)
    ss = getSampleStyleSheet()
    titulo = ParagraphStyle("t", parent=ss["Title"], textColor=verde, fontSize=17)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=verde)
    corpo = ParagraphStyle("c", parent=ss["BodyText"], fontSize=9, leading=12)
    mini = ParagraphStyle("m", parent=corpo, fontSize=7.5, textColor=colors.grey)

    manifest_path = engaj_dir / "manifest.json"
    n_arquivos = len(json.loads(manifest_path.read_text(encoding="utf-8"))) \
        if manifest_path.exists() else 0
    data_base = parametros.get("data_base_extracoes") or "—"

    el = [
        Paragraph("AGRITAX AUDIT — DIAGNÓSTICO FISCAL", titulo),
        Paragraph(f"Programa de Trabalho PT-AF-003 · Cliente: "
                  f"{parametros.get('cliente', '—')} · CNPJ: "
                  f"{parametros.get('cnpj', '—')}", corpo),
        Paragraph(f"Emitido em {datetime.now():%d/%m/%Y %H:%M} · Data-base das "
                  f"extrações: {data_base} · {n_arquivos} arquivo(s) sob cadeia "
                  f"de custódia (SHA-256)", mini),
        Spacer(1, 6 * mm),
        Paragraph("1. Metodologia", h2),
        Paragraph(
            "Diagnóstico executado pela plataforma AgriTax Audit sobre as fontes "
            "oficiais: escriturações SPED (ReceitaNetBX) × declarações de débitos "
            "(DCTF/DCTFWeb/PGDAS-D) × pagamentos (DARF/DAS) × compensações "
            "(PER/DCOMP). Procedimentos executados: cruzamentos estruturais "
            "CR-01/04/05/06/08, reperformance RP-02 (ECF) e módulo Simples "
            "SN-01/02/04/11, conforme o PT-AF-003.", corpo),
        Spacer(1, 4 * mm),
        Paragraph("2. Resumo dos achados por risco", h2),
    ]

    grupos: dict = {}
    for a in achados:
        k = a["risco"] or "—"
        g = grupos.setdefault(k, {"n": 0, "soma": 0.0})
        g["n"] += 1
        g["soma"] += abs(a.get("diferenca") or 0)
    dados = [["Risco", "Descrição", "Achados", "Σ valores (R$)"]]
    for k in sorted(grupos):
        dados.append([k, Paragraph(RISCOS.get(k, "Verificações complementares"),
                                   mini), grupos[k]["n"],
                      f"{grupos[k]['soma']:,.2f}"])
    tbl = Table(dados, colWidths=[16 * mm, 96 * mm, 20 * mm, 34 * mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), verde),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    el += [tbl, Spacer(1, 4 * mm),
           Paragraph("3. Principais achados (prioridade ALTA)", h2)]

    altas = [a for a in achados if a["prioridade"] == "ALTA"][:15]
    if not altas:
        el.append(Paragraph("Nenhum achado de prioridade ALTA.", corpo))
    for a in altas:
        el.append(Paragraph(
            f"<b>[{a['ref']}{' · ' + a['risco'] if a['risco'] else ''}] "
            f"{a['titulo']}</b> — {a['descricao'][:420]}", corpo))
        el.append(Spacer(1, 1.5 * mm))

    mapa = mapa_creditos(con)
    if mapa:
        el += [Spacer(1, 3 * mm),
               Paragraph("4. Créditos recuperáveis (com decadência)", h2)]
        dados = [["Competência", "Tributo", "Valor (R$)", "Decadência", "Via"]]
        for m in mapa[:20]:
            dados.append([m["competencia"], m["tributo"],
                          f"{m['valor_recuperavel']:,.2f}",
                          m["decadencia"] or "—", m["via"]])
        t2 = Table(dados, colWidths=[26 * mm, 24 * mm, 34 * mm, 26 * mm, 56 * mm])
        t2.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), verde),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
            ("ALIGN", (2, 1), (2, -1), "RIGHT"),
        ]))
        el.append(t2)

    el += [Spacer(1, 5 * mm), Paragraph("Limitação de escopo", h2),
           Paragraph(_LIMITACAO, mini)]
    doc.build(el)
    return destino
