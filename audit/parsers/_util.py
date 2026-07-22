"""Utilidades compartilhadas dos parsers: competência canônica e moeda BRL.

Extraído do AgriTax Audit v5 consolidado (agritax_audit_consolidado.py),
sem alterações de lógica — apenas modularização (M1 da arquitetura).
"""
import re

def format_competencia_teste(p) -> str:
    """
    Converte um período/competência bruto em código canônico no formato:
      AAAA.MM   — competência mensal (01..12)
      AAAA.NT   — competência trimestral (1T..4T)
      AAAA      — competência anual

    Entradas suportadas (e resultado):
      '28/02/2026'            → '2026.02'   (DARF/DAS: DD/MM/AAAA — descarta dia)
      '02/2026'               → '2026.02'   (DAS: MM/AAAA)
      'Fevereiro de 2026'     → '2026.02'   (DCOMP débito: "Mês de AAAA")
      'Fevereiro/2026'        → '2026.02'
      '1º Trimestre/2024'     → '2024.1T'   (PERDCOMP crédito trimestral)
      '3º Trimestre 2024'     → '2024.3T'
      'Trimestre 3/2024'      → '2024.3T'
      '2024'                  → '2024'      (crédito anual)

    Retorna string vazia se `p` for vazio/None ou não reconhecido.
    """
    if not p:
        return ""

    s = str(p).strip().upper()
    if not s:
        return ""

    # Remove preposição "DE" solta entre palavras (ex.: "FEVEREIRO DE 2026")
    s = re.sub(r"\s+DE\s+", " ", s)

    # ── Caso 1: DD/MM/AAAA (DARF) → AAAA.MM ──────────────────────────────────
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return f"{m.group(3)}.{int(m.group(2)):02d}"

    # ── Caso 2: MM/AAAA (DAS) → AAAA.MM ──────────────────────────────────────
    m = re.fullmatch(r"(\d{1,2})/(\d{4})", s)
    if m:
        return f"{m.group(2)}.{int(m.group(1)):02d}"

    # ── Caso 3: Nome do mês por extenso ou abreviado → AAAA.MM ───────────────
    # Nomes completos primeiro (mais específicos), depois abreviações.
    MESES = {
        "JANEIRO": "01", "FEVEREIRO": "02", "MARÇO": "03", "MARCO": "03",
        "ABRIL":   "04", "MAIO":      "05", "JUNHO":  "06",
        "JULHO":   "07", "AGOSTO":    "08", "SETEMBRO": "09",
        "OUTUBRO": "10", "NOVEMBRO":  "11", "DEZEMBRO": "12",
    }
    for nome, num in MESES.items():
        if nome in s:
            m_ano = re.search(r"(\d{4})", s)
            if m_ano:
                return f"{m_ano.group(1)}.{num}"
            return num   # sem ano (muito raro)

    # Abreviações de 3 letras (tipicamente em períodos diário/decendial):
    #   "21° Dia/Dez/2023", "3° Decendio/Dez/2023", "Jan/2024" etc.
    # Usa \b para evitar casar fragmentos dentro de outras palavras.
    MESES_ABREV = {
        "JAN": "01", "FEV": "02", "MAR": "03", "ABR": "04",
        "MAI": "05", "JUN": "06", "JUL": "07", "AGO": "08",
        "SET": "09", "OUT": "10", "NOV": "11", "DEZ": "12",
    }
    for abrev, num in MESES_ABREV.items():
        if re.search(rf"\b{abrev}\w*\b", s):  # aceita sufixos (Março, Dezembro, etc.)
            # Se já casou com o nome completo no loop acima, não chega aqui.
            # Mas casar abreviação curta ainda exige ano.
            m_ano = re.search(r"/(\d{4})\b", s) or re.search(r"(\d{4})\b", s)
            if m_ano:
                return f"{m_ano.group(1)}.{num}"

    # ── Caso 4: Trimestre → AAAA.NT ──────────────────────────────────────────
    # "1º TRIMESTRE/2024", "3º TRIMESTRE 2024", "3 TRIMESTRE 2024"
    m = re.search(r"(\d)[ºO°]?\s*TRIMESTRE\s*[/\s]*(\d{4})", s)
    if m:
        return f"{m.group(2)}.{m.group(1)}T"
    # "TRIMESTRE 3/2024"
    m = re.search(r"TRIMESTRE\s+(\d)\s*[/\s]*(\d{4})", s)
    if m:
        return f"{m.group(2)}.{m.group(1)}T"
    # Sem ano: "3º TRIMESTRE"
    m = re.search(r"(\d)[ºO°]?\s*TRIMESTRE\b", s)
    if m:
        return f"{m.group(1)}T"

    # ── Caso 5: apenas o ano ──────────────────────────────────────────────────
    m = re.fullmatch(r"(\d{4})", s)
    if m:
        return s

    # Fallback — retorna a string limpa
    return s


def parse_brl(val):
    if not val: return 0.0
    clean = re.sub(r'[R$\s]', "", str(val).strip())
    clean = clean.replace(".", "").replace(",", ".")
    try: return float(clean)
    except: return 0.0

def format_brl(value):
    s = f"R$ {value:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")

