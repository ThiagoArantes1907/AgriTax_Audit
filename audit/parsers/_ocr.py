"""Ambiente de OCR (Tesseract/Poppler) para PDFs sem camada de texto.

Extraído do AgriTax Audit v5 consolidado (agritax_audit_consolidado.py),
sem alterações de lógica — apenas modularização (M1 da arquitetura).
"""
# =============================================================================

try:
    import pytesseract as _pytesseract
    PYTESSERACT_OK = True
except ImportError:
    _pytesseract = None
    PYTESSERACT_OK = False

try:
    import pdf2image as _pdf2image
    PDF2IMAGE_OK = True
except ImportError:
    _pdf2image = None
    PDF2IMAGE_OK = False


# ── Diagnóstico do ambiente OCR ───────────────────────────────────────────────
def _probe_ocr_environment() -> dict:
    """
    Verifica o estado do ambiente OCR (Tesseract + idiomas + Poppler).
    Tenta autodetectar o Tesseract no Windows mesmo quando não está no PATH,
    buscando nos caminhos de instalação padrão.

    Retorna:
        {
          "pytesseract_ok": bool,
          "tesseract_exe":  str | None,   # caminho detectado
          "tesseract_ver":  str | None,   # ex: "5.3.1"
          "langs":          list[str],    # ex: ["eng", "por"]
          "has_por":        bool,         # True se português disponível
          "pdf2image_ok":   bool,
          "poppler_ok":     bool,         # True se Poppler responde
          "errors":         list[str],
          "recommendations":list[str],
        }
    """
    import os as _os, shutil as _shutil, subprocess as _subprocess
    info = {
        "pytesseract_ok": PYTESSERACT_OK,
        "tesseract_exe":  None,
        "tesseract_ver":  None,
        "langs":          [],
        "has_por":        False,
        "pdf2image_ok":   PDF2IMAGE_OK,
        "poppler_ok":     False,
        "errors":         [],
        "recommendations":[],
    }

    if not PYTESSERACT_OK:
        info["errors"].append("Biblioteca Python 'pytesseract' não instalada.")
        info["recommendations"].append(
            "Instale com: pip install pytesseract pdf2image pdfplumber Pillow")
        return info

    # ── Tenta localizar o binário do Tesseract ───────────────────────────────
    candidates = []
    # 1) PATH
    in_path = _shutil.which("tesseract") or _shutil.which("tesseract.exe")
    if in_path:
        candidates.append(in_path)
    # 2) Caminhos padrão de instalação no Windows
    for pth in [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        _os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
        _os.path.expandvars(r"%USERPROFILE%\AppData\Local\Tesseract-OCR\tesseract.exe"),
        # 3) Caminhos comuns no Linux/Mac (em geral já estão no PATH, mas por garantia)
        "/usr/bin/tesseract",
        "/usr/local/bin/tesseract",
        "/opt/homebrew/bin/tesseract",
    ]:
        if pth and _os.path.isfile(pth) and pth not in candidates:
            candidates.append(pth)

    if not candidates:
        info["errors"].append("Tesseract OCR não encontrado no sistema.")
        info["recommendations"].append(
            "Baixe e instale: https://github.com/UB-Mannheim/tesseract/wiki\n"
            "Durante a instalação, marque 'Add to PATH' e inclua o idioma Português.")
        return info

    tesseract_exe = candidates[0]
    info["tesseract_exe"] = tesseract_exe
    # Configura pytesseract para usar o binário detectado (cobre o caso em que
    # o Tesseract está instalado mas não foi adicionado ao PATH do Windows).
    try:
        _pytesseract.pytesseract.tesseract_cmd = tesseract_exe
    except Exception:
        pass

    # ── Obtém versão ─────────────────────────────────────────────────────────
    try:
        out = _subprocess.run([tesseract_exe, "--version"],
                              capture_output=True, text=True, timeout=5)
        first_line = (out.stdout or out.stderr or "").splitlines()[0] if (out.stdout or out.stderr) else ""
        # formato "tesseract 5.3.1" ou "tesseract v5.3.1.20230401"
        import re as _re
        m = _re.search(r"tesseract\s+v?(\d+\.\d+\.\d+)", first_line, _re.IGNORECASE)
        if m:
            info["tesseract_ver"] = m.group(1)
    except Exception as e:
        info["errors"].append(f"Tesseract encontrado em {tesseract_exe} mas não executa: {e}")

    # ── Lista idiomas disponíveis ────────────────────────────────────────────
    try:
        langs = _pytesseract.get_languages(config="")
        info["langs"] = sorted(langs) if langs else []
        info["has_por"] = "por" in info["langs"]
        if not info["has_por"]:
            info["recommendations"].append(
                "Instale o pacote de idioma Português do Tesseract para melhor acurácia "
                "com acentos e caracteres especiais. No Windows, reinstale o Tesseract e "
                "marque 'Portuguese' em 'Additional language data'.")
    except Exception as e:
        info["errors"].append(f"Não foi possível listar idiomas do Tesseract: {e}")

    # ── Verifica Poppler (necessário pelo pdf2image) ─────────────────────────
    if PDF2IMAGE_OK:
        poppler_bin = _shutil.which("pdftoppm") or _shutil.which("pdftoppm.exe")
        # Tenta caminhos padrão no Windows
        if not poppler_bin:
            for pth in [
                r"C:\poppler\Library\bin\pdftoppm.exe",
                r"C:\Program Files\poppler\Library\bin\pdftoppm.exe",
                r"C:\Program Files\poppler\bin\pdftoppm.exe",
            ]:
                if _os.path.isfile(pth):
                    poppler_bin = pth
                    break
        info["poppler_ok"] = bool(poppler_bin)
        if not info["poppler_ok"]:
            info["recommendations"].append(
                "Poppler (requerido pelo pdf2image) não detectado. No Windows baixe em "
                "https://github.com/oschwartz10612/poppler-windows/releases, extraia em "
                "C:\\poppler\\ e adicione C:\\poppler\\Library\\bin ao PATH.")

    return info


