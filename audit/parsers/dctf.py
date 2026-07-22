"""Parser de DCTF (PDFs do e-CAC, com fallback OCR).

Extraído do AgriTax Audit v5 consolidado (agritax_audit_consolidado.py),
sem alterações de lógica — apenas modularização (M1 da arquitetura).
"""
import re
import unicodedata
from pathlib import Path

import pdfplumber

from ._ocr import (_get_ocr_env, _format_ocr_diagnostic,
                   _pytesseract, _pdf2image, PDF2IMAGE_OK)

_DCTF_KEYWORDS = ["CNPJ", "DCTF", "DEBITO", "DÉBITO", "TRIBUTO",
                  "MINISTERIO", "MINISTÉRIO", "DECLARACAO", "DECLARAÇÃO"]
from ._util import format_competencia_teste

def _dctf_strip_accents(s: str) -> str:
    """Remove acentos para comparacao tolerante. 'DÉBITO' -> 'DEBITO'."""
    if not s:
        return s
    import unicodedata
    nfkd = unicodedata.normalize('NFKD', s)
    return ''.join(c for c in nfkd if not unicodedata.combining(c))


# ── Helpers ───────────────────────────────────────────────────────────────────
def _dctf_brl(s) -> float:
    """Converte valor BRL com artefatos OCR para float. Ex: '1.118,21' -> 1118.21"""
    if isinstance(s, (int, float)):
        return float(s)
    s = re.sub(r"\s+", "", str(s).strip())
    if re.match(r"^[\d.]+,\d{1,2}$", s):        # formato BR
        return float(s.replace(".", "").replace(",", "."))
    if re.match(r"^[\d,]+\.\d{1,2}$", s):        # formato EN
        return float(s.replace(",", ""))
    s = re.sub(r"[^\d.]", "", s.replace(",", "."))
    try:
        return float(s)
    except ValueError:
        return 0.0


def _dctf_re(pattern, text, default=""):
    """Busca regex case-insensitive; retorna grupo 1 ou default."""
    m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else default


def _dctf_val(pattern, text) -> float:
    """Busca regex e converte resultado para float BRL."""
    m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    return _dctf_brl(m.group(1)) if m else 0.0


def _dctf_last_brl(line: str) -> float:
    """Pega o ultimo valor monetario BR da linha. Ex: 'Total ...: 242,77'"""
    nums = re.findall(r"\d[\d.]*,\d{2}", line)
    return _dctf_brl(nums[-1]) if nums else 0.0


def _dctf_text_valido(pages: list) -> bool:
    """
    Valida se a lista de textos contem conteudo util de DCTF.
    Evita aceitar texto de baixa qualidade OCR como se fosse valido.
    Requer: pelo menos 200 caracteres totais E pelo menos 1 palavra-chave DCTF.
    """
    full = " ".join(pages)
    if len(full.strip()) < 200:
        return False
    return any(kw in full.upper() for kw in _DCTF_KEYWORDS)


# ── Extracao de texto das paginas ─────────────────────────────────────────────
def _is_cid_text(text: str) -> bool:
    """
    Detecta se o texto extraido eh majoritariamente glifos CID (Character ID)
    sem mapeamento Unicode — situacao tipica dos PDFs da DCTF gerados via
    "Microsoft Print to PDF" a partir do eCAC. Esses PDFs contem o texto
    visualmente (glifos posicionados) mas sem a tabela ToUnicode que traduz
    os IDs dos glifos para caracteres Unicode reais, entao o texto sai como
    '(cid:131)(cid:132)(cid:133)' em vez de palavras legiveis.

    Heuristica: texto eh considerado CID se a contagem de '(cid:' representar
    mais de 10% do conteudo.
    """
    if not text:
        return False
    cid_count = text.count("(cid:")
    # Proporcao alta de CIDs em relacao ao tamanho do texto
    return cid_count > 20 and (cid_count * 8) > len(text) * 0.1


