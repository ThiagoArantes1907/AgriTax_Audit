"""Central de estruturação (CB-06): identifica, parseia e grava fatos.

Varre o raw/ do engajamento, classifica cada arquivo pela extensão + conteúdo,
registra a custódia (CB-03), roda o parser correspondente e grava os FatoFiscal
no SQLite. Reimportação é idempotente por (fonte, arquivo).
"""
from __future__ import annotations

import re
from pathlib import Path

from audit.core import custodia, db

from . import darf as p_darf
from . import dctf as p_dctf
from . import dctfweb as p_dctfweb
from . import ecd as p_ecd
from . import ecf as p_ecf
from . import efd_contribuicoes as p_efd
from . import fatos as adapt
from . import perdcomp as p_perdcomp
from . import simples as p_simples

EXTENSOES_SUPORTADAS = {".pdf", ".txt", ".xml", ".zip"}


def identificar_tipo(path: str | Path) -> str:
    """Classifica o arquivo: darf | dctf | dctfweb | perdcomp | simples |
    efd_contribuicoes | ecd | desconhecido."""
    path = Path(path)
    ext = path.suffix.lower()

    if ext == ".txt":
        try:
            primeiras = p_efd._efd_decode_file(path)[:5]
        except Exception:
            return "desconhecido"
        if not primeiras or not primeiras[0].startswith("|0000|"):
            return "desconhecido"
        # Layouts do 0000 (validados em arquivos reais do ReceitaNetBX):
        #   ECD:            |0000|LECD|DT_INI|...            → f[2] = "LECD"
        #   EFD-ICMS/IPI:   |0000|019|COD_FIN|DT_INI|...     → f[4] é data (8 dígitos)
        #   Contribuições:  |0000|006|TIPO|IND_SIT|NUM_REC|DT_INI|... → f[4] vazio/recibo
        f = primeiras[0].split("|")
        if len(f) > 2 and f[2] == "LECD":
            return "ecd"
        if len(f) > 2 and f[2] == "LECF":
            return "ecf"
        if len(f) > 4 and re.fullmatch(r"\d{8}", f[4] or ""):
            return "efd_icms"
        return "efd_contribuicoes"

    if ext in (".xml", ".zip"):
        return "dctfweb"

    if ext == ".pdf":
        texto = _texto_primeira_pagina(path).upper()
        if not texto:
            return "dctf"  # PDF vetorial sem camada de texto: caso típico da DCTF (OCR)
        if "PER/DCOMP" in texto or "PERDCOMP" in texto or "DECLARAÇÃO DE COMPENSAÇÃO" in texto \
                or "DECLARACAO DE COMPENSACAO" in texto or "PEDIDO DE RESTITUIÇÃO" in texto \
                or "PEDIDO DE RESTITUICAO" in texto or "PEDIDO DE RESSARCIMENTO" in texto:
            return "perdcomp"
        if "DCTFWEB" in texto:
            return "dctfweb"
        if "PGDAS" in texto or ("SIMPLES NACIONAL" in texto and "EXTRATO" in texto):
            return "simples"
        if "COMPROVANTE DE ARRECADA" in texto or "DOCUMENTO DE ARRECADA" in texto:
            return "darf"
        # capa real da DCTF imprime "D C T F" espaçado — checa sem espaços
        if "DCTF" in texto.replace(" ", "") \
                or "DECLARAÇÃO DE DÉBITOS E CRÉDITOS" in texto \
                or "DECLARACAO DE DEBITOS E CREDITOS" in texto:
            return "dctf"
        # fallback: pista no nome do arquivo (padrão dos downloads e-CAC)
        nome = path.name.upper()
        for pista, tipo in (("PERDCOMP", "perdcomp"), ("DCTFWEB", "dctfweb"),
                            ("DCTF", "dctf"), ("DARF", "darf"),
                            ("PGDAS", "simples"), ("DAS_", "simples")):
            if pista in nome:
                return tipo
        return "desconhecido"

    return "desconhecido"


def _texto_primeira_pagina(path: Path) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            if not pdf.pages:
                return ""
            return pdf.pages[0].extract_text() or ""
    except Exception:
        return ""