# Cache do diagnóstico — probe é executado uma vez por sessão
_OCR_ENV_CACHE = None

def _get_ocr_env():
    global _OCR_ENV_CACHE
    if _OCR_ENV_CACHE is None:
        _OCR_ENV_CACHE = _probe_ocr_environment()
    return _OCR_ENV_CACHE


def _format_ocr_diagnostic(env: dict) -> str:
    """Monta mensagem amigável a partir do diagnóstico, sem repetir o óbvio."""
    lines = ["Nao foi possivel extrair texto deste PDF.\n"]
    lines.append("── Diagnóstico do ambiente OCR ──")
    if env["pytesseract_ok"]:
        lines.append("  ✓ pytesseract (Python) OK")
    else:
        lines.append("  ✗ pytesseract (Python) NÃO instalado")
    if env["tesseract_exe"]:
        ver = env["tesseract_ver"] or "versão desconhecida"
        lines.append(f"  ✓ Tesseract OCR  {ver}  em: {env['tesseract_exe']}")
        if env["has_por"]:
            lines.append(f"  ✓ Idioma Português instalado")
        else:
            langs = ", ".join(env["langs"][:6]) if env["langs"] else "(nenhum)"
            lines.append(f"  ⚠ Idioma Português AUSENTE  (idiomas atuais: {langs})")
    else:
        lines.append("  ✗ Tesseract OCR NÃO encontrado no sistema")
    if env["pdf2image_ok"]:
        lines.append(f"  {'✓' if env['poppler_ok'] else '⚠'} pdf2image OK  "
                     f"{'(Poppler detectado)' if env['poppler_ok'] else '(Poppler NÃO detectado)'}")
    else:
        lines.append("  ⚠ pdf2image (Python) não instalado — é um fallback opcional")

    if env["recommendations"]:
        lines.append("\n── Como resolver ──")
        for i, rec in enumerate(env["recommendations"], 1):
            lines.append(f"  {i}. {rec}")
    return "\n".join(lines)


# ── Colunas ───────────────────────────────────────────────────────────────────
DCTF_DETAIL_COLS = [
    ("cnpj",                 "CNPJ",                      150),
    ("nome_empresarial",     "Nome Empresarial",          230),
    ("periodo_competencia",  "Periodo de Competencia",    145),
    ("numero_declaracao",    "Numero da Declaracao",      210),
    ("numero_recibo",        "Numero do Recibo",          175),
    ("data_recepcao",        "Data de Recepcao",          110),
    ("data_processamento",   "Data de Processamento",     130),
    ("situacao_declaracao",  "Situacao da Declaracao",    120),
    ("retificadora",         "Decl. Retificadora",        110),
    ("grupo_tributo",        "Grupo do Tributo",          290),
    ("codigo_receita",       "Codigo da Receita",          95),
    ("periodicidade",        "Periodicidade",              90),
    ("periodo_apuracao",     "Periodo de Apuracao",       130),
    ("competencia_teste",    "Competencia Teste",         130),
    ("debito_apurado",       "Debito Apurado",            120),
    ("credito_pagamento",    "Credito - Pagamento",       130),
    ("credito_compensacoes", "Credito - Compensacoes",    145),
    ("credito_parcelamento", "Credito - Parcelamento",    145),
    ("credito_suspensao",    "Credito - Suspensao",       130),
    ("soma_creditos",        "Soma dos Creditos Vinc.",   155),
    ("saldo_pagar",          "Saldo a Pagar",             110),
    ("valor_total_debito",   "Valor Total do Debito",     140),
    ("total_contribuicao",   "Total da Contribuicao",     140),
    ("pagamento_darf",       "Pagamento com DARF",        140),
    ("darf_pa",              "DARF - PA",                  95),
    ("darf_codigo_receita",  "DARF - Cod. Receita",        95),
    ("darf_vencimento",      "DARF - Vencimento",         110),
    ("darf_principal",       "DARF - Principal",          120),
    ("darf_multa",           "DARF - Multa",              100),
    ("darf_juros",           "DARF - Juros",              100),
    ("darf_total",           "DARF - Total",              110),
    ("darf_pago",            "DARF - Valor Pago",         120),
]

DCTF_RESUMO_COLS = [
    ("codigo_receita",     "Codigo da Receita",    120),
    ("grupo_tributo",      "Grupo do Tributo",      290),
    ("qtd_declaracoes",    "Qtd. Declaracoes",      100),
    ("total_debito",       "Total Debito Apurado",  150),
    ("total_pagamento",    "Total Pagamento",       140),
    ("total_compensacao",  "Total Compensacoes",    145),
    ("total_parcelamento", "Total Parcelamento",    140),
    ("total_suspensao",    "Total Suspensao",       130),
    ("total_saldo",        "Total Saldo a Pagar",   140),
    ("total_darf_pago",    "Total DARF Pago",       130),
]

DCTF_MONEY_KEYS = {
    "debito_apurado", "credito_pagamento", "credito_compensacoes",
    "credito_parcelamento", "credito_suspensao", "soma_creditos",
    "saldo_pagar", "valor_total_debito", "total_contribuicao",
    "pagamento_darf", "darf_principal", "darf_multa", "darf_juros",
    "darf_total", "darf_pago",
    "total_debito", "total_pagamento", "total_compensacao",
    "total_parcelamento", "total_suspensao", "total_saldo", "total_darf_pago",
}