def _dctf_get_pages_text(pdf_path: str) -> list:
    """
    Extrai texto de cada pagina do PDF. Tenta tres estrategias em ordem:
      1. pdfplumber extrai texto diretamente (PDFs com texto selecionavel)
      2. pdfplumber.to_image(300 DPI) + pytesseract (PDFs vetoriais — caso DCTF eCAC)
      3. pdf2image(300 DPI) + pytesseract (fallback quando estrategia 2 nao disponivel)

    Caso especial: os PDFs da DCTF gerados no eCAC via "Microsoft Print to PDF"
    contem texto embutido mas usam fontes sem mapeamento Unicode (ToUnicode CMap
    ausente), o que faz o texto sair como '(cid:XXX)' em vez de letras. Nesses
    casos a estrategia 1 produz texto tecnicamente presente mas ilegivel, e a
    unica saida eh rasterizar + OCR. A mensagem de erro explica isso ao usuario.

    Lanca RuntimeError com diagnóstico detalhado de cada estratégia se todas
    falharem.
    """
    tentativas = []   # ("nome", "resultado descritivo")

    # Estrategia 1 — texto direto
    cid_detectado = False
    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages = [page.extract_text(x_tolerance=3, y_tolerance=3) or ""
                     for page in pdf.pages]
        full_len = sum(len(p) for p in pages)
        if _dctf_text_valido(pages):
            return pages
        cid_detectado = any(_is_cid_text(p) for p in pages)
        tentativas.append(("pdfplumber direto",
            f"{len(pages)} pág., {full_len} chars"
            + (" (texto CID — PDF vetorial sem ToUnicode)" if cid_detectado else " (texto insuficiente ou sem keywords DCTF)")))
    except Exception as e:
        tentativas.append(("pdfplumber direto", f"ERRO: {type(e).__name__}: {e}"))

    # PDFs de DCTF do eCAC precisam de OCR
    env = _get_ocr_env()
    if not env["pytesseract_ok"] or not env["tesseract_exe"]:
        diag = _format_ocr_diagnostic(env)
        if cid_detectado:
            diag = (
                "Este PDF foi gerado pelo eCAC via 'Imprimir como PDF'.\n"
                "O texto existe no arquivo MAS esta embutido como glifos sem\n"
                "mapeamento Unicode (ToUnicode CMap ausente).\n\n"
            ) + diag
        raise RuntimeError(diag)

    ocr_lang = "por" if env["has_por"] else "eng"
    ocr_cfg  = "--psm 6 --oem 3"

    # Estrategia 2 — pdfplumber.to_image (200 DPI) + OCR
    # 200 DPI é suficiente para texto gerado por computador e ~2.25x mais rápido que 300 DPI
    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages = []
            for page in pdf.pages:
                pil_img = page.to_image(resolution=200).original
                pages.append(_pytesseract.image_to_string(pil_img, lang=ocr_lang, config=ocr_cfg))
        full_len = sum(len(p) for p in pages)
        if _dctf_text_valido(pages):
            return pages
        tentativas.append(("pdfplumber.to_image(200) + OCR",
            f"{len(pages)} pág., {full_len} chars (texto insuficiente ou sem keywords DCTF)"))
    except Exception as e:
        tentativas.append(("pdfplumber.to_image(200) + OCR", f"ERRO: {type(e).__name__}: {e}"))

    # Estrategia 3 — pdf2image (200 DPI) + OCR
    if PDF2IMAGE_OK:
        try:
            images = _pdf2image.convert_from_path(pdf_path, dpi=200)
            pages = [_pytesseract.image_to_string(img, lang=ocr_lang, config=ocr_cfg)
                     for img in images]
            full_len = sum(len(p) for p in pages)
            if _dctf_text_valido(pages):
                return pages
            tentativas.append(("pdf2image(200) + OCR",
                f"{len(pages)} pág., {full_len} chars (texto insuficiente ou sem keywords DCTF)"))
            # Fallback extremo: se o texto tem algum tamanho mas não tem as keywords,
            # retorna mesmo assim — pode ser DCTF com layout diferente.
            # Melhor tentar parser do que falhar silenciosamente.
            if full_len > 100:
                return pages
        except Exception as e:
            tentativas.append(("pdf2image(300) + OCR", f"ERRO: {type(e).__name__}: {e}"))
    else:
        tentativas.append(("pdf2image(300) + OCR", "pdf2image não disponível"))

    # Monta diagnóstico detalhado — mostra o que cada estratégia retornou
    det = "\n".join(f"  [{i}] {nome}: {res}" for i, (nome, res) in enumerate(tentativas, 1))
    diag = _format_ocr_diagnostic(env)
    raise RuntimeError(
        f"Nenhuma estratégia de extração funcionou para este PDF.\n\n"
        f"--- Tentativas ---\n{det}\n\n"
        f"--- Ambiente OCR ---\n{diag}"
    )


