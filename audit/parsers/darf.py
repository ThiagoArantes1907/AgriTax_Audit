"""Parser de DARF/DAS (comprovantes de arrecadação do e-CAC).

Extraído do AgriTax Audit v5 consolidado (agritax_audit_consolidado.py),
sem alterações de lógica — apenas modularização (M1 da arquitetura).
"""
import re
from pathlib import Path

import pdfplumber

from ._util import format_competencia_teste, parse_brl

DARF_COLS = [
    ("cnpj",           "CNPJ",               120),
    ("razao_social",   "Razão Social",        210),
    ("tipo_doc",       "Tipo",                 50),
    ("numero_doc",     "Nº Documento",        165),
    ("periodo",        "Período/Competência", 130),
    ("competencia_teste", "Competência Teste", 120),
    ("dt_vencimento",  "Dt. Vencimento",       98),
    ("dt_arrecadacao", "Dt. Arrecadação",      98),
    ("banco",          "Banco",               200),   # ← banco logo após datas
    ("agencia",        "Agência",              65),
    ("estabelecimento","Estabelecimento",       90),
    ("referencia",     "Referência",           100),
    ("codigo",         "Código",               55),
    ("descricao",      "Descrição",            250),
    ("principal",      "Principal",             88),
    ("multa",          "Multa",                 78),
    ("juros",          "Juros",                 78),
    ("total_item",     "Total Item",            88),
    ("total_doc",      "Total Documento",      108),
    ("_source",        "Arquivo PDF",          195),
]
DARF_KEYS = [c[0] for c in DARF_COLS]
DARF_MONEY_KEYS = {"principal", "multa", "juros", "total_item", "total_doc"}


