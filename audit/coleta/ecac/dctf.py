"""Robô e-CAC: download em massa de DCTF (roda em thread, callbacks injetados — sem Tkinter).

Extraído do AgriTax Audit v5 consolidado, sem alterações de lógica (M4).
"""
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Callable

from ._infra import *  # noqa: F401,F403 — constantes/_dl_helpers/manifesto do v5
from ._infra import (REQUESTS_OK, SELENIUM_OK, _DownloadEntry, _DownloadManifest)
try:
    import requests
except ImportError:
    requests = None
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as SelOptions
    from selenium.webdriver.common.by import By
    from selenium.webdriver.remote.webelement import WebElement
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.common.exceptions import (
        ElementClickInterceptedException,
        StaleElementReferenceException,
        TimeoutException,
        WebDriverException,
        NoAlertPresentException,
        UnexpectedAlertPresentException,
    )
    SELENIUM_OK = True
except ImportError:
    SELENIUM_OK = False


class DctfDownloader:
    """Baixa em massa os recibos de DCTF clássica do eCAC.

    Fluxo: localiza a tabela de listagem -> para cada linha, clica no ícone
    de impressora -> aguarda nova aba abrir -> usa CDP Page.printToPDF para
    capturar o PDF -> salva e fecha a aba -> próxima linha.
    """

    # Palavras-chave pra localizar a aba/iframe correta da listagem
    URL_KEYWORDS = ("aplicacao.aspx", "id=14", "copia", "declaracao",
                    "declaracoes", "ecac")
    # Palavras-chave esperadas no cabeçalho da tabela de listagem
    # (baseado nas colunas visíveis: CNPJ | Período | Data Transmissão |
    #  Início | Fim | Tipo | Orig./Retif. Cancelador | Ações)
    TABLE_HEADER_KEYWORDS = ("período", "periodo", "transmissão", "transmissao",
                             "cnpj", "início", "inicio", "fim", "tipo",
                             "situação", "situacao", "retif", "cancelador",
                             "data", "dctf")

    def __init__(self,
                 out_dir: Optional[Path] = None,
                 debug_port: int = 9222,
                 status_filter: str = "todas",
                 on_log: Callable[[str, str], None] = None,
                 on_progress: Callable[[dict], None] = None,
                 on_finished: Callable[[bool, dict], None] = None,
                 legacy_dir_for_migration: Optional[Path] = None):
        # out_dir é OPCIONAL — definido após detectar CNPJ
        self.out_dir = Path(out_dir) if out_dir else None
        self.debug_port = debug_port
        # status_filter: "todas" | "ativas" | "ultima_versao"
        self.status_filter = status_filter
        self.on_log = on_log or (lambda *a, **k: None)
        self.on_progress = on_progress or (lambda *a, **k: None)
        self.on_finished = on_finished or (lambda *a, **k: None)
        self._cancel_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._driver = None
        self._stats = {
            "baixados": 0, "erros": 0, "pulados": 0, "filtrados": 0,
            "pagina_atual": 0, "linha_atual": 0, "linhas_pagina": 0,
        }
        self._downloaded_files: List[Path] = []
        self._cnpj: str = ""
        self._empresa: str = ""
        self._legacy_dir = legacy_dir_for_migration

    # -------- API pública --------
    def start(self) -> None:
        if not (SELENIUM_OK and REQUESTS_OK):
            self.on_finished(False,
                {"erro": "Dependências (selenium, requests) não instaladas."})
            return
        if self._thread and self._thread.is_alive():
            self._log("Já há um download em andamento.", "warn")
            return
        # Reseta flag de diagnóstico — vai logar a primeira linha desta execução
        DctfDownloader._print_icon_diagnosed = False
        self._cancel_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel_event.set()
        self._log("Cancelamento solicitado — encerrando após o item atual...", "warn")

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def get_downloaded_files(self) -> List[Path]:
        return list(self._downloaded_files)

    def get_cnpj(self) -> str:
        return self._cnpj

    def get_empresa(self) -> str:
        return self._empresa

    # -------- Internos --------
    def _log(self, msg: str, level: str = "info") -> None:
        self.on_log(msg, level)

    def _emit_progress(self) -> None:
        self.on_progress(dict(self._stats))

    def _save_debug_snapshot(self, tag: str) -> None:
        try:
            target_dir = self.out_dir or Path.cwd()
            target_dir.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            (target_dir / f"_debug_dctf_{tag}_{ts}.html").write_text(
                self._driver.page_source, encoding="utf-8")
            try:
                self._driver.save_screenshot(
                    str(target_dir / f"_debug_dctf_{tag}_{ts}.png"))
            except Exception:
                pass
            self._log(f"Snapshot debug salvo: _debug_dctf_{tag}_{ts}.html", "warn")
        except Exception:
            pass

    def _run(self) -> None:
        success = False
        summary: Dict = {}
        manifest: Optional[_DownloadManifest] = None

        try:
            self._log(f"Conectando ao Chrome em localhost:{self.debug_port}...", "info")
            try:
                self._driver = self._attach_to_chrome()
            except WebDriverException as e:
                self._log(f"✗ Não consegui conectar ao Chrome: {e}", "error")
                summary["erro"] = "Chrome inacessível"
                return

            self._log(f"✓ Conectado. Aba: {self._driver.title!r}", "ok")

            # ── Seleciona a aba das DCTFs ANTES de detectar o CNPJ ──
            # A detecção lê o header da aba atual; selecionar a aba do
            # módulo primeiro evita pegar o CNPJ de outra empresa
            # aberta noutra aba.
            if not self._switch_to_listing_context():
                self._log("✗ Tela de listagem de DCTFs não localizada. Garanta "
                          "que está na tela com a tabela de DCTFs visível "
                          "(URL contém 'Aplicacao.aspx?id=14').", "error")
                self._save_debug_snapshot("contexto_nao_localizado")
                summary["erro"] = "Listagem não localizada"
                return
            self._log("✓ Aba das DCTFs localizada", "ok")

            # ── Detecta CNPJ ativo no eCAC (na aba das DCTFs) ─────────
            self._log("Detectando CNPJ ativo no eCAC...", "info")
            cnpj, nome = _dl_detect_cnpj_from_ecac(self._driver, self._log)
            if not cnpj:
                self._log("✗ CNPJ não detectado no header do eCAC. "
                          "Garanta que está logado e na tela de DCTFs.",
                          "error")
                summary["erro"] = "CNPJ não detectado"
                return
            self._cnpj = cnpj
            self._empresa = nome
            self._log(f"✓ CNPJ detectado: {cnpj}"
                      + (f" — {nome}" if nome else ""), "ok")

            # ── Cria estrutura de pastas C:\AgriTaxAudit\<8digitos>\ ──
            try:
                paths = _dl_ensure_company_dirs(cnpj)
            except Exception as e:
                self._log(f"✗ Erro ao criar estrutura de pastas: {e}", "error")
                summary["erro"] = f"Erro pasta: {e}"
                return
            self.out_dir = paths["dctf_dir"]
            log_path = paths["log_dctf"]
            self._log(f"✓ Pasta: {self.out_dir}", "ok")
            self._log(f"   Log:  {log_path}", "info")

            # ── Carrega manifest e migra antigo se preciso ─────────────
            manifest = _DownloadManifest(log_path, files_root=self.out_dir)
            if self._legacy_dir and self._legacy_dir.exists():
                old_manifest = self._legacy_dir / "_manifest_dctf.json"
                if old_manifest.exists():
                    n = manifest.migrate_from(old_manifest, self._legacy_dir)
                    if n > 0:
                        self._log(f"✓ Migradas {n} entradas do manifesto antigo "
                                  f"({self._legacy_dir.name})", "ok")

            # Pré-popular lista de arquivos baixados (resume)
            for entry in manifest.entries.values():
                if entry.status == "baixado" and entry.arquivo:
                    p = self.out_dir / entry.arquivo
                    if p.exists():
                        self._downloaded_files.append(p)
            self._log(f"   Já baixados anteriormente: "
                      f"{len(self._downloaded_files)} PDF(s)", "info")

            # Re-seleciona o contexto da listagem (garantia)
            if not self._switch_to_listing_context():
                self._log("✗ Tela de listagem de DCTFs não localizada.",
                          "error")
                self._save_debug_snapshot("contexto_nao_localizado")
                summary["erro"] = "Listagem não localizada"
                return

            self._log("✓ Contexto da listagem pronto", "ok")
            if self.status_filter != "todas":
                filter_name = {
                    "ativas": "Apenas DCTFs ativas (não-canceladas)",
                    "ultima_versao": "Apenas a última versão de cada período",
                }.get(self.status_filter, self.status_filter)
                self._log(f"Filtro: {filter_name}", "info")

            page_num = 1
            # Cache pra filtro 'ultima_versao': período -> última entry vista
            ultima_versao_cache: Dict[str, _DownloadEntry] = {}

            while not self._cancel_event.is_set():
                self._stats["pagina_atual"] = page_num
                self._stats["linha_atual"] = 0
                self._log(f"━━━━━━ Página {page_num} ━━━━━━", "info")
                self._emit_progress()

                try:
                    WebDriverWait(self._driver, _DL_WAIT_TIMEOUT).until(
                        lambda d: self._find_listing_table() is not None
                        and len(self._find_rows(self._find_listing_table())) > 0
                    )
                except TimeoutException:
                    self._log(f"✗ Listagem da página {page_num} não carregou.", "error")
                    # Diagnóstico no log: mostra TODAS as tabelas vistas
                    self._log("Diagnóstico das tabelas encontradas na página:", "warn")
                    for line in self._diagnose_tables().splitlines():
                        self._log(line, "info")
                    self._save_debug_snapshot(f"page{page_num}_nao_carregou")
                    self._log(f"Snapshot HTML salvo. Compartilhe o arquivo "
                              f"_debug_dctf_*.html com o suporte para ajuste "
                              f"de seletores.", "warn")
                    break

                entries = self._collect_entries()
                self._stats["linhas_pagina"] = len(entries)
                self._log(f"  {len(entries)} entrada(s) detectada(s)", "info")
                self._emit_progress()

                if not entries:
                    self._log("Página vazia — encerrando.", "warn")
                    # Mesmo diagnóstico: a tabela foi achada mas sem linhas?
                    self._log("Diagnóstico das tabelas encontradas na página:", "warn")
                    for line in self._diagnose_tables().splitlines():
                        self._log(line, "info")
                    self._save_debug_snapshot(f"page{page_num}_vazia")
                    break

                # Pré-processa pro filtro 'ultima_versao': encontra a entry
                # mais recente de cada período (pela data de transmissão)
                if self.status_filter == "ultima_versao":
                    for e in entries:
                        if not e.periodo:
                            continue
                        prev = ultima_versao_cache.get(e.periodo)
                        if prev is None or self._eh_mais_recente(e, prev):
                            ultima_versao_cache[e.periodo] = e

                for idx in range(len(entries)):
                    if self._cancel_event.is_set():
                        break
                    self._stats["linha_atual"] = idx + 1
                    self._emit_progress()

                    table = self._find_listing_table()
                    if not table:
                        break
                    rows = self._find_rows(table)
                    if idx >= len(rows):
                        continue
                    row = rows[idx]
                    entry = self._parse_row(row, idx) or entries[idx]
                    entry.pagina = page_num
                    entry.kind = "DCTF"
                    entry.cnpj = self._cnpj

                    # Aplica filtro
                    if not self._passa_filtro(entry, ultima_versao_cache):
                        self._log(f"  [FILTRADO] {entry.periodo} | "
                                  f"{entry.tipo or '?'} (não passou no filtro)",
                                  "info")
                        self._stats["filtrados"] += 1
                        self._emit_progress()
                        continue

                    if manifest.is_done(entry.numero):
                        self._log(f"  [PULAR] {entry.numero} — já baixado", "info")
                        self._stats["pulados"] += 1
                        self._emit_progress()
                        continue

                    label = (f"{entry.periodo or '?'} | "
                             f"{entry.tipo or '?'} | rec {entry.numero}")
                    self._log(f"  [{idx+1}/{len(entries)}] {label}", "info")

                    try:
                        ok = self._download_one(row, entry)
                    except Exception as ex:
                        self._log(f"    ✗ erro inesperado: {ex}", "error")
                        entry.status = "erro"
                        entry.erro = str(ex)
                        ok = False

                    # Verifica o CNPJ DENTRO do PDF e move pra pasta
                    # certa se for de outra empresa.
                    if ok and entry.arquivo:
                        try:
                            pdf_p = self.out_dir / entry.arquivo
                            st, dest = _dl_relocate_pdf_by_cnpj(
                                pdf_p, self._cnpj, "dctf_dir", self._log)
                            if st == "movido":
                                entry.cnpj = (_dl_read_cnpj_from_pdf(dest)
                                              or "")
                                entry.arquivo = ""
                                entry.status = "movido"
                                entry.erro = ("PDF de outra empresa — "
                                              f"movido para {dest}")
                        except Exception as ex:
                            self._log(f"    ⚠ erro ao verificar CNPJ "
                                      f"do PDF: {ex}", "warn")

                    manifest.upsert(entry)
                    if ok and entry.status != "movido":
                        self._stats["baixados"] += 1
                        if entry.arquivo:
                            self._downloaded_files.append(
                                self.out_dir / entry.arquivo)
                    elif entry.status == "movido":
                        self._stats["baixados"] += 1
                        self._log("    ↪ contabilizado na pasta da "
                                  "empresa correta", "info")
                    else:
                        self._stats["erros"] += 1
                        self._log(f"    ✗ ERRO: {entry.erro}", "error")
                    self._emit_progress()
                    time.sleep(_DL_DELAY_BETWEEN_DOWNLOADS)

                # A listagem de DCTFs é uma tabela ÚNICA, sem
                # paginação. Já processamos todas as linhas no
                # 'for' acima — encerra o loop. Sem este break, o
                # 'while' reprocessaria a mesma tabela para sempre
                # (loop infinito).
                if not self._go_to_next_page():
                    self._log("Listagem concluída.", "info")
                    break
                page_num += 1
            success = not self._cancel_event.is_set()
        except Exception as e:
            self._log(f"✗ Erro fatal: {e}", "error")
            summary["erro"] = str(e)
        finally:
            if manifest is not None and self.out_dir is not None:
                try:
                    manifest.export_csv(self.out_dir / "_resumo_dctf.csv")
                except Exception:
                    pass
            summary.update(self._stats)
            summary["arquivos_baixados"] = list(self._downloaded_files)
            summary["cancelado"] = self._cancel_event.is_set()
            summary["cnpj"] = self._cnpj
            summary["empresa"] = self._empresa
            summary["pasta_saida"] = str(self.out_dir) if self.out_dir else ""
            self.on_finished(success, summary)

    def _attach_to_chrome(self):
        options = SelOptions()
        options.add_experimental_option("debuggerAddress",
                                        f"localhost:{self.debug_port}")
        return webdriver.Chrome(options=options)

    def _switch_to_listing_context(self) -> bool:
        """Procura a aba/iframe da listagem (Aplicacao.aspx?id=14)."""
        # Critério prioritário: URL com 'id=14'. Se for, ainda checa iframes.
        for handle in self._driver.window_handles:
            try:
                self._driver.switch_to.window(handle)
            except Exception:
                continue
            url = (self._driver.current_url or "").lower()
            if "aplicacao.aspx" in url and "id=14" in url:
                # Tabela direto no documento principal?
                if self._find_listing_table() is not None:
                    return True
                # Senão, desce nos iframes dessa aba
                try:
                    self._driver.switch_to.default_content()
                    iframes = self._driver.find_elements(By.TAG_NAME, "iframe")
                    self._log(f"   URL Aplicacao.aspx?id=14 OK, mas tabela "
                              f"não está no nível principal. Tentando "
                              f"{len(iframes)} iframe(s)...", "info")
                    for i, fr in enumerate(iframes):
                        try:
                            self._driver.switch_to.frame(fr)
                            if self._find_listing_table() is not None:
                                self._log(f"   ✓ Tabela achada em iframe[{i}]",
                                          "ok")
                                return True
                            self._driver.switch_to.default_content()
                        except Exception:
                            try:
                                self._driver.switch_to.default_content()
                            except Exception:
                                pass
                            continue
                    # Volta pra aba sem entrar em iframe
                    self._driver.switch_to.default_content()
                except Exception:
                    pass
                # Última tentativa: aceita o contexto da aba mesmo sem achar
                # tabela (vai falhar adiante, mas com diagnóstico apropriado)
                return True

        # Critério secundário: qualquer URL/título com palavras-chave
        for handle in self._driver.window_handles:
            try:
                self._driver.switch_to.window(handle)
            except Exception:
                continue
            url = (self._driver.current_url or "").lower()
            title = (self._driver.title or "").lower()
            if any(kw in url for kw in self.URL_KEYWORDS) or \
               any(kw in title for kw in self.URL_KEYWORDS):
                # Verifica se tem tabela compatível
                if self._find_listing_table() is not None:
                    return True
            # Tenta iframes
            try:
                self._driver.switch_to.default_content()
                iframes = self._driver.find_elements(By.TAG_NAME, "iframe")
                for fr in iframes:
                    try:
                        self._driver.switch_to.frame(fr)
                        if self._find_listing_table() is not None:
                            return True
                        self._driver.switch_to.default_content()
                    except Exception:
                        try:
                            self._driver.switch_to.default_content()
                        except Exception:
                            pass
                        continue
            except Exception:
                pass
        return False

    def _table_looks_like_listing(self, tbl) -> bool:
        """Verifica se a <table> parece a listagem de DCTFs."""
        try:
            if not tbl.is_displayed():
                return False
            # Pega texto do header (th ou primeiro tr de td)
            header_text = " ".join(
                th.text.lower() for th in tbl.find_elements(By.XPATH, ".//th")
            )
            if not header_text:
                first_row = tbl.find_elements(By.XPATH, ".//tr[1]/td")
                header_text = " ".join(c.text.lower() for c in first_row)
            # 2 keywords no cabeçalho já indicam tabela compatível
            matches = sum(1 for kw in self.TABLE_HEADER_KEYWORDS
                          if kw in header_text)
            return matches >= 2
        except Exception:
            return False

    def _row_looks_like_dctf_data(self, row) -> bool:
        """Verifica se uma <tr> tem cara de linha de dados de DCTF
        (CNPJ formatado E mês/ano OU número visível)."""
        try:
            txt = (row.text or "").lower()
            if not txt:
                return False
            # Tem CNPJ formatado?
            has_cnpj = bool(re.search(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", txt))
            # Tem mês/ano em formato pt-BR?
            has_periodo = any(m in txt for m in (
                "janeiro", "fevereiro", "março", "marco", "abril", "maio",
                "junho", "julho", "agosto", "setembro", "outubro",
                "novembro", "dezembro", "trimestre"))
            # Tem palavra de status?
            has_situacao = any(w in txt for w in (
                "original", "retificadora", "retif", "ativa",
                "cancelada", "normal"))
            return has_cnpj and (has_periodo or has_situacao)
        except Exception:
            return False

    def _table_looks_like_listing_by_rows(self, tbl) -> bool:
        """Plano B: verifica se a tabela contém linhas com cara de DCTF
        (mesmo que o cabeçalho não tenha keywords claras)."""
        try:
            if not tbl.is_displayed():
                return False
            rows = tbl.find_elements(By.XPATH, ".//tr")
            if len(rows) < 2:
                return False
            # Conta quantas linhas têm cara de DCTF
            data_rows = sum(1 for r in rows
                            if self._row_looks_like_dctf_data(r))
            # Pelo menos 2 linhas de dados pra ser considerada
            return data_rows >= 2
        except Exception:
            return False

    def _find_listing_table(self):
        """Localiza a tabela com a listagem de DCTFs.

        Estratégias em ordem de confiança:
          1. Cabeçalho com >=2 keywords conhecidas
          2. Tabela com >=2 linhas que TÊM cara de dados de DCTF (CNPJ + mês)
          3. Maior tabela visível com >= 3 linhas
        """
        try:
            tables = self._driver.find_elements(By.TAG_NAME, "table")
        except Exception:
            return None

        # Estratégia 1: cabeçalho compatível
        for tbl in tables:
            try:
                if self._table_looks_like_listing(tbl):
                    return tbl
            except StaleElementReferenceException:
                continue

        # Estratégia 2: linhas com cara de DCTF (mais permissiva)
        for tbl in tables:
            try:
                if self._table_looks_like_listing_by_rows(tbl):
                    return tbl
            except StaleElementReferenceException:
                continue

        # Estratégia 3: maior tabela visível
        largest, largest_n = None, 0
        for tbl in tables:
            try:
                if not tbl.is_displayed():
                    continue
                n = len(tbl.find_elements(By.XPATH, ".//tbody/tr"))
                if n == 0:
                    n = max(0, len(tbl.find_elements(By.TAG_NAME, "tr")) - 1)
                if n > largest_n:
                    largest, largest_n = tbl, n
            except StaleElementReferenceException:
                continue
        if largest and largest_n >= 3:
            return largest
        return None

    def _diagnose_tables(self) -> str:
        """Gera um relatório de TODAS as tabelas vistas na página.
        Usado pra debug quando _find_listing_table retorna None.
        """
        lines = []
        try:
            tables = self._driver.find_elements(By.TAG_NAME, "table")
            lines.append(f"  Total de <table> na página: {len(tables)}")
            for i, tbl in enumerate(tables[:15]):  # cap em 15
                try:
                    visible = tbl.is_displayed()
                    n_tr = len(tbl.find_elements(By.TAG_NAME, "tr"))
                    n_tbody = len(tbl.find_elements(By.XPATH, ".//tbody/tr"))
                    th_text = " ".join(
                        th.text.lower()
                        for th in tbl.find_elements(By.XPATH, ".//th"))[:80]
                    first_td = " | ".join(
                        c.text[:25] for c in
                        tbl.find_elements(By.XPATH, ".//tr[1]/td")[:6])[:120]
                    matches_header = sum(1 for kw in self.TABLE_HEADER_KEYWORDS
                                         if kw in th_text)
                    data_rows = sum(1 for r in tbl.find_elements(By.TAG_NAME, "tr")
                                    if self._row_looks_like_dctf_data(r))
                    lines.append(
                        f"  [{i}] visible={visible} tr={n_tr} tbody={n_tbody} "
                        f"th_kws={matches_header} data_rows={data_rows}")
                    if th_text.strip():
                        lines.append(f"      <th>: {th_text[:100]!r}")
                    if first_td.strip():
                        lines.append(f"      tr[1]: {first_td!r}")
                except Exception as e:
                    lines.append(f"  [{i}] erro inspeção: {e}")
        except Exception as e:
            lines.append(f"  Erro listando tabelas: {e}")
        return "\n".join(lines)

    @staticmethod
    def _safe_displayed(el) -> bool:
        try:
            return el.is_displayed()
        except StaleElementReferenceException:
            return False

    def _find_rows(self, table) -> List:
        """Retorna as linhas de DADOS (ignora header).

        Lida com tabelas ASP.NET típicas que podem ter estruturas como:
          <table><tr><td><table><tr><td>...</td></tr></table></td></tr></table>
        Nesse caso, as <tr> "de fora" não têm <td> diretos (só aninhados).
        Pegamos as <tr> que TÊM <td> direto via XPath './/tr[td]'.
        """
        if not table:
            return []
        try:
            # Caminho 1: <tr> que TEM <td> direto e está dentro da tabela
            # (./tr[td] = tr direta com td direto = exclui aninhadas)
            # Mas como tabela pode ter tbody/thead, usamos descendente
            all_trs = table.find_elements(By.TAG_NAME, "tr")
            rows_with_td = []
            for tr in all_trs:
                try:
                    # Conta SOMENTE <td> filhos diretos (não netos)
                    direct_tds = tr.find_elements(By.XPATH, "./td")
                    if direct_tds:
                        rows_with_td.append((tr, len(direct_tds)))
                except StaleElementReferenceException:
                    continue

            if not rows_with_td:
                return []

            # Filtra: visível, com texto, e com >= 3 td (pra eliminar header
            # e linhas de paginação que só têm 1-2 colunas)
            filtered = []
            max_tds = max(c for _, c in rows_with_td)
            min_acceptable = max(3, max_tds - 2)  # tolera variações de coluna
            for tr, n_td in rows_with_td:
                if n_td < min_acceptable:
                    continue
                if not self._safe_displayed(tr):
                    continue
                try:
                    text = (tr.text or "").strip()
                except StaleElementReferenceException:
                    continue
                if not text:
                    continue
                # Heurística: linha de dados tem CNPJ-like OU mês/ano
                if (re.search(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", text)
                    or re.search(
                        r"(janeiro|fevereiro|março|marco|abril|maio|junho|"
                        r"julho|agosto|setembro|outubro|novembro|dezembro)/?\s*\d{4}",
                        text, re.IGNORECASE)):
                    filtered.append(tr)
            return filtered
        except Exception:
            return []

    def _parse_row(self, row, idx: int = 0) -> Optional[_DownloadEntry]:
        """Extrai campos da linha. Layout esperado:
        CNPJ | Período (Janeiro/2023) | Data Transmissão | Início | Fim |
        Tipo (Normal/...) | Situação (Original/Ativa, Retificadora/Cancelada,
        etc.) | Ações (lupa, impressora, documento)
        """
        try:
            cells = row.find_elements(By.TAG_NAME, "td")
            if not cells:
                return None
            texts = [c.text.strip() for c in cells]
            joined = " | ".join(texts)
        except StaleElementReferenceException:
            return None

        if not texts:
            return None

        # CNPJ — formato XX.XXX.XXX/XXXX-XX
        cnpj = ""
        m = re.search(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", joined)
        if m:
            cnpj = m.group(0)

        # Período — "Janeiro/2023", "Dezembro/2024", "1º Trimestre/2024"
        periodo = ""
        for t in texts:
            m = re.search(
                r"(Janeiro|Fevereiro|Março|Marco|Abril|Maio|Junho|Julho|"
                r"Agosto|Setembro|Outubro|Novembro|Dezembro)/(\d{4})",
                t, re.IGNORECASE)
            if m:
                periodo = f"{m.group(1).capitalize()}/{m.group(2)}"
                break
            m = re.search(r"\d[ºo°]?\s*Trimestre\s*/?\s*(\d{4})",
                          t, re.IGNORECASE)
            if m:
                periodo = re.sub(r"\s+", " ", t).strip()
                break

        # Data de transmissão — DD/MM/AAAA. Pega a primeira após o período
        data = ""
        all_dates = _DL_DATE_BR_RE.findall(joined)
        if all_dates:
            data = all_dates[0]

        # Tipo (Normal, Retificadora, etc.)
        tipo = ""
        for t in texts:
            for kw in ("Normal", "Retificadora", "Original", "Ativa",
                       "Cancelada", "Trimestral", "Mensal"):
                if kw.lower() in t.lower():
                    tipo = t
                    break
            if tipo:
                break

        # Situação completa (Original/Ativa, Retificadora/Cancelada, ...)
        situacao = ""
        for t in texts:
            if "/" in t and any(w in t.lower() for w in
                                ("ativa", "cancelada", "original",
                                 "retificadora", "retif")):
                situacao = t
                break

        # Número do recibo: tenta achar uma sequência de dígitos longa
        # Como ele NÃO aparece visivelmente na listagem (só após abrir),
        # vamos usar o índice + período como chave única de manifest
        numero = situacao or f"linha_{idx}_{periodo or 'sem_periodo'}"

        # Sanitiza o "número" pra ser usado como chave (sem caracteres ruins)
        chave = f"{periodo or 'sem_periodo'}__{situacao or 'sem_status'}__{data or 'sem_data'}"

        return _DownloadEntry(
            numero=chave, tipo=tipo, data_transmissao=data,
            periodo=periodo, kind="DCTF",
            tipo_credito=situacao,  # reaproveita campo pra guardar status
        )

    def _collect_entries(self) -> List[_DownloadEntry]:
        table = self._find_listing_table()
        if not table:
            return []
        rows = self._find_rows(table)
        out: List[_DownloadEntry] = []
        for i, r in enumerate(rows):
            e = self._parse_row(r, i)
            if e:
                out.append(e)
        return out

    def _eh_mais_recente(self, e1: _DownloadEntry, e2: _DownloadEntry) -> bool:
        """Compara duas entries do mesmo período. Retorna True se e1 é mais
        recente (data de transmissão maior)."""
        from datetime import datetime
        def to_date(s):
            try:
                return datetime.strptime(s, "%d/%m/%Y")
            except Exception:
                return datetime.min
        return to_date(e1.data_transmissao) > to_date(e2.data_transmissao)

    def _passa_filtro(self, entry: _DownloadEntry,
                      ultima_versao_cache: Dict[str, _DownloadEntry]) -> bool:
        """Aplica o filtro de status configurado."""
        situacao = (entry.tipo_credito or "").lower()
        if self.status_filter == "todas":
            return True
        if self.status_filter == "ativas":
            return "cancel" not in situacao
        if self.status_filter == "ultima_versao":
            if not entry.periodo:
                return True
            mais_recente = ultima_versao_cache.get(entry.periodo)
            if mais_recente is None:
                return True
            return entry.numero == mais_recente.numero
        return True

    # ---- Estado pra diagnóstico (só loga 1x na primeira linha) ----
    _print_icon_diagnosed: bool = False

    def _log_row_inspection(self, row, idx_label: str = "") -> None:
        """Loga (modo diagnóstico) tudo que existe de clicável na linha.
        Chamado UMA VEZ na primeira linha pra ajudar a achar o ícone certo.
        """
        try:
            self._log(f"   ── DIAGNÓSTICO da linha {idx_label} ──", "warn")
            # Info bruta sobre a linha
            try:
                tag = row.tag_name
                row_class = row.get_attribute("class") or ""
                row_id = row.get_attribute("id") or ""
                row_text = (row.text or "").strip()
                self._log(f"   Tag: <{tag}> id={row_id!r} class={row_class!r}",
                          "info")
                self._log(f"   Texto: {row_text[:150]!r}", "info")
            except Exception:
                pass

            tds = row.find_elements(By.TAG_NAME, "td")
            direct_tds = row.find_elements(By.XPATH, "./td")
            self._log(f"   Total de <td> descendentes: {len(tds)}", "info")
            self._log(f"   Total de <td> filhos diretos: {len(direct_tds)}",
                      "info")

            # Se não tem td direto, a "row" pode ser um wrapper. Inspeciona
            # qualquer descendente clicável
            if not direct_tds:
                self._log("   ⚠ Linha sem <td> direto — provavelmente é "
                          "wrapper/aninhada", "warn")
                # Tenta inspecionar clicáveis em qualquer descendente
                clickables = row.find_elements(
                    By.XPATH, ".//a | .//img | .//input[@type='image'] | "
                    ".//button | .//*[@onclick]")
                self._log(f"   Total de clicáveis (descendentes): "
                          f"{len(clickables)}", "info")
                for ci, el in enumerate(clickables[:10]):
                    try:
                        attrs = {
                            "tag": el.tag_name,
                            "alt": (el.get_attribute("alt") or "")[:30],
                            "title": (el.get_attribute("title") or "")[:30],
                            "src": (el.get_attribute("src") or "").split("/")[-1][:40],
                            "class": (el.get_attribute("class") or "")[:30],
                            "onclick": (el.get_attribute("onclick") or "")[:50],
                            "href": (el.get_attribute("href") or "")[:50],
                        }
                        non_empty = {k: v for k, v in attrs.items() if v}
                        self._log(f"     [{ci}] {non_empty}", "info")
                    except Exception:
                        continue
                return

            # Caso normal: itera TDs filhos diretos
            for ti, td in enumerate(direct_tds):
                clickables = td.find_elements(
                    By.XPATH, ".//a | .//img | .//input[@type='image'] | "
                    ".//button | .//*[@onclick]")
                td_text = (td.text or "").strip()[:40]
                if clickables or td_text:
                    self._log(f"   td[{ti}] text={td_text!r} "
                              f"clicáveis={len(clickables)}", "info")
                for ci, el in enumerate(clickables[:5]):
                    try:
                        attrs = {
                            "tag": el.tag_name,
                            "alt": (el.get_attribute("alt") or "")[:30],
                            "title": (el.get_attribute("title") or "")[:30],
                            "src": (el.get_attribute("src") or "").split("/")[-1][:40],
                            "class": (el.get_attribute("class") or "")[:30],
                            "onclick": (el.get_attribute("onclick") or "")[:50],
                            "href": (el.get_attribute("href") or "")[:50],
                        }
                        non_empty = {k: v for k, v in attrs.items() if v}
                        self._log(f"     [{ci}] {non_empty}", "info")
                    except Exception:
                        continue
        except Exception as e:
            self._log(f"   (falha no diagnóstico: {e})", "warn")

    def _score_print_candidate(self, el, position_in_row: int = -1) -> int:
        """Retorna pontuação 0-100 indicando o quanto este elemento
        parece o ícone de IMPRESSORA. Quanto maior, mais provável."""
        try:
            attrs_text = " ".join([
                el.get_attribute("alt") or "",
                el.get_attribute("title") or "",
                el.get_attribute("aria-label") or "",
                el.get_attribute("src") or "",
                el.get_attribute("class") or "",
                el.get_attribute("id") or "",
                el.get_attribute("name") or "",
                el.get_attribute("onclick") or "",
                el.get_attribute("href") or "",
            ]).lower()
        except Exception:
            return 0

        score = 0
        # Sinais fortíssimos: termos exatos de impressão
        for kw in ("imprimir", "imprime", "impressao", "impressão",
                   "comprovante", "recibo"):
            if kw in attrs_text:
                score += 50
        # Sinais médios: 'print' (pode ser "print", "printer", "printDoc"...)
        for kw in ("print", "printer", "imprime", "impr"):
            if kw in attrs_text:
                score += 30
        # Ícone fontawesome / unicode comum pra impressora
        for kw in ("fa-print", "icon-print", "printer", "iconeimpr",
                   "btn-print", "btnimprimir"):
            if kw in attrs_text:
                score += 40
        # Penalização: se for claramente lupa/visualizar/cancelar/retificar
        for kw in ("visualizar", "lupa", "magnify", "search", "view",
                   "cancelar", "retificar", "retif", "excluir", "delete"):
            if kw in attrs_text:
                score -= 30
        return max(0, score)

    def _click_print_icon(self, row) -> bool:
        """Clica no ícone de IMPRESSORA da linha.

        Estratégia:
        1. Coleta TODOS os elementos clicáveis da linha inteira
        2. Pontua cada um com _score_print_candidate
        3. Escolhe o de maior pontuação (>= 30)
        4. Se nenhum tem score, usa fallback posicional
        """
        # Diagnóstico só na primeira chamada (vai aparecer no log preto)
        if not DctfDownloader._print_icon_diagnosed:
            self._log_row_inspection(row, "(primeira)")
            DctfDownloader._print_icon_diagnosed = True

        # Coleta clicáveis com posição (qual td) — funciona com td direto OU
        # caindo na linha inteira como fallback
        candidates: List[tuple] = []  # (score, td_idx, el)
        try:
            direct_tds = row.find_elements(By.XPATH, "./td")
            if direct_tds:
                for td_idx, td in enumerate(direct_tds):
                    clickables = td.find_elements(
                        By.XPATH, ".//a | .//img | .//input[@type='image'] | "
                        ".//button | .//*[@onclick]")
                    for el in clickables:
                        score = self._score_print_candidate(el, td_idx)
                        candidates.append((score, td_idx, el))
            else:
                # Linha sem td direto — pega qualquer clicável descendente
                clickables = row.find_elements(
                    By.XPATH, ".//a | .//img | .//input[@type='image'] | "
                    ".//button | .//*[@onclick]")
                for ci, el in enumerate(clickables):
                    score = self._score_print_candidate(el, ci)
                    # Aproxima a "td_idx" pela posição relativa
                    candidates.append((score, ci, el))
        except Exception:
            return False

        if not candidates:
            return False

        # Ordena por score decrescente
        candidates.sort(key=lambda t: t[0], reverse=True)
        top_score = candidates[0][0]

        # Escolhe o melhor candidato com score >= 30 (sinal claro)
        if top_score >= 30:
            for score, td_idx, el in candidates:
                if score < 30:
                    break
                if self._click_element(el):
                    return True
            # Nenhum dos bons candidatos clicou — cai no fallback

        # Fallback 1: 2º elemento do "grupo" (td ou cluster) com mais clicáveis
        # (provável coluna de ações com lupa, impressora, documento)
        td_counts: Dict[int, int] = {}
        for _, td_idx, _ in candidates:
            td_counts[td_idx] = td_counts.get(td_idx, 0) + 1
        if td_counts:
            actions_td = max(td_counts, key=lambda k: td_counts[k])
            els_in_actions_td = [el for _, ti, el in candidates
                                 if ti == actions_td]
            if len(els_in_actions_td) >= 2:
                if self._click_element(els_in_actions_td[1]):
                    return True
            if len(els_in_actions_td) == 1:
                if self._click_element(els_in_actions_td[0]):
                    return True

        # Fallback 2: tenta cada candidato em ordem (último recurso)
        for _, _, el in candidates:
            if self._click_element(el):
                return True

        return False

    def _click_element(self, el) -> bool:
        """Tenta clicar no elemento por várias estratégias."""
        # Scroll into view
        try:
            self._driver.execute_script(
                "arguments[0].scrollIntoView({block:'center', inline:'center'});",
                el)
        except Exception:
            pass
        # Click direto
        try:
            el.click()
            return True
        except ElementClickInterceptedException:
            pass
        except Exception:
            pass
        # JS click
        try:
            self._driver.execute_script("arguments[0].click();", el)
            return True
        except Exception:
            pass
        # Ancestor clicável
        try:
            anc = el.find_element(
                By.XPATH, "./ancestor::a[1] | ./ancestor::button[1]")
            anc.click()
            return True
        except Exception:
            pass
        return False

    def _capture_pdf_via_cdp(self, target_path: Path) -> bool:
        """Captura o PDF da aba ATIVA usando Chrome DevTools Protocol.
        Equivale ao 'Imprimir → Salvar como PDF' do Chrome, sem diálogo.
        """
        try:
            # Aguarda a página renderizar minimamente
            time.sleep(1.5)
            try:
                WebDriverWait(self._driver, 15).until(
                    lambda d: d.execute_script(
                        "return document.readyState") == "complete"
                )
            except TimeoutException:
                self._log("    aviso: readyState não chegou em 'complete'", "warn")
            time.sleep(0.5)

            # Page.printToPDF é o equivalente CDP de "Salvar como PDF"
            result = self._driver.execute_cdp_cmd("Page.printToPDF", {
                "printBackground": True,
                "preferCSSPageSize": True,
                "marginTop":    0.4,
                "marginBottom": 0.4,
                "marginLeft":   0.4,
                "marginRight":  0.4,
            })
            import base64 as _b64
            pdf_bytes = _b64.b64decode(result["data"])
            target_path.write_bytes(pdf_bytes)
            return target_path.stat().st_size > 1000  # PDFs vazios têm <1KB
        except Exception as e:
            self._log(f"    erro Page.printToPDF: {e}", "error")
            return False

    def _check_and_handle_alert(self) -> tuple:
        """Verifica se há um alert JavaScript pendente.

        Retorna (had_alert, alert_text, is_session_expired).
        Se houver alert, ele é DISMISSADO (aceito) pra liberar o Selenium.
        """
        try:
            alert = self._driver.switch_to.alert
        except NoAlertPresentException:
            return False, "", False
        except Exception:
            return False, "", False

        try:
            text = alert.text or ""
        except Exception:
            text = ""

        # Detecta sessão expirada por palavras-chave (acentos podem variar)
        text_low = text.lower().replace("ã", "a").replace("é", "e")
        is_expired = any(kw in text_low for kw in (
            "sessao expirada", "sessão expirada",
            "sessao expirado", "logue-se",
            "logue se novamente", "logar novamente",
            "sessao", "expirou",
        ))

        try:
            alert.accept()
        except Exception:
            try:
                alert.dismiss()
            except Exception:
                pass
        return True, text, is_expired

    def _download_one(self, row, entry: _DownloadEntry) -> bool:
        """Baixa o PDF da linha. Lida com 3 cenários:
          A) Clique abre NOVA ABA com a Impressão da Declaração
          B) Clique faz a ABA ATUAL navegar pra a Impressão
          C) Aparece um ALERT JavaScript (ex: "Sessão expirada")
        """
        main_window = self._driver.current_window_handle
        handles_before = set(self._driver.window_handles)

        # URL atual ANTES do clique (pra detectar mudança na mesma aba)
        try:
            url_iframe_before = self._driver.current_url
        except Exception:
            url_iframe_before = ""
        try:
            url_top_before = self._driver.execute_script(
                "return window.top.location.href")
        except Exception:
            url_top_before = url_iframe_before

        if not self._click_print_icon(row):
            entry.status = "erro"
            entry.erro = "Ícone de impressora não localizado na linha."
            return False

        # Loop de espera: nova aba | URL muda | alert aparece
        deadline = time.time() + 15.0
        new_handle = None
        same_tab_navigated = False
        alert_text = ""
        session_expired = False

        while time.time() < deadline:
            time.sleep(0.3)

            # 1) Verifica alert (precisa ser checado primeiro pq bloqueia tudo)
            had_alert, alert_text, session_expired = self._check_and_handle_alert()
            if had_alert:
                self._log(f"    ⚠ Alert do navegador: {alert_text!r}", "warn")
                if session_expired:
                    # Sinaliza pra abortar toda a execução
                    self._cancel_event.set()
                    self._log("    ✗ SESSÃO DO eCAC EXPIROU. "
                              "Logue-se novamente no Chrome e refaça a "
                              "consulta de DCTFs antes de tentar de novo.",
                              "error")
                    entry.status = "erro"
                    entry.erro = "Sessão do eCAC expirada"
                    return False
                # Outros alerts: foi aceito; continua observando
                continue

            # 2) Verifica nova aba
            try:
                new_handles = set(self._driver.window_handles) - handles_before
                if new_handles:
                    new_handle = next(iter(new_handles))
                    break
            except UnexpectedAlertPresentException:
                continue  # alert apareceu durante a leitura — trata na próxima iter
            except Exception:
                pass

            # 3) Verifica mudança de URL na mesma aba (caso ASP.NET selecionaServico)
            try:
                cur_top = self._driver.execute_script(
                    "return window.top.location.href")
                if cur_top and cur_top != url_top_before:
                    same_tab_navigated = True
                    self._log(f"    URL mudou: {cur_top[:80]}", "info")
                    break
            except UnexpectedAlertPresentException:
                continue
            except Exception:
                pass

        if not new_handle and not same_tab_navigated:
            entry.status = "erro"
            entry.erro = ("Clique não causou navegação detectável "
                          "(pode ter dado outro alert ou erro silencioso)")
            return False

        # ── CASO A: Nova aba ──────────────────────────────────────────
        if new_handle:
            try:
                self._driver.switch_to.window(new_handle)

                # Verifica alert NA NOVA ABA também (eCAC pode mostrar
                # "Sessão expirada" só após abrir a nova aba)
                time.sleep(0.5)
                had_alert, alert_text, session_expired = \
                    self._check_and_handle_alert()
                if had_alert:
                    self._log(f"    ⚠ Alert na nova aba: {alert_text!r}",
                              "warn")
                    if session_expired:
                        self._cancel_event.set()
                        self._log("    ✗ SESSÃO DO eCAC EXPIROU. "
                                  "Logue-se novamente.", "error")
                        entry.status = "erro"
                        entry.erro = "Sessão do eCAC expirada"
                        return False

                # Espera a página carregar minimamente
                try:
                    WebDriverWait(self._driver, 15).until(
                        lambda d: d.execute_script(
                            "return document.readyState") == "complete"
                    )
                except (TimeoutException, UnexpectedAlertPresentException):
                    pass
                time.sleep(1.0)

                # Re-verifica alert depois do load
                had_alert, alert_text, session_expired = \
                    self._check_and_handle_alert()
                if had_alert and session_expired:
                    self._cancel_event.set()
                    entry.status = "erro"
                    entry.erro = "Sessão do eCAC expirada (pós-load)"
                    return False

                url = self._driver.current_url
                self._log(f"    nova aba: {url[:80]}", "info")

                target = self.out_dir / (entry.safe_basename() + ".pdf")
                if target.exists():
                    ts = time.strftime("%H%M%S")
                    target = self.out_dir / (
                        entry.safe_basename() + f"_{ts}.pdf")

                ok = self._capture_pdf_via_cdp(target)
                if ok:
                    entry.arquivo = target.name
                    entry.status = "baixado"
                    size_kb = target.stat().st_size / 1024
                    self._log(f"    ✓ salvo: {target.name} ({size_kb:.0f} KB)",
                              "ok")
                else:
                    entry.status = "erro"
                    entry.erro = "Falha ao capturar PDF via CDP."
                return ok
            finally:
                try:
                    self._driver.close()
                except Exception:
                    pass
                try:
                    self._driver.switch_to.window(main_window)
                except Exception:
                    pass
                self._switch_to_listing_context()

        # ── CASO B: Mesma aba navegou pra Impressão da Declaração ─────
        try:
            try:
                WebDriverWait(self._driver, 15).until(
                    lambda d: d.execute_script(
                        "return document.readyState") == "complete"
                )
            except (TimeoutException, UnexpectedAlertPresentException):
                pass
            time.sleep(1.5)

            # Verifica alert também
            had_alert, alert_text, session_expired = \
                self._check_and_handle_alert()
            if had_alert and session_expired:
                self._cancel_event.set()
                entry.status = "erro"
                entry.erro = "Sessão do eCAC expirada"
                return False

            try:
                self._driver.switch_to.default_content()
            except Exception:
                pass

            target = self.out_dir / (entry.safe_basename() + ".pdf")
            if target.exists():
                ts = time.strftime("%H%M%S")
                target = self.out_dir / (
                    entry.safe_basename() + f"_{ts}.pdf")

            ok = self._capture_pdf_via_cdp(target)
            if ok:
                entry.arquivo = target.name
                entry.status = "baixado"
                size_kb = target.stat().st_size / 1024
                self._log(f"    ✓ salvo: {target.name} ({size_kb:.0f} KB)", "ok")
            else:
                entry.status = "erro"
                entry.erro = "Falha ao capturar PDF via CDP (mesma aba)."
        finally:
            try:
                self._driver.back()
            except Exception:
                pass
            time.sleep(1.5)
            self._switch_to_listing_context()

        return entry.status == "baixado"

    def _go_to_next_page(self) -> bool:
        """Avança 1 página da listagem de DCTFs, se houver paginação.

        IMPORTANTE: a listagem de DCTFs do eCAC normalmente é uma
        tabela ÚNICA, sem paginação — todas as declarações cabem numa
        página só. Cada linha tem vários <input> de ação (Consultar,
        Imprimir, Extrato). Uma busca genérica por <input> pode pegar
        um desses botões por engano, clicar, e fazer o downloader
        achar que "avançou de página" — entrando em LOOP infinito,
        reprocessando a mesma tabela para sempre.

        Por isso esta função é RIGOROSA: só reconhece paginação se
        houver um link/controle de paginação DE VERDADE — um elemento
        de navegação ('Próxima', '»', número de página) dentro de um
        container de paginação reconhecível. Botões de ação da tabela
        NUNCA são considerados paginação.

        Na dúvida, retorna False (encerra) — é o comportamento seguro:
        no pior caso deixa de pegar uma 2ª página que provavelmente
        não existe; nunca entra em loop.
        """
        # Procura SOMENTE links <a> de paginação real. Botões <input>
        # da tabela ficam de fora de propósito.
        nxt = None
        candidatos = []
        try:
            # Links cujo texto é exatamente um marcador de "próxima"
            candidatos = self._driver.find_elements(
                By.XPATH,
                "//a[normalize-space()='Próxima' "
                "or normalize-space()='Próximo' "
                "or normalize-space()='»' "
                "or normalize-space()='Next']")
        except Exception:
            candidatos = []

        for el in candidatos:
            try:
                if not (el.is_displayed() and el.is_enabled()):
                    continue
                cls = (el.get_attribute("class") or "").lower()
                aria = (el.get_attribute("aria-disabled") or "").lower()
                href = (el.get_attribute("href") or "").lower()
                if "disabled" in cls or aria == "true":
                    continue
                # Tem que ter ação de navegação (href ou onclick)
                onclick = (el.get_attribute("onclick") or "")
                if not href and not onclick:
                    continue
                # Não pode ser um botão de ação de linha
                if any(p in onclick.lower() for p in
                       ("selecionaservico", "imprimir", "consultar",
                        "extrato")):
                    continue
                nxt = el
                break
            except Exception:
                continue

        if not nxt:
            # Sem link de paginação real — lista de página única.
            return False

        # Guarda assinatura da página atual ANTES de clicar
        first_numero_before = ""
        table = self._find_listing_table()
        if table:
            rows = self._find_rows(table)
            if rows:
                e = self._parse_row(rows[0], 0)
                if e:
                    first_numero_before = e.numero

        try:
            self._driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", nxt)
            nxt.click()
        except Exception:
            return False

        # Confirma a virada: a 1ª linha tem que MUDAR de verdade
        deadline = time.time() + _DL_PAGE_CHANGE_TIMEOUT
        while time.time() < deadline:
            if self._cancel_event.is_set():
                return False
            time.sleep(0.5)
            t = self._find_listing_table()
            if t:
                rows = self._find_rows(t)
                if rows:
                    e = self._parse_row(rows[0], 0)
                    if e and e.numero and e.numero != first_numero_before:
                        return True
        # Não mudou — não havia página nova
        return False


# ---- Backend: DctfWebDownloader ---------------------------------------------
# Baixa em massa os 3 recibos de cada DCTFWeb (Débitos / Créditos / Completa)
# via eCAC > DCTFWeb (módulo Angular moderno).
#
# Fluxo de cada DCTFWeb:
#   1. Lista linhas da tabela principal (período/categoria/situação)
#   2. Pra cada linha, clica em "Detalhar"/"Visualizar" pra abrir o detalhe
#   3. No detalhe, captura os 3 recibos (Débitos, Créditos, Completa)
#      via Page.printToPDF do CDP
#   4. Volta pra listagem (botão Voltar ou Back)
#
# Como ainda não vi a DOM real, uso seletores defensivos com múltiplas
# estratégias. Salva _debug_dctfweb_*.html quando trava.