# ── Parser do cabecalho ───────────────────────────────────────────────────────
def _dctf_parse_cabecalho(full_text: str) -> dict:
    """Extrai campos do cabecalho a partir do texto OCR completo do PDF.

    Normaliza acentos e dashes antes de aplicar regex — cobre tanto OCR
    em Portugues (preserva acentos) quanto em Ingles (perde acentos).
    """
    # Normaliza acentos e dashes — tolerante a ambas variantes do OCR
    full_text = _dctf_strip_accents(full_text)
    full_text = full_text.replace("—", "-").replace("–", "-")

    r = _dctf_re

    # CNPJ — formato XX.XXX.XXX/XXXX-XX
    cnpj = r(r"CNPJ[:\s]+(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})", full_text)

    # Periodo de competencia — "Dezembro/2024" logo apos o CNPJ
    comp = r(r"CNPJ[:\s]+[\d./-]+\s+((?:Jan|Fev|Mar|Abr|Mai|Jun|Jul|Ago|Set|Out|Nov|Dez)\w*/\d{4})",
             full_text)
    if not comp:
        comp = r(r"\b((?:Janeiro|Fevereiro|Mar[cç]o|Abril|Maio|Junho|Julho|Agosto|"
                  r"Setembro|Outubro|Novembro|Dezembro)/\d{4})\b", full_text)

    # Numero da declaracao — "100.2024.2025.1861637518"
    # OCR varia: "Declaragao", "Declaracgeo", "Declaragdo", etc.
    num_decl = r(r"N[^\s]{0,12}mero\s+da\s+Declara[^\s:]{0,15}[:\s]+([\d.]{8,})", full_text)

    # Numero do recibo
    num_recibo = r(r"N[^\s]{0,12}mero\s+do\s+Recibo[:\s]+([\d./-]+)", full_text)

    # Datas — OCR: "Recepcdo", "Recepgdo", "2zocessamento", etc.
    data_rec  = r(r"Data\s+de\s+Recep[^\s:]{0,12}[:\s]+(\d{2}/\d{2}/\d{4})", full_text)
    data_proc = r(r"Data\s+de\s+[Pp2][^\s:]{0,14}[:\s]+(\d{2}/\d{2}/\d{4})", full_text)

    # Situacao da declaracao
    situacao = r(r"Situa[^\s:]{0,12}[:\s]+(Normal|Retificad[ao]|Ativa|Em\s+An[aai]lise)",
                  full_text)
    if not situacao:
        situacao = r(r"Situa[^\s:]{0,12}[:\s]+([^\n]{1,25})", full_text)
    situacao = situacao.strip()[:30]

    # Declaracao retificadora — OCR: "N&o", "Nao", "Sim", etc.
    retif_raw = r(r"Declara[^\s]{0,12}\s+Retificadora[:\s]+(\S+)", full_text)
    retif = "Sim" if re.match(r"[Ss]", retif_raw.strip()) else "Nao"

    # Nome empresarial
    nome = r(r"Nome\s+Empresarial[:\s]+([^\n]+)", full_text).strip()

    return {
        "cnpj":                cnpj,
        "nome_empresarial":    nome,
        "periodo_competencia": comp,
        "numero_declaracao":   num_decl,
        "numero_recibo":       num_recibo,
        "data_recepcao":       data_rec,
        "data_processamento":  data_proc,
        "situacao_declaracao": situacao,
        "retificadora":        retif,
    }


