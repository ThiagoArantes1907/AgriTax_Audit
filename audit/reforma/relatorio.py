"""Relatório comercial: 'Impacto da Reforma Tributária no seu negócio'.

Peça de diagnóstico/prospecção — números do próprio cliente, projeção ano a
ano, riscos e oportunidades, com as lacunas do acervo declaradas na última
página (qualidade não depende de acervo completo, mas honestidade sim).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (PageBreak, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

from .coleta import LACUNA
from .simulador import Resultado

VERDE = colors.HexColor("#1F6E43")
VERMELHO = colors.HexColor("#C0392B")
AMBAR = colors.HexColor("#B7770D")
CINZA = colors.HexColor("#CCCCCC")
CLARO = colors.HexColor("#E8F3EC")


def _estilos():
    ss = getSampleStyleSheet()
    return {
        "titulo": ParagraphStyle("t", parent=ss["Title"], textColor=VERDE, fontSize=15),
        "destaque": ParagraphStyle("d", parent=ss["Title"], textColor=VERMELHO,
                                   fontSize=13, spaceBefore=2, spaceAfter=2),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], textColor=VERDE),
        "h2r": ParagraphStyle("h2r", parent=ss["Heading2"], textColor=VERMELHO),
        "h2a": ParagraphStyle("h2a", parent=ss["Heading2"], textColor=AMBAR),
        "h3": ParagraphStyle("h3", parent=ss["Heading3"], textColor=VERDE, fontSize=10),
        "corpo": ParagraphStyle("c", parent=ss["BodyText"], fontSize=9.3, leading=12.5),
        "cel": ParagraphStyle("cel", parent=ss["BodyText"], fontSize=7.4, leading=9.4),
        "mini": ParagraphStyle("m", parent=ss["BodyText"], fontSize=7.5,
                               textColor=colors.grey, leading=9.5),
    }


def _tab(dados, larguras, dinheiro=(), destaque_linhas=()):
    t = Table(dados, colWidths=[w * mm for w in larguras])
    e = [("BACKGROUND", (0, 0), (-1, 0), VERDE),
         ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
         ("FONTSIZE", (0, 0), (-1, -1), 7.4),
         ("GRID", (0, 0), (-1, -1), 0.4, CINZA),
         ("VALIGN", (0, 0), (-1, -1), "TOP")]
    for c in dinheiro:
        e.append(("ALIGN", (c, 1), (c, -1), "RIGHT"))
    for ln in destaque_linhas:
        e += [("FONTNAME", (0, ln), (-1, ln), "Helvetica-Bold"),
              ("BACKGROUND", (0, ln), (-1, ln), CLARO)]
    t.setStyle(TableStyle(e))
    return t


def _brl(v: float) -> str:
    return f"{v:,.2f}"


def gerar_pdf(engaj: Path, res: Resultado, cliente: str = "") -> Path:
    st = _estilos()
    d, b, p = res.dados, res.base, res.premissas
    destino = engaj / "entregaveis" / "Reforma_Tributaria_Impacto.pdf"
    destino.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(destino), pagesize=A4, leftMargin=15 * mm,
                            rightMargin=15 * mm, topMargin=13 * mm,
                            bottomMargin=13 * mm)

    nome = cliente or d.nome or "(cliente)"
    sobe = res.delta_anual > 0
    el = [
        Paragraph("IMPACTO DA REFORMA TRIBUTÁRIA NO SEU NEGÓCIO", st["titulo"]),
        Paragraph(f"{nome} · CNPJ {d.cnpj or '—'}"
                  + (f" · {d.uf}" if d.uf else "")
                  + (f" · {d.regime}" if d.regime else "")
                  + (f" · CNAE {d.cnae}" if d.cnae else ""), st["corpo"]),
        Paragraph(f"Simulação AgriTax Audit emitida em {datetime.now():%d/%m/%Y} · "
                  f"EC 132/2023 e LC 214/2025 · base de cálculo: exercício "
                  f"{b.ano} (dados reais das suas escriturações)", st["mini"]),
        Spacer(1, 4 * mm),
    ]

    # ── o número que interessa ──
    seta = "sobe" if sobe else "cai"
    cor = st["destaque"] if sobe else ParagraphStyle(
        "dv", parent=st["destaque"], textColor=VERDE)
    el += [
        Paragraph(f"Sua carga sobre consumo {seta} de "
                  f"{res.carga_hoje_pct*100:.1f}% para "
                  f"{res.carga_plena_pct*100:.1f}% da receita", cor),
        Paragraph(f"Diferença de <b>R$ {_brl(abs(res.delta_anual))} por ano</b> "
                  f"a preços de hoje, quando o modelo estiver pleno (2033)."
                  + (f" Considerando que {res.pct_b2b*100:.0f}% do seu "
                     f"faturamento vai para empresas que tomarão crédito, o "
                     f"impacto econômico direto estimado é de "
                     f"<b>R$ {_brl(abs(res.impacto_economico))}/ano</b>."
                     if b.receita_b2b else ""), st["corpo"]),
        Spacer(1, 3 * mm),

        Paragraph("1. COMO ESTÁ HOJE — E COMO FICA", st["h2"]),
    ]
    dados = [["Tributo", f"Hoje ({b.ano})", "% da receita", "No modelo pleno (2033)"],
             ["PIS/COFINS", _brl(b.pis_cofins.valor),
              f"{b.pis_cofins.valor/res.receita*100:.2f}%", "extintos em 2027"],
             ["ICMS", _brl(b.icms.valor),
              f"{b.icms.valor/res.receita*100:.2f}%", "extinto em 2033"],
             ["ISS", _brl(b.iss.valor),
              f"{b.iss.valor/res.receita*100:.2f}%", "extinto em 2033"],
             ["CBS + IBS (débito)", "—", "—", _brl(res.pleno.debito_novo)],
             ["(−) créditos do novo modelo", "—", "—",
              "(" + _brl(res.pleno.credito_novo) + ")"],
             ["TOTAL", _brl(res.carga_hoje_valor),
              f"{res.carga_hoje_pct*100:.2f}%", _brl(res.pleno.total)]]
    el += [_tab(dados, [46, 34, 26, 44], (1, 2, 3), destaque_linhas=(6,)),
           Paragraph(f"Receita base: R$ {_brl(res.receita)} ({b.receita.fonte}). "
                     f"Alíquota de referência considerada: "
                     f"{p.referencia*100:.1f}% "
                     f"(CBS {p.cbs*100:.1f}% + IBS {p.ibs*100:.1f}%)"
                     + (f", reduzida para {p.efetiva*100:.2f}% pelo regime "
                        f"diferenciado aplicável." if p.perfil.redutor else "."),
                     st["mini"]),
           Spacer(1, 3 * mm),

           Paragraph("2. A TRANSIÇÃO ANO A ANO", st["h2"])]
    dados = [["Ano", "PIS/COFINS", "ICMS/ISS", "CBS+IBS líquido", "Total",
              "% receita", "O que acontece"]]
    for pr in res.projecao:
        dados.append([str(pr.ano), _brl(pr.legado_pis_cofins),
                      _brl(pr.legado_icms_iss), _brl(pr.novo_liquido),
                      _brl(pr.total), f"{pr.total/res.receita*100:.1f}%",
                      Paragraph(pr.nota, st["cel"])])
    el += [_tab(dados, [11, 24, 22, 26, 24, 15, 58], (1, 2, 3, 4, 5),
                destaque_linhas=(len(dados) - 1,)),
           Paragraph("Valores a preços de hoje (receita constante) para isolar o "
                     "efeito tributário. 2026 é ano-teste: CBS de 0,9% e IBS de "
                     "0,1% são compensados com o PIS/COFINS devido, por isso o "
                     "efeito no caixa é praticamente nulo — mas a obrigação "
                     "acessória já vale, e quem não cumprir perde a compensação.",
                     st["mini"]),
           ]

    # ── cenários (quando o enquadramento é decisivo e ainda indiciário) ──
    if res.alternativo is not None:
        alt = res.alternativo
        el += [Spacer(1, 3 * mm),
               Paragraph("2.1 — DOIS CENÁRIOS: a decisão mais valiosa da sua "
                         "transição", st["h2a"]),
               Paragraph(f"O enquadramento em <b>{p.perfil.rotulo.lower()}</b> "
                         f"ainda depende de confirmação documental, e ele muda o "
                         f"resultado completamente:", st["corpo"])]
        dados = [["Cenário", "Alíquota", "Carga no regime pleno", "% da receita",
                  "Variação vs hoje"],
                 [Paragraph(f"COM o regime diferenciado<br/>({p.perfil.rotulo})",
                            st["cel"]),
                  f"{p.efetiva*100:.2f}%", _brl(res.pleno.total),
                  f"{res.carga_plena_pct*100:.2f}%",
                  ("+" if res.delta_anual > 0 else "") + _brl(res.delta_anual)],
                 [Paragraph("SEM o regime diferenciado<br/>(alíquota cheia)",
                            st["cel"]),
                  f"{alt.premissas.efetiva*100:.2f}%", _brl(alt.pleno.total),
                  f"{alt.carga_plena_pct*100:.2f}%",
                  ("+" if alt.delta_anual > 0 else "") + _brl(alt.delta_anual)],
                 ["DIFERENÇA ENTRE OS CENÁRIOS", "—",
                  _brl(alt.pleno.total - res.pleno.total),
                  f"{(alt.carga_plena_pct-res.carga_plena_pct)*100:.2f} p.p.",
                  "por ano"]]
        el += [_tab(dados, [50, 22, 36, 26, 32], (2, 3, 4),
                    destaque_linhas=(3,)),
               Paragraph(p.perfil.observacao or "", st["mini"])]

    el += [PageBreak()]

    # ── riscos ──
    el += [Paragraph("3. O QUE EXIGE DECISÃO SUA (riscos)", st["h2r"])]
    for i, a in enumerate(res.alertas, 1):
        el.append(Paragraph(f"<b>{i}.</b> {a}", st["corpo"]))
    el += [Spacer(1, 2 * mm),
           Paragraph("4. ONDE ESTÁ O DINHEIRO (oportunidades)", st["h2"])]
    for i, o in enumerate(res.oportunidades, 1):
        el.append(Paragraph(f"<b>{i}.</b> {o}", st["corpo"]))

    # ── plano ──
    el += [Spacer(1, 3 * mm),
           Paragraph("5. PLANO DE TRANSIÇÃO — O QUE FAZER E QUANDO", st["h2a"])]
    plano = [["Prazo", "Ação", "Por quê"],
             ["Agora (2026)",
              Paragraph("Cumprir a obrigação acessória do ano-teste (destaque de "
                        "CBS/IBS nos documentos fiscais)", st["cel"]),
              Paragraph("Sem o cumprimento, perde-se a compensação e nasce "
                        "passivo com efeito zero de caixa hoje", st["cel"])],
             ["Agora (2026)",
              Paragraph("Segregar o faturamento por tipo de adquirente (PJ "
                        "contribuinte, Simples, PF/consumidor final, exportação)",
                        st["cel"]),
              Paragraph("É o que define quanto do aumento é repassável — a "
                        "conta muda completamente conforme esse mix", st["cel"])],
             ["2026",
              Paragraph("Revisar contratos de prazo longo: cláusula de revisão "
                        "por alteração tributária e preço 'por dentro'", st["cel"]),
              Paragraph("Contratos fechados antes de 2027 sem cláusula "
                        "transferem todo o aumento para a sua margem", st["cel"])],
             ["2026–2027",
              Paragraph("Mapear fornecedores que NÃO geram crédito cheio "
                        "(Simples, produtor rural PF, MEI, informais)", st["cel"]),
              Paragraph("No novo modelo, comprar de quem não dá crédito passa a "
                        "custar mais caro — renegociar ou trocar", st["cel"])],
             ["2026–2027",
              Paragraph("Revisar o cronograma de CAPEX", st["cel"]),
              Paragraph("Crédito integral e imediato sobre imobilizado a partir "
                        "da CBS (2027) muda o melhor momento de investir",
                        st["cel"])],
             ["2027",
              Paragraph("Reprogramar sistemas, layout de notas e apuração; "
                        "simular o split payment no fluxo de caixa", st["cel"])
              ,
              Paragraph("O recolhimento passa a ocorrer na liquidação "
                        "financeira — o giro financiado pelo prazo acaba",
                        st["cel"])],
             ["Contínuo",
              Paragraph("Reavaliar o enquadramento em regimes diferenciados a "
                        "cada linha de receita", st["cel"]),
              Paragraph("O enquadramento é por operação (NBS/NCM); receitas "
                        "acessórias podem ter tratamento próprio", st["cel"])]]
    el += [_tab(plano, [22, 74, 74])]

    # ── qualidade / completude ──
    el += [PageBreak(),
           Paragraph("6. BASE DA SIMULAÇÃO — O QUE FOI USADO E O QUE FALTA", st["h2"]),
           Paragraph("Transparência é parte do método: cada número abaixo indica "
                     "de onde veio e com que grau de certeza. Onde há lacuna, "
                     "dizemos o que falta e como isso muda o resultado — em vez "
                     "de omitir a análise.", st["corpo"])]
    medidas = [("Receita bruta", b.receita), ("PIS/COFINS devidos", b.pis_cofins),
               ("ICMS a recolher", b.icms), ("ISS", b.iss),
               ("Base creditável (aquisições/despesas)", b.base_creditavel),
               ("Créditos já aproveitados hoje", b.creditos_atuais),
               ("Receita faturada contra PJ", b.receita_b2b)]
    dados = [["Medida", "Valor (R$)", "Fonte", "Confiança"]]
    for rotulo, m in medidas:
        dados.append([rotulo, _brl(m.valor) if m else "—",
                      Paragraph(m.fonte or "não localizado no acervo", st["cel"]),
                      "lacuna" if m.confianca == LACUNA else m.confianca])
    el += [_tab(dados, [50, 26, 66, 28], (1,))]

    if b.creditavel_top:
        el += [Spacer(1, 2 * mm),
               Paragraph("Composição da base de crédito identificada", st["h3"]),
               Paragraph("São as maiores contas de custo/despesa que passam a "
                         "gerar crédito no novo modelo. Confira se alguma vem de "
                         "fornecedor fora do regime regular (Simples, MEI, pessoa "
                         "física) — nesses casos o crédito é limitado ou "
                         "inexistente:", st["corpo"])]
        dados = [["Conta (ECD)", "Valor no ano (R$)"]]
        for nome, valor in b.creditavel_top:
            dados.append([nome, _brl(valor)])
        el += [_tab(dados, [110, 34], (1,))]

    dados = [["Fonte de dados", "No acervo?", "Cobertura"]]
    for fonte, ok, detalhe in d.completude:
        dados.append([fonte, "sim" if ok else "NÃO", Paragraph(detalhe, st["cel"])])
    el += [Spacer(1, 2 * mm), _tab(dados, [40, 20, 110]),
           Spacer(1, 2 * mm),
           Paragraph("Premissas e limitações", st["h3"]),
           Paragraph(f"• Alíquota de referência de {p.referencia*100:.1f}% "
                     f"(CBS {p.cbs*100:.1f}% + IBS {p.ibs*100:.1f}%): estimativa "
                     f"do Ministério da Fazenda — a alíquota definitiva será "
                     f"fixada por lei/resolução do Senado e pode variar;<br/>"
                     f"• Receita mantida constante a preços de hoje, para isolar "
                     f"o efeito tributário do efeito de crescimento;<br/>"
                     f"• Créditos calculados sobre a base identificada nas suas "
                     f"escriturações, com aproveitamento de "
                     f"{p.aproveitamento_credito*100:.0f}% (conservador: nem todo "
                     f"fornecedor estará no regime regular);<br/>"
                     f"• Regimes específicos (financeiro, imóveis, combustíveis, "
                     f"cooperativas, hotelaria, bares e restaurantes) têm regras "
                     f"próprias e exigem análise dedicada;<br/>"
                     f"• Esta simulação é diagnóstica e não substitui o "
                     f"planejamento detalhado da transição.", st["corpo"]),
           Spacer(1, 3 * mm),
           Paragraph("AgriTax Tributário &amp; Contábil — diagnóstico gerado pela "
                     "plataforma AgriTax Audit a partir das escriturações e "
                     "documentos fiscais oficiais do contribuinte.", st["mini"])]

    doc.build(el)
    return destino
