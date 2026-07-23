"""Fase `coletar` (CB-04): robôs Selenium do e-CAC + cópia para o engajamento.

Fluxo (mesma arquitetura do v5, validada em produção na GUI):
 1. Chrome aberto em modo debug (porta 9222) — `--abrir-chrome` abre um;
 2. VOCÊ loga no e-CAC com certificado/procuração e navega até o serviço;
 3. Cada robô conecta na porta, detecta o CNPJ logado e baixa em massa para
    a pasta central C:\\AgriTaxAudit\\<cnpj8>\\<módulo> (manifesto + resume);
 4. Ao final, os arquivos do CNPJ são COPIADOS para raw/ecac/<módulo> do
    engajamento — a fase `estruturar` faz custódia + parsing em seguida.

Os robôs rodam um por vez (sessão e-CAC não gosta de paralelismo) e são
síncronos aqui: o CLI espera cada um terminar.

Onde navegar antes de cada módulo (telas do e-CAC):
    perdcomp  → Restituição e Compensação > PER/DCOMP Web > Documentos
                Entregues > Pesquisar
    dctf      → Declarações e Demonstrativos > Consulta DCTF (pesquisa feita)
    dctfweb   → Declarações e Demonstrativos > DCTFWeb (lista de declarações)
    darf      → Pagamentos e Parcelamentos > Consulta Comprovante de
                Pagamento (pesquisa feita)
    simples   → Simples Nacional > PGDAS-D e DEFIS (área do contribuinte)
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from audit.core.modelo import normaliza_cnpj

from .ecac._infra import (SELENIUM_OK, REQUESTS_OK, _dl_get_company_paths,
                          _dl_is_debug_port_open, _dl_open_chrome_in_debug_mode)

_EXTENSOES_UTEIS = {".pdf", ".xml", ".zip", ".xlsx", ".txt"}


def _downloaders():
    from .ecac.darf import DarfDownloader
    from .ecac.dctf import DctfDownloader
    from .ecac.dctfweb import DctfWebDownloader
    from .ecac.perdcomp import PerdcompDownloader
    from .ecac.simples import SimplesNacionalDownloader
    return {
        "perdcomp": (PerdcompDownloader, "perdcomp_dir"),
        "dctf": (DctfDownloader, "dctf_dir"),
        "dctfweb": (DctfWebDownloader, "dctfweb_dir"),
        "darf": (DarfDownloader, "darf_dir"),
        "simples": (SimplesNacionalDownloader, "simples_dir"),
    }


MODULOS = ("perdcomp", "dctf", "dctfweb", "darf", "simples")


def coletar_engajamento(engaj_dir: str | Path, cnpj: str,
                        modulos: list[str] | None = None, porta: int = 9222,
                        abrir_chrome: bool = False,
                        log=print) -> dict:
    engaj_dir = Path(engaj_dir)
    cnpj = normaliza_cnpj(cnpj)
    modulos = [m.strip().lower() for m in (modulos or list(MODULOS))]
    invalidos = [m for m in modulos if m not in MODULOS]
    if invalidos:
        return {"erro": f"módulo(s) desconhecido(s): {invalidos} (use {MODULOS})"}

    if not (SELENIUM_OK and REQUESTS_OK):
        return {"erro": "dependências ausentes — instale com: "
                        "pip install selenium requests"}

    if abrir_chrome and not _dl_is_debug_port_open(porta):
        ok, msg = _dl_open_chrome_in_debug_mode(porta)
        log(("✓ " if ok else "✗ ") + msg)
        if ok:
            log("→ FAÇA LOGIN no e-CAC (certificado/procuração) na janela que "
                "abriu e navegue até o serviço do 1º módulo. Aguardando a "
                "porta de debug...")
            for _ in range(120):
                if _dl_is_debug_port_open(porta):
                    break
                time.sleep(1)

    if not _dl_is_debug_port_open(porta):
        return {"erro": f"Chrome em modo debug não encontrado na porta {porta}. "
                        f"Rode com --abrir-chrome ou abra manualmente: "
                        f'chrome.exe --remote-debugging-port={porta} '
                        f'--user-data-dir=C:\\chrome-debug-perdcomp'}

    registry = _downloaders()
    resumo: dict = {}
    for mod in modulos:
        classe, chave_dir = registry[mod]
        log(f"\n══ Módulo {mod.upper()} ══ (navegue até a tela correta e aguarde)")
        stats_finais: dict = {}

        def on_log(msg, level="info", _mod=mod):
            log(f"  [{_mod}] {msg}")

        def on_finished(ok, resumo_robo, _s=stats_finais):
            _s.update(resumo_robo or {})
            _s["_ok"] = ok

        robo = classe(debug_port=porta, on_log=on_log, on_finished=on_finished)
        robo.start()
        while robo.is_running():
            time.sleep(1)
        resumo[mod] = {k: v for k, v in stats_finais.items()
                       if not k.startswith("_")}
        resumo[mod]["ok"] = stats_finais.get("_ok", False)

    resumo["copiados"] = _copiar_para_engajamento(engaj_dir, cnpj, modulos, log)
    return resumo


def _copiar_para_engajamento(engaj_dir: Path, cnpj: str, modulos: list[str],
                             log=print) -> int:
    """Copia os downloads da pasta central (C:\\AgriTaxAudit) para raw/ecac.

    Inclui o que já existia de execuções anteriores (resume do manifesto) —
    o engajamento fica autossuficiente e o `estruturar` cuida da custódia."""
    try:
        paths = _dl_get_company_paths(cnpj)
    except ValueError as e:
        log(f"✗ {e}")
        return 0
    registry = _downloaders()
    copiados = 0
    for mod in modulos:
        origem = paths.get(registry[mod][1])
        if not origem or not origem.exists():
            continue
        destino = engaj_dir / "raw" / "ecac" / mod
        destino.mkdir(parents=True, exist_ok=True)
        for arq in sorted(origem.rglob("*")):
            if not arq.is_file() or arq.suffix.lower() not in _EXTENSOES_UTEIS:
                continue
            alvo = destino / arq.name
            if alvo.exists() and alvo.stat().st_size == arq.stat().st_size:
                continue
            shutil.copy2(arq, alvo)
            copiados += 1
    log(f"\n✓ {copiados} arquivo(s) novo(s) copiado(s) para {engaj_dir / 'raw' / 'ecac'}")
    log("→ Próximo passo: python pipeline.py estruturar --cliente ... --cnpj ... "
        "--data-base AAAA-MM-DD")
    return copiados