def parse_darf_pdf(path: str) -> list:
    """
    Lê um PDF de comprovantes de arrecadação (DARF e/ou DAS).
    Retorna lista de dicts, um por linha de composição
    (headers repetidos para cada linha do documento).
    Documentos multi-página são consolidados pelo Número do Documento.
    """
    import pdfplumber, re

    def _first(pat, txt, default=""):
        m = re.search(pat, txt)
        return m.group(1).strip() if m else default

    def _brl(v):
        if not v or v.strip() in ("-", ""):
            return ""
        v = re.sub(r"[R$\s]", "", v.strip()).replace(".", "").replace(",", ".")
        try:
            return f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except Exception:
            return v

    # ── 1. Agrupa páginas por Número do Documento ───────────────────────────
    doc_map: dict = {}   # {numero_doc: {meta, items: []}}

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            txt = page.extract_text(x_tolerance=3, y_tolerance=3) or ""

            # Tipo (DARF ou DAS)
            if "arrecadação de DAS" in txt:
                tipo = "DAS"
            else:
                tipo = "DARF"

            # Número do documento (chave de agrupamento)
            num = _first(r"Número do Documento\s*\n?\s*(\d{17})", txt) or \
                  _first(r"(\d{17})", txt)
            if not num:
                continue

            if num not in doc_map:
                # Extrai cabeçalho
                cnpj_rs = re.search(
                    r"(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})\s+(.+?)(?:\n|$)", txt)
                cnpj = cnpj_rs.group(1).strip() if cnpj_rs else ""
                razao = cnpj_rs.group(2).strip() if cnpj_rs else ""

                # Período/Competência e Data de Vencimento
                # PDF layout: "DD/MM/AAAA  DD/MM/AAAA  NNNNNNNNNNNNNNNNN"
                # DAS layout: "MM/AAAA  DD/MM/AAAA  NNNNNNNNNNNNNNNNN"
                datas_m = re.search(
                    r"(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+\d{17}", txt)
                comp_m  = re.search(
                    r"(\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+\d{17}", txt)
                if datas_m:
                    periodo = datas_m.group(1)
                    dt_venc = datas_m.group(2)
                elif comp_m:
                    periodo = comp_m.group(1)
                    dt_venc = comp_m.group(2)
                else:
                    all_dates = re.findall(r"\d{2}/\d{2}/\d{4}", txt)
                    periodo = all_dates[0] if len(all_dates) > 0 else ""
                    dt_venc = all_dates[1] if len(all_dates) > 1 else ""

                # Banco e data de arrecadação
                # Trata "341 - BANCO ITAU S A 30/12/2024" e
                # "748 - BANCO COOPERATIVO SICREDI S/A - 29/11/2024"
                banco_m = re.search(
                    r"(\d{3})\s*-\s*([A-ZÀ-Ú][^\n]+?)\s+(\d{2}/\d{2}/\d{4})", txt)
                if banco_m:
                    # Remove " -" ou " S/A -" solto no final do nome
                    nome = re.sub(r"\s+-\s*$", "", banco_m.group(2).strip()).strip()
                    banco    = f"{banco_m.group(1)} - {nome}"
                    dt_arrec = banco_m.group(3)
                else:
                    pix_m = re.search(
                        r"(Documento pago via PIX)\s+(\d{2}/\d{2}/\d{4})", txt)
                    if pix_m:
                        banco    = pix_m.group(1)
                        dt_arrec = pix_m.group(2)
                    else:
                        banco    = ""
                        dt_arrec = ""

                # Agência / Estabelecimento / Referência
                # Linha dos valores: "0322  0671  0,00  10180218"
                # tokens: [agencia, estab, valor_reservado, referencia?]
                agencia = estab = ref = ""
                agencia_m = re.search(
                    r"Agência\s*Estabelecimento[^\n]*\n([^\n]+)", txt)
                if agencia_m:
                    tokens = agencia_m.group(1).split()
                    agencia = tokens[0] if len(tokens) > 0 else ""
                    estab   = tokens[1] if len(tokens) > 1 else ""
                    ref     = tokens[3] if len(tokens) > 3 else ""  # token 3 = referência

                doc_map[num] = {
                    "cnpj": cnpj, "razao_social": razao,
                    "tipo_doc": tipo, "numero_doc": num,
                    "periodo": periodo, "dt_vencimento": dt_venc,
                    "dt_arrecadacao": dt_arrec, "banco": banco,
                    "agencia": agencia, "estabelecimento": estab,
                    "referencia": ref, "total_doc": "",
                    "items": [],
                }

            meta = doc_map[num]

            # Atualiza campos bancários se estavam vazios na 1ª página
            # (cobre documentos multi-página onde o banco só aparece em página posterior)
            if not meta["banco"] and banco:
                meta["banco"] = banco
                meta["dt_arrecadacao"] = dt_arrec
            if not meta["agencia"] and agencia:
                meta["agencia"] = agencia
            if not meta["estabelecimento"] and estab:
                meta["estabelecimento"] = estab
            if not meta["referencia"] and ref:
                meta["referencia"] = ref

            # ── 2. Extrai itens da composição ─────────────────────────────
            in_comp = False
            lines = txt.split("\n")
            # Loop indexado: a linha de SUB-CÓDIGO ("01 - ISS - SIMPLES...")
            # vem logo APÓS a linha do item, então precisamos olhar adiante.
            for _li in range(len(lines)):
                line = lines[_li].strip()
                if "Composição do Documento" in line:
                    in_comp = True
                    continue
                if not in_comp:
                    continue
                if line.startswith("Totais"):
                    # Extrai total geral
                    vals = re.findall(r"[\d\.]+,\d{2}", line)
                    if vals:
                        meta["total_doc"] = _brl(vals[-1].replace(".", "").replace(",", ".").replace(".", "").replace(",", "."))
                        meta["total_doc"] = vals[-1]   # já formatado
                    in_comp = False
                    continue
                if re.match(r"^Comprovante emitido", line):
                    in_comp = False
                    continue
                # Bloco bancário no rodapé ("Banco Data de Arrecadação",
                # "Agência Estabelecimento..."): fecha a composição para evitar
                # que a linha "NNNN NNNN 0,00" (agência/estabelecimento)
                # seja confundida com um item de arrecadação.
                if re.match(r"^(Banco\b|Agência\b|Age[nñ]cia\b)", line, re.IGNORECASE):
                    in_comp = False
                    continue

                # Helper: o código de receita pode ter um sufixo de 2
                # dígitos numa linha logo abaixo do item, no formato
                # "NN - <descrição>" (ex.: comprovante de DAS mostra
                # "1010" na linha do item e "01 - ISS - SIMPLES..." na
                # linha seguinte → código completo "1010-01").
                def _codigo_completo(cod4: str, idx: int) -> str:
                    nxt = lines[idx + 1].strip() if idx + 1 < len(lines) else ""
                    ms = re.match(r"^(\d{2})\s*[-–]\s*[A-Za-zÀ-ÿ]", nxt)
                    if ms:
                        return f"{cod4}-{ms.group(1)}"
                    return cod4

                # Linha de item (começa com código 4 dígitos)
                item_m = re.match(
                    r"^(\d{4})\s+(.+?)\s+([\d\.]+,\d{2}|-)\s+([\d\.]+,\d{2}|-)\s+([\d\.]+,\d{2}|-)\s+([\d\.]+,\d{2})$",
                    line
                )
                if item_m:
                    desc = item_m.group(2).strip()
                    # Descrição deve ter ao menos uma letra — senão é linha de
                    # agência/estabelecimento e não item de DARF.
                    if re.search(r"[A-Za-zÀ-ÿ]", desc):
                        meta["items"].append({
                            "codigo":    _codigo_completo(item_m.group(1), _li),
                            "descricao": desc,
                            "principal": item_m.group(3) if item_m.group(3) != "-" else "",
                            "multa":     item_m.group(4) if item_m.group(4) != "-" else "",
                            "juros":     item_m.group(5) if item_m.group(5) != "-" else "",
                            "total_item":item_m.group(6),
                        })
                    continue

                # Linha de item sem juros/multa (só total à direita)
                item_m2 = re.match(
                    r"^(\d{4})\s+(.+?)\s+([\d\.]+,\d{2})$", line)
                if item_m2:
                    desc = item_m2.group(2).strip()
                    # Mesma proteção — descrição precisa ter letra.
                    if re.search(r"[A-Za-zÀ-ÿ]", desc):
                        meta["items"].append({
                            "codigo":    _codigo_completo(item_m2.group(1), _li),
                            "descricao": desc,
                            "principal": "", "multa": "", "juros": "",
                            "total_item": item_m2.group(3),
                        })

    # ── 3. Achata em linhas ──────────────────────────────────────────────────
    fname = Path(path).name
    rows = []
    for meta in doc_map.values():
        items = meta["items"]
        if not items:
            # Sem itens parseados: cria linha vazia para o documento aparecer
            items = [{"codigo": "", "descricao": "", "principal": "",
                      "multa": "", "juros": "", "total_item": ""}]
        for it in items:
            rows.append({
                "cnpj":           meta["cnpj"],
                "razao_social":   meta["razao_social"],
                "tipo_doc":       meta["tipo_doc"],
                "numero_doc":     meta["numero_doc"],
                "periodo":        meta["periodo"],
                "competencia_teste": format_competencia_teste(meta["periodo"]),
                "dt_vencimento":  meta["dt_vencimento"],
                "dt_arrecadacao": meta["dt_arrecadacao"],
                "banco":          meta["banco"],
                "agencia":        meta["agencia"],
                "estabelecimento":meta["estabelecimento"],
                "referencia":     meta["referencia"],
                "codigo":         it["codigo"],
                "descricao":      it["descricao"],
                "principal":      it["principal"],
                "multa":          it["multa"],
                "juros":          it["juros"],
                "total_item":     it["total_item"],
                "total_doc":      meta["total_doc"],
                "_source":        fname,
            })
    return rows