# ── Parser de uma pagina de tributo ──────────────────────────────────────────
def _dctf_parse_tributo(page_text: str) -> dict:
    """
    Extrai campos de uma pagina que contem um tributo declarado.
    Layout OCR tipico:
      GRUPO DO TRIBUTO . PIS/PASEP - CONTRIB. P/PROGRAMA...
      CODIGO RECEITA : 6912-01
      PERIODICIDADE: Mensal  PERIODO DE APURACAO: Dezembro/2024
      DEBITO APURADO  242,77
      - PAGAMENTO  242,77
      ...
      PA: 31/12/2024  ...  Codigo da Receita: 6912
      Data do Vencimento  24/01/2025
      Valor do Principal: 242,77 / Valor da Multa: 0,80 / ...

    IMPORTANTE: normaliza o texto removendo acentos no inicio, porque
    Tesseract com lang='por' produz 'DÉBITO APURADO' e sem 'por' produz
    'DEBITO APURADO'. Os regex abaixo assumem texto SEM acento.
    Tambem normaliza em-dash/en-dash para hyphen simples.
    """
    # Normaliza acentos e dashes antes de aplicar qualquer regex
    page_text = _dctf_strip_accents(page_text)
    page_text = page_text.replace("—", "-").replace("–", "-")

    r = _dctf_re
    v = lambda pat: _dctf_val(pat, page_text)
    N = r"([\d.,]+)"

    # --- Grupo do tributo ---
    # Linha OCR: "GRUPO DO TRIBUTO . PIS/PASEP - CONTRIB. ..."
    # Separador entre "GRUPO DO TRIBUTO" e o nome pode ser: . : ; | ' ` > 1 I , espaco
    # (OCR varia conforme a fonte e o DPI — cobre todos os artefatos conhecidos)
    grupo = ""
    m_grp = re.search(r"GRUPO\s+DO\s+TRIBUTO\s*[.:;|'`1I>,\s]\s*(.+)",
                       page_text, re.IGNORECASE)
    if m_grp:
        # Limpa separadores do início do sufixo. NUNCA remove caracteres que
        # poderiam ser o primeiro char de um nome de tributo válido (I de IRRF,
        # 1 seria letra/dígito, etc.). Só remove sinais de pontuação OCR e espaços.
        _sep_clean = lambda txt: re.sub(r"^[.:;|'`>,\s]+", "", txt).strip()
        sufixo = _sep_clean(m_grp.group(1))
        # A linha ANTERIOR pode conter o inicio do nome do tributo
        antes = page_text[:m_grp.start()].rstrip()
        linha_ant = antes.split("\n")[-1].strip() if "\n" in antes else antes.strip()
        eh_nome = (
            len(linha_ant) > 5
            and re.search(r"[A-Z]{3}", linha_ant)
            and not re.search(
                r"MINISTERIO|SECRETARIA|INFORMACAO|CNPJ|DCTF|"
                r"DEBITO\s+APURADO|FIM\s+DE|GRUPO|FISCAL",
                linha_ant, re.IGNORECASE)
        )
        if eh_nome and sufixo:
            linha_ant = re.sub(r"^[_\-\s']+", "", linha_ant).strip()
            grupo = (linha_ant + " " + sufixo).strip()
        elif eh_nome:
            grupo = re.sub(r"^[_\-\s']+", "", linha_ant).strip()
        elif sufixo:
            grupo = sufixo
            # Verifica continuacao na linha seguinte
            resto = page_text[m_grp.end():]
            nl = re.match(r"[ \t]*([A-Z][A-Z0-9/. -]{3,})\n", resto)
            if nl and not re.search(
                    r"^(CODIGO|PERIODICIDADE|PERIODO|DEBITO|CREDITO|SOMA|SALDO|Valor|Total|Pag)",
                    nl.group(1).strip(), re.IGNORECASE):
                grupo = (grupo + " " + nl.group(1).strip()).strip()

    # --- Codigo da Receita ---
    # OCR: "CODIGO RECEITA : 6912-01" ou "CODIGO RECEITA > 6912-01" ou "CODIGO RECEITA 1 6912-01"
    cod = r(r"CODIGO\s+RECEITA\s*[^\d]?\s*(\d{4}-\d{2})", page_text)
    if not cod:
        cod = r(r"\b(\d{4}-\d{2})\b", page_text)

    # --- Periodicidade e Periodo de Apuracao ---
    # Cobre variações do OCR: "PERIODO" ou "PERTODO" (I→T), "APURACAO" ou "APURAC4O".
    period = r(r"PERIODICIDADE[:\s]+(\w+)", page_text)
    # Chave "PERIODO DE APURACAO" — aceita PERIODO / PERTODO etc. (1 char variável)
    _key_pa = r"PER.ODO\s+DE\s+APURAC.O"
    # Mensal: "Dezembro/2024"
    pa = r(_key_pa + r"[:\s]+((?:Jan|Fev|Mar|Abr|Mai|Jun|Jul|Ago|Set|Out|Nov|Dez)\w*/\d{4})",
            page_text)
    # Trimestral: "4° Trimestre/2024"  (o ° às vezes vira "o" no OCR)
    if not pa:
        pa = r(_key_pa + r"[:\s]+(\d[°ºo]?\s*Trimestre/\d{4})", page_text)
    # Decendial: "3° Decendio/Dez/2023"
    if not pa:
        pa = r(_key_pa + r"[:\s]+(\d[°ºo]?\s*Decendio/\w+/\d{4})", page_text)
    # Diária: "21° Dia/Dez/2023"
    if not pa:
        pa = r(_key_pa + r"[:\s]+(\d{1,2}[°ºo]?\s*Dia/\w+/\d{4})", page_text)
    # Quinzenal: "2° Quinzena/Dez/2023"
    if not pa:
        pa = r(_key_pa + r"[:\s]+(\d[°ºo]?\s*Quinzena/\w+/\d{4})", page_text)
    # Semanal: "5° Semana/Dez/2023"
    if not pa:
        pa = r(_key_pa + r"[:\s]+(\d[°ºo]?\s*Semana/\w+/\d{4})", page_text)
    # Anual / Semestral / fallback genérico: "Algo/2024"
    if not pa:
        pa = r(_key_pa + r"[:\s]+([\w°ºo° /]+?/\d{4})", page_text)

    # --- Debito apurado e creditos vinculados ---
    debito    = v(r"DEBITO\s+APURADO\s+" + N)
    pagamento = v(r"[-–—]\s*PAGAMENTO\s+" + N)
    compens   = v(r"[-–—]\s*COMPENSACOES\s+" + N)
    parcel    = v(r"[-–—]\s*PARCELAMENTO\s+" + N)
    suspens   = v(r"[-–—]\s*SUSPENSAO\s+" + N)
    soma_cred = v(r"SOMA\s+DOS\s+CREDITOS\s+VINCULADOS[:\s]+" + N)
    saldo     = v(r"SALDO\s+A\s+PAGAR\s+DO\s+DEBITO[:\s]+" + N)

    # Valor do Debito Total — OCR converte "R$" em "RS"
    vl_total = v(r"Valor\s+do\s+D[eé][^\s]{0,5}\s*[-–]\s*R[S$]\s+Total[:\s]+" + N)

    # Total da Contribuicao — pega o ultimo valor monetario da linha
    linha_contr = r(r"(Total\s+da\s+Contribui[^\n]{5,120})", page_text)
    tot_contr = _dctf_last_brl(linha_contr) if linha_contr else 0.0

    # Pagamento com DARF
    pag_darf = v(r"Pagamento\s+com\s+DARF\s*[-–—]\s*R[S$]\s+Total[:\s]+" + N)

    # --- DARF vinculado ao debito ---
    darf_pa    = r(r"\bPA[:\s]+(\d{2}/\d{2}/\d{4})", page_text)
    darf_cod   = r(r"C[eé][^\s]{0,8}digo\s+da\s+Receita[:\s]+(\d{4})", page_text)
    darf_venc  = r(r"Data\s+do\s+Vencimento\s+(\d{2}/\d{2}/\d{4})", page_text)
    darf_princ = v(r"Valor\s+do\s+Principal[:\s]+" + N)
    darf_multa = v(r"Valor\s+da\s+Multa[:\s]+" + N)
    darf_juros = v(r"Valor\s+dos\s+Juros[:\s]+" + N)
    # DARF total: captura ate 12 chars para cobrir "1.121, 90" com espaco OCR
    m_dt = re.search(r"Valor\s+Total\s+do\s+DARF[:\s]+([\d.,\s]{3,12})",
                     page_text, re.IGNORECASE)
    darf_total = _dctf_brl(m_dt.group(1)) if m_dt else 0.0
    darf_pago  = v(r"Valor\s+Pago\s+do\s+D[eé][^\s]{0,5}[:\s]+" + N)

    return {
        "grupo_tributo":        grupo,
        "codigo_receita":       cod,
        "periodicidade":        period,
        "periodo_apuracao":     pa,
        "competencia_teste":    format_competencia_teste(pa),
        "debito_apurado":       debito,
        "credito_pagamento":    pagamento,
        "credito_compensacoes": compens,
        "credito_parcelamento": parcel,
        "credito_suspensao":    suspens,
        "soma_creditos":        soma_cred,
        "saldo_pagar":          saldo,
        "valor_total_debito":   vl_total,
        "total_contribuicao":   tot_contr,
        "pagamento_darf":       pag_darf,
        "darf_pa":              darf_pa,
        "darf_codigo_receita":  darf_cod,
        "darf_vencimento":      darf_venc,
        "darf_principal":       darf_princ,
        "darf_multa":           darf_multa,
        "darf_juros":           darf_juros,
        "darf_total":           darf_total,
        "darf_pago":            darf_pago,
    }