# tipo → (parser de arquivo → rows, adaptador rows → fatos)
_PIPELINES = {
    "darf": (p_darf.parse_darf_pdf, adapt.fatos_darf),
    "dctf": (p_dctf.extract_dctf, adapt.fatos_dctf),
    "dctfweb": (p_dctfweb.extract_dctfweb, adapt.fatos_dctfweb),
    "simples": (p_simples.extract_simples, adapt.fatos_simples),
    "efd_contribuicoes": (p_efd.extract_efd_contribuicoes, adapt.fatos_efd_contribuicoes),
    "ecf": (p_ecf.extract_ecf, adapt.fatos_ecf),
    "perdcomp": (lambda p: p_perdcomp.flatten_rows(p_perdcomp.parse_pdf(p), Path(p).name),
                 adapt.fatos_perdcomp),
}

_FONTES_POR_TIPO = {  # fontes a limpar na reimportação de um arquivo
    "darf": ("DARF", "DAS"),
    "dctf": ("DCTF",),
    "dctfweb": ("DCTFWEB",),
    "simples": ("PGDAS_D", "DAS"),
    "efd_contribuicoes": ("EFD_CONTRIBUICOES",),
    "ecf": ("ECF",),
    "perdcomp": ("PERDCOMP",),
}


def processar_arquivo(path: str | Path) -> tuple[str, list, list]:
    """(tipo, rows, fatos) de um único arquivo. ECD retorna rows sem fatos
    (vira insumo contábil do CR-01/02 no M6)."""
    path = Path(path)
    tipo = identificar_tipo(path)
    if tipo == "ecd":
        return tipo, p_ecd.extract_ecd(str(path)), []
    if tipo == "efd_icms":
        return tipo, [], []   # consistência interna (RP-04) e CR-03 chegam depois
    if tipo not in _PIPELINES:
        return tipo, [], []
    parser, adaptador = _PIPELINES[tipo]
    rows = parser(str(path))
    return tipo, rows, adaptador(rows, arquivo=path.name)


def _canal(engaj_dir: Path, arquivo: Path) -> str:
    rel = arquivo.resolve()
    if (engaj_dir / "raw" / "ecac").resolve() in rel.parents:
        return "ECAC"
    if (engaj_dir / "raw" / "bx").resolve() in rel.parents:
        return "RECEITANETBX"
    return "MANUAL"


def estruturar_engajamento(engaj_dir: str | Path, data_base: str = "") -> dict:
    """Fase `estruturar` do pipeline: raw/** → custódia → parsers → SQLite."""
    engaj_dir = Path(engaj_dir)
    resultado = {"processados": [], "ignorados": [], "erros": [], "fatos_gravados": 0}

    con = db.conectar(engaj_dir)
    try:
        for arquivo in sorted((engaj_dir / "raw").rglob("*")):
            if not arquivo.is_file():
                continue
            if arquivo.suffix.lower() not in EXTENSOES_SUPORTADAS:
                resultado["ignorados"].append({"arquivo": arquivo.name,
                                               "motivo": "extensão não suportada"})
                continue
            try:
                tipo, rows, fatos_lidos = processar_arquivo(arquivo)
                if tipo in ("desconhecido",):
                    resultado["ignorados"].append({"arquivo": arquivo.name,
                                                   "motivo": "tipo não identificado"})
                    continue
                custodia.registrar_arquivo(engaj_dir, arquivo,
                                           canal=_canal(engaj_dir, arquivo),
                                           data_base=data_base, descricao=tipo)
                for fonte in _FONTES_POR_TIPO.get(tipo, ()):
                    db.limpar_fonte(con, fonte, arquivo_origem=arquivo.name)
                if fatos_lidos:
                    db.inserir_fatos(con, fatos_lidos)
                resultado["fatos_gravados"] += len(fatos_lidos)
                resultado["processados"].append({"arquivo": arquivo.name, "tipo": tipo,
                                                 "linhas": len(rows),
                                                 "fatos": len(fatos_lidos)})
            except Exception as e:  # um arquivo ruim não derruba a carga
                resultado["erros"].append({"arquivo": arquivo.name, "erro": str(e)})
    finally:
        con.close()
    return resultado