# ── Extracao principal ────────────────────────────────────────────────────────
def extract_dctf(pdf_path: str) -> list:
    """
    Processa um PDF de DCTF e retorna lista de dicts (uma linha por tributo).
    Lanca RuntimeError com mensagem clara se o PDF nao puder ser processado.
    """
    pages = _dctf_get_pages_text(pdf_path)
    full  = "\n".join(pages)

    cabecalho = _dctf_parse_cabecalho(full)
    cabecalho["_source"] = Path(pdf_path).name

    # Paginas de tributo: contem "GRUPO DO TRIBUTO" e "DEBITO APURADO"
    # Normaliza acentos para que funcione com OCR em PT (DÉBITO) ou EN (DEBITO).
    def _has_tributo(p: str) -> bool:
        norm = _dctf_strip_accents(p)
        return bool(re.search(r"GRUPO\s+DO\s+TRIBUTO", norm, re.IGNORECASE)
                    and re.search(r"DEBITO\s+APURADO", norm, re.IGNORECASE))

    tributo_pages = [p for p in pages if _has_tributo(p)]

    rows = [{**cabecalho, **_dctf_parse_tributo(p)} for p in tributo_pages]

    if not rows:
        rows.append({
            **cabecalho,
            "grupo_tributo":  "Nenhum tributo encontrado",
            "codigo_receita": "", "periodicidade": "", "periodo_apuracao": "",
            "competencia_teste": "",
        })

    return rows


# ── Resumo consolidado ────────────────────────────────────────────────────────
def build_dctf_resumo(rows: list) -> list:
    from collections import defaultdict
    acc = defaultdict(lambda: {
        "qtd_declaracoes": 0, "total_debito": 0.0, "total_pagamento": 0.0,
        "total_compensacao": 0.0, "total_parcelamento": 0.0,
        "total_suspensao": 0.0, "total_saldo": 0.0, "total_darf_pago": 0.0,
        "grupo_tributo": "",
    })
    for r in rows:
        k = r.get("codigo_receita") or "-"
        acc[k]["qtd_declaracoes"]    += 1
        acc[k]["total_debito"]       += float(r.get("debito_apurado",       0) or 0)
        acc[k]["total_pagamento"]    += float(r.get("credito_pagamento",    0) or 0)
        acc[k]["total_compensacao"]  += float(r.get("credito_compensacoes", 0) or 0)
        acc[k]["total_parcelamento"] += float(r.get("credito_parcelamento", 0) or 0)
        acc[k]["total_suspensao"]    += float(r.get("credito_suspensao",    0) or 0)
        acc[k]["total_saldo"]        += float(r.get("saldo_pagar",         0) or 0)
        acc[k]["total_darf_pago"]    += float(r.get("darf_pago",           0) or 0)
        if not acc[k]["grupo_tributo"]:
            acc[k]["grupo_tributo"] = r.get("grupo_tributo", "")
    return [{"codigo_receita": k, **v} for k, v in sorted(acc.items())]


# ── Exportacao Excel ─────────────────────────────────────────────────────────
