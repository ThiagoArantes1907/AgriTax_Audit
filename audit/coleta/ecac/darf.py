"""Robô e-CAC: download em massa de DARF (Pagamentos Web) (roda em thread, callbacks injetados — sem Tkinter).

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


class DarfDownloader:
    """Baixa em massa os comprovantes de pagamento de DARF/DAS do eCAC."""

    # Palavras-chave pra identificar contexto/aba
    URL_KEYWORDS = ("servicos.receitafederal.gov.br", "pagamentos",
                    "/consulta", "meus pagamentos")
    # Cabeçalho esperado da tabela
    TABLE_HEADER_KEYWORDS = (
        "data de arrecadação", "data de arrecadacao", "arrecadação",
        "arrecadacao", "código de receita", "codigo de receita",
        "número documento", "numero documento", "período de apuração",
        "periodo de apuracao", "valor total", "ações", "acoes",
    )

    # Diagnóstico (logado UMA vez)
    _row_diagnosed: bool = False

    def __init__(self,
                 out_dir: Optional[Path] = None,
                 debug_port: int = 9222,
                 on_log: Callable[[str, str], None] = None,
                 on_progress: Callable[[dict], None] = None,
                 on_finished: Callable[[bool, dict], None] = None,
                 legacy_dir_for_migration: Optional[Path] = None):
        self.out_dir = Path(out_dir) if out_dir else None
        self.debug_port = debug_port
        self.on_log = on_log or (lambda *a, **k: None)
        self.on_progress = on_progress or (lambda *a, **k: None)
        self.on_finished = on_finished or (lambda *a, **k: None)
        self._cancel_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._driver = None
        self._stats = {
            "baixados": 0, "erros": 0, "pulados": 0,
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
        DarfDownloader._row_diagnosed = False
        self._cancel_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel_event.set()
        self._log("Cancelamento solicitado — encerrando após o item atual...",
                  "warn")

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
            (target_dir / f"_debug_darf_{tag}_{ts}.html").write_text(
                self._driver.page_source, encoding="utf-8")
            try:
                self._driver.save_screenshot(
                    str(target_dir / f"_debug_darf_{tag}_{ts}.png"))
            except Exception:
                pass
            self._log(f"Snapshot debug salvo: _debug_darf_{tag}_{ts}.html",
                      "warn")
        except Exception:
            pass

    def _check_and_handle_alert(self) -> tuple:
        """Verifica/aceita alerts JS. Retorna (had_alert, text, is_expired)."""
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
        text_low = text.lower().replace("ã", "a").replace("é", "e")
        is_expired = any(kw in text_low for kw in (
            "sessao expirada", "sessão expirada",
            "logue-se", "expirou", "logue se novamente",
        ))
        try:
            alert.accept()
        except Exception:
            try:
                alert.dismiss()
            except Exception:
                pass
        return True, text, is_expired

    def _run(self) -> None:
        success = False
        summary: Dict = {}
        manifest: Optional[_DownloadManifest] = None

        try:
            self._log(f"Conectando ao Chrome em localhost:{self.debug_port}...",
                      "info")
            try:
                self._driver = self._attach_to_chrome()
            except WebDriverException as e:
                self._log(f"✗ Não consegui conectar ao Chrome: {e}", "error")
                summary["erro"] = "Chrome inacessível"
                return

            self._log(f"✓ Conectado. Aba: {self._driver.title!r}", "ok")

            # ── Seleciona a aba 'Meus Pagamentos' ANTES de detectar CNPJ ──
            # A detecção lê o header da aba atual; selecionar a aba do
            # módulo primeiro evita detectar o CNPJ de outra empresa.
            if not self._switch_to_pagamentos_context():
                self._log("✗ Tela 'Meus Pagamentos' não localizada. Garanta "
                          "que está na aba com a URL "
                          "servicos.receitafederal.gov.br/servico/pagamentos "
                          "e a tabela de pagamentos visível.", "error")
                self._save_debug_snapshot("contexto_nao_localizado")
                summary["erro"] = "Tela Meus Pagamentos não localizada"
                return
            self._log("✓ Aba 'Meus Pagamentos' localizada", "ok")

            # Detecta CNPJ (na aba 'Meus Pagamentos')
            self._log("Detectando CNPJ ativo no eCAC...", "info")
            cnpj, nome = _dl_detect_cnpj_from_ecac(self._driver, self._log)
            if not cnpj:
                # No app 'Meus Pagamentos' o CNPJ aparece no canto superior
                # direito — tenta um seletor alternativo
                cnpj, nome = self._detect_cnpj_from_pagamentos()
            if not cnpj:
                self._log("✗ CNPJ não detectado. Garanta que está logado no "
                          "eCAC e na tela 'Meus Pagamentos'.", "error")
                summary["erro"] = "CNPJ não detectado"
                return
            self._cnpj = cnpj
            self._empresa = nome
            self._log(f"✓ CNPJ detectado: {cnpj}"
                      + (f" — {nome}" if nome else ""), "ok")

            # Cria pastas
            try:
                paths = _dl_ensure_company_dirs(cnpj)
            except Exception as e:
                self._log(f"✗ Erro ao criar pastas: {e}", "error")
                summary["erro"] = f"Erro pasta: {e}"
                return
            self.out_dir = paths["darf_dir"]
            log_path = paths["log_darf"]
            self._log(f"✓ Pasta: {self.out_dir}", "ok")
            self._log(f"   Log:  {log_path}", "info")

            # Carrega manifest
            manifest = _DownloadManifest(log_path, files_root=self.out_dir)
            for entry in manifest.entries.values():
                if entry.status == "baixado" and entry.arquivo:
                    p = self.out_dir / entry.arquivo
                    if p.exists():
                        self._downloaded_files.append(p)
            self._log(f"   Já baixados anteriormente: "
                      f"{len(self._downloaded_files)} PDF(s)", "info")

            # Re-seleciona o contexto 'Meus Pagamentos' (garantia)
            if not self._switch_to_pagamentos_context():
                self._log("✗ Tela 'Meus Pagamentos' não localizada.",
                          "error")
                self._save_debug_snapshot("contexto_nao_localizado")
                summary["erro"] = "Tela Meus Pagamentos não localizada"
                return

            self._log("✓ Contexto 'Meus Pagamentos' pronto", "ok")

            page_num = 1
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
                    self._log(f"✗ Listagem página {page_num} não carregou.",
                              "error")
                    # Diagnóstico: mostra estrutura da página pra debug
                    self._log("Diagnóstico da estrutura da página:", "warn")
                    for line in self._diagnose_page().splitlines():
                        self._log(line, "info")
                    self._save_debug_snapshot(f"page{page_num}_nao_carregou")
                    break

                entries = self._collect_entries()
                self._stats["linhas_pagina"] = len(entries)
                self._log(f"  {len(entries)} pagamento(s) detectado(s)", "info")
                self._emit_progress()

                if not entries:
                    self._log("Página vazia — encerrando.", "warn")
                    self._save_debug_snapshot(f"page{page_num}_vazia")
                    break

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
                    entry.cnpj = self._cnpj

                    if manifest.is_done(entry.numero):
                        self._log(f"  [PULAR] {entry.numero} — já baixado",
                                  "info")
                        self._stats["pulados"] += 1
                        self._emit_progress()
                        continue

                    label = (f"{entry.kind} {entry.periodo or '?'} | "
                             f"cód {entry.tipo_credito or '?'} | "
                             f"doc {entry.numero}")
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
                                pdf_p, self._cnpj, "darf_dir", self._log)
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

                # Avança pra próxima página da tabela de pagamentos
                # (a tela 'Meus Pagamentos' é Angular e TEM paginação
                # real). Sem este bloco o 'while' reprocessaria a
                # página 1 para sempre — loop infinito.
                if self._go_to_next_page():
                    page_num += 1
                    continue
                self._log("Última página alcançada.", "info")
                break
            success = not self._cancel_event.is_set()
        except Exception as e:
            self._log(f"✗ Erro fatal: {e}", "error")
            summary["erro"] = str(e)
        finally:
            if manifest is not None and self.out_dir is not None:
                try:
                    manifest.export_csv(self.out_dir / "_resumo_darf.csv")
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

    def _detect_cnpj_from_pagamentos(self) -> tuple:
        """Detecta CNPJ no app 'Meus Pagamentos' — o CNPJ aparece no
        canto superior direito perto do nome da empresa."""
        try:
            body_text = self._driver.execute_script(
                "return document.body ? document.body.innerText : '';") or ""
        except Exception:
            body_text = ""
        m = _DL_ANY_CNPJ_RE.search(body_text)
        if m:
            cnpj = m.group(0)
            # Tenta achar o nome (texto antes do CNPJ na mesma região)
            nome = ""
            try:
                idx = body_text.find(cnpj)
                trecho = body_text[max(0, idx - 80):idx].strip()
                linhas = [l.strip() for l in trecho.splitlines() if l.strip()]
                if linhas:
                    nome = linhas[-1]
            except Exception:
                pass
            return cnpj, nome
        return "", ""

    def _switch_to_pagamentos_context(self) -> bool:
        """Procura a aba com a tela 'Meus Pagamentos'."""
        for handle in self._driver.window_handles:
            try:
                self._driver.switch_to.window(handle)
            except Exception:
                continue
            url = (self._driver.current_url or "").lower()
            if "servicos.receitafederal.gov.br" in url and \
               "pagamentos" in url:
                return True
        # Critério secundário: qualquer aba cujo conteúdo tenha a tabela
        for handle in self._driver.window_handles:
            try:
                self._driver.switch_to.window(handle)
            except Exception:
                continue
            if self._find_listing_table() is not None:
                return True
        return False

    # ---- Tabela / linhas ------------------------------------------------
    def _find_listing_table(self):
        """Localiza a tabela 'Pagamentos'. App é Angular — pode ser <table>
        ou estrutura de divs com role=table."""
        # Estratégia 1: <table> com cabeçalho compatível
        try:
            tables = self._driver.find_elements(By.TAG_NAME, "table")
            for tbl in tables:
                try:
                    if not tbl.is_displayed():
                        continue
                    header = " ".join(
                        th.text.lower()
                        for th in tbl.find_elements(By.XPATH, ".//th"))
                    if not header:
                        first = tbl.find_elements(By.XPATH, ".//tr[1]/td")
                        header = " ".join(c.text.lower() for c in first)
                    matches = sum(1 for kw in self.TABLE_HEADER_KEYWORDS
                                  if kw in header)
                    if matches >= 3:
                        return tbl
                except StaleElementReferenceException:
                    continue
        except Exception:
            pass

        # Estratégia 2: estrutura Angular com role='table' / mat-table
        for by, sel in [
            (By.CSS_SELECTOR, "[role='table']"),
            (By.CSS_SELECTOR, "mat-table"),
            (By.CSS_SELECTOR, "table[class*='table']"),
        ]:
            try:
                els = self._driver.find_elements(by, sel)
                for el in els:
                    try:
                        if el.is_displayed():
                            return el
                    except Exception:
                        continue
            except Exception:
                continue

        # Estratégia 3: maior <table> visível
        try:
            tables = self._driver.find_elements(By.TAG_NAME, "table")
            largest, largest_n = None, 0
            for tbl in tables:
                try:
                    if not tbl.is_displayed():
                        continue
                    n = len(tbl.find_elements(By.TAG_NAME, "tr"))
                    if n > largest_n:
                        largest, largest_n = tbl, n
                except StaleElementReferenceException:
                    continue
            if largest and largest_n >= 2:
                return largest
        except Exception:
            pass

        # Estratégia 4: estrutura de <div>s (Angular sem <table>).
        # Procura um container cujo texto tenha as colunas-chave E que
        # tenha vários "filhos repetidos" (as linhas).
        try:
            # Procura qualquer elemento cujo texto contenha 3+ keywords
            # de cabeçalho — esse é o container da listagem
            all_divs = self._driver.find_elements(
                By.CSS_SELECTOR,
                "div, section, [role='grid'], [role='list'], "
                "[class*='tabela'], [class*='table'], [class*='lista'], "
                "[class*='grid'], [class*='resultado']")
            best = None
            best_size = 10 ** 9  # começa enorme
            best_score = 0
            for d in all_divs:
                try:
                    if not d.is_displayed():
                        continue
                    txt = (d.text or "").lower()
                    if not txt:
                        continue
                    matches = sum(1 for kw in self.TABLE_HEADER_KEYWORDS
                                  if kw in txt)
                    # Tem data DD/MM/AAAA no conteúdo? (sinal de linha de dados)
                    has_data = bool(re.search(r"\d{2}/\d{2}/\d{4}", txt))
                    if matches >= 3 and has_data:
                        # Prefere o MENOR container que ainda tem tudo
                        # (mais perto das linhas reais)
                        size = len(txt)
                        if size < best_size:
                            best = d
                            best_size = size
                            best_score = matches
                except StaleElementReferenceException:
                    continue
            if best is not None:
                return best
        except Exception:
            pass
        return None

    def _diagnose_page(self) -> str:
        """Gera relatório da estrutura da página pra debug quando a
        tabela não é encontrada."""
        lines = []
        try:
            # Conta elementos estruturais
            for tag in ("table", "mat-table", "tbody", "tr", "mat-row"):
                try:
                    n = len(self._driver.find_elements(By.TAG_NAME, tag))
                    lines.append(f"  <{tag}>: {n}")
                except Exception:
                    pass
            # role=table / role=grid / role=row
            for role in ("table", "grid", "row", "list", "listitem"):
                try:
                    n = len(self._driver.find_elements(
                        By.CSS_SELECTOR, f"[role='{role}']"))
                    if n:
                        lines.append(f"  [role='{role}']: {n}")
                except Exception:
                    pass
            # Procura elementos cujo texto tem as keywords de cabeçalho
            lines.append("  Elementos com keywords de cabeçalho:")
            try:
                all_els = self._driver.find_elements(
                    By.CSS_SELECTOR, "div, section, table, ul")
                shown = 0
                for el in all_els:
                    if shown >= 8:
                        break
                    try:
                        if not el.is_displayed():
                            continue
                        txt = (el.text or "").lower()
                        if not txt or len(txt) > 3000:
                            continue
                        matches = sum(1 for kw in self.TABLE_HEADER_KEYWORDS
                                      if kw in txt)
                        if matches >= 3:
                            tag = el.tag_name
                            cls = (el.get_attribute("class") or "")[:50]
                            eid = (el.get_attribute("id") or "")[:40]
                            role = (el.get_attribute("role") or "")[:20]
                            has_data = bool(re.search(
                                r"\d{2}/\d{2}/\d{4}", txt))
                            lines.append(
                                f"    <{tag}> id={eid!r} class={cls!r} "
                                f"role={role!r} kws={matches} "
                                f"tem_data={has_data} len={len(txt)}")
                            shown += 1
                    except Exception:
                        continue
                if shown == 0:
                    lines.append("    (nenhum elemento com 3+ keywords "
                                 "encontrado — página ainda carregando?)")
            except Exception as e:
                lines.append(f"    erro: {e}")
            # URL atual
            try:
                lines.append(f"  URL atual: {self._driver.current_url[:90]}")
            except Exception:
                pass
        except Exception as e:
            lines.append(f"  Erro no diagnóstico: {e}")
        return "\n".join(lines)

    @staticmethod
    def _safe_displayed(el) -> bool:
        try:
            return el.is_displayed()
        except StaleElementReferenceException:
            return False

    def _find_rows(self, table) -> List:
        """Linhas de DADOS da tabela de pagamentos.
        Suporta <tr>, <mat-row> e estrutura de <div>s (Angular SPA)."""
        if not table:
            return []

        def _is_data_row(text: str) -> bool:
            """Decide se um texto parece uma linha de dados de pagamento."""
            if not text:
                return False
            text_low = text.lower()
            # Pula cabeçalho
            if "data de arrecada" in text_low and \
               "código de receita" in text_low:
                return False
            if "valor total" in text_low and "ações" in text_low:
                return False
            if "período de apuração" in text_low and \
               "número documento" in text_low:
                return False
            # Linha de dados precisa ter data DD/MM/AAAA
            if not re.search(r"\b\d{2}/\d{2}/\d{4}\b", text):
                return False
            return True

        try:
            # 1) Padrão tabela: <tr> / <mat-row>
            rows = table.find_elements(
                By.CSS_SELECTOR, "mat-row, tr[role='row']")
            if not rows:
                rows = table.find_elements(By.XPATH, ".//tbody/tr")
            if not rows:
                rows = table.find_elements(By.TAG_NAME, "tr")

            filtered = []
            for r in rows:
                try:
                    if not self._safe_displayed(r):
                        continue
                    text = (r.text or "").strip()
                    if _is_data_row(text):
                        filtered.append(r)
                except StaleElementReferenceException:
                    continue
            if filtered:
                return filtered

            # 2) Estrutura de <div>s — procura elementos repetidos que
            #    pareçam linhas (Angular SPA sem <table>)
            for sel in ("[role='row']", "[class*='linha']",
                        "[class*='row']", "[class*='item']",
                        "[class*='registro']", "li"):
                try:
                    candidates = table.find_elements(By.CSS_SELECTOR, sel)
                    div_rows = []
                    for c in candidates:
                        try:
                            if not self._safe_displayed(c):
                                continue
                            text = (c.text or "").strip()
                            if _is_data_row(text):
                                div_rows.append(c)
                        except StaleElementReferenceException:
                            continue
                    if div_rows:
                        return div_rows
                except Exception:
                    continue

            # 3) Último recurso: filhos diretos do container que tenham data
            try:
                children = table.find_elements(By.XPATH, "./*")
                div_rows = []
                for c in children:
                    try:
                        if not self._safe_displayed(c):
                            continue
                        text = (c.text or "").strip()
                        if _is_data_row(text):
                            div_rows.append(c)
                    except StaleElementReferenceException:
                        continue
                if div_rows:
                    return div_rows
            except Exception:
                pass

            return []
        except Exception:
            return []

    def _parse_row(self, row, idx: int = 0) -> Optional[_DownloadEntry]:
        """Extrai dados de uma linha de pagamento.

        O texto da linha vem com colunas separadas por quebra de linha:
          linha[0] = Data de Arrecadação   (DD/MM/AAAA)
          linha[1] = Código de Receita     (ex: 1410, 3333)
          linha[2] = Número Documento      (sequência longa de dígitos)
          linha[3] = Período de Apuração   (DD/MM/AAAA)
          linha[4] = Valor Total           (ex: 4.187,91)
          linha[5+] = ações (Detalhar, Emitir, ...)
        """
        try:
            text = row.text
        except StaleElementReferenceException:
            return None
        if not text:
            return None

        # Divide em linhas não-vazias — cada uma é uma coluna
        linhas = [l.strip() for l in text.splitlines() if l.strip()]

        data_arrecadacao = ""
        codigo = ""
        numero = ""
        periodo = ""
        valor = ""

        # ── Tenta o parsing posicional (coluna por linha) ──
        if len(linhas) >= 5:
            # linha[0]: data de arrecadação
            if re.fullmatch(r"\d{2}/\d{2}/\d{4}", linhas[0]):
                data_arrecadacao = linhas[0]
            # linha[1]: código de receita (3-6 dígitos, pode ter sufixo)
            m = re.match(r"(\d{3,6})", linhas[1])
            if m:
                codigo = m.group(1)
            # linha[2]: número do documento (dígitos longos)
            m = re.match(r"(\d{8,})", linhas[2])
            if m:
                numero = m.group(1)
            # linha[3]: período de apuração
            if re.fullmatch(r"\d{2}/\d{2}/\d{4}", linhas[3]):
                periodo = linhas[3]
            # linha[4]: valor total
            m = re.match(r"(\d{1,3}(?:\.\d{3})*,\d{2})", linhas[4])
            if m:
                valor = m.group(1)

        # ── Fallback: parsing por regex no texto inteiro ──
        if not data_arrecadacao:
            datas = re.findall(r"\b\d{2}/\d{2}/\d{4}\b", text)
            if datas:
                data_arrecadacao = datas[0]
                if len(datas) > 1 and not periodo:
                    periodo = datas[1]
        if not periodo:
            periodo = data_arrecadacao
        if not codigo:
            # Procura número de 3-6 dígitos que NÃO faça parte de uma
            # data nem de uma sequência longa
            for tok in re.findall(r"\b\d{3,6}\b", text):
                # Pula se for ano (4 dígitos 19xx/20xx)
                if re.fullmatch(r"(19|20)\d{2}", tok):
                    continue
                codigo = tok
                break
        if not numero:
            m = re.search(r"\b(\d{12,})\b", text)
            if m:
                numero = m.group(1)
            else:
                seqs = [s for s in re.findall(r"\d+", text) if len(s) >= 8]
                if seqs:
                    numero = max(seqs, key=len)
        if not numero:
            numero = f"linha_{idx}_{data_arrecadacao.replace('/', '')}"
        if not valor:
            m = re.search(r"\d{1,3}(?:\.\d{3})*,\d{2}", text)
            if m:
                valor = m.group(0)

        # Tipo: DARF ou DAS
        kind = "DARF"
        if re.search(r"\bdas\b", text, re.IGNORECASE):
            kind = "DAS"

        # ── Monta a CHAVE única e ESTÁVEL do pagamento ──
        # A tabela 'Meus Pagamentos' (Angular) mostra basicamente
        # DATA + VALOR por linha — nem sempre há um "número do
        # documento" visível. O índice da linha (idx) NÃO serve como
        # chave: ele reinicia em cada página, então a mesma chave
        # ('linha_0_...') se repete em páginas diferentes e o resume
        # rebaixa tudo.
        #
        # Chave estável = data + valor + código de receita. Essa
        # combinação não depende de página nem de índice. Se houver um
        # número de documento real, ele entra junto para desambiguar.
        partes_chave = [
            data_arrecadacao.replace("/", "") or "sd",
            (valor or "sv").replace(".", "").replace(",", ""),
            codigo or "sc",
        ]
        if numero and not numero.startswith("linha_"):
            partes_chave.append(numero)
        chave = "DARF_" + "_".join(partes_chave)

        return _DownloadEntry(
            numero=chave,
            tipo=valor,                  # reaproveitado: valor total
            data_transmissao=data_arrecadacao,
            tipo_credito=codigo,         # reaproveitado: código de receita
            periodo=periodo,
            kind=kind,
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
        # Desambigua chaves repetidas DENTRO da mesma página: se dois
        # pagamentos têm data+valor+código idênticos, acrescenta um
        # sufixo de ocorrência (#2, #3...) preservando a ordem da
        # tabela — assim a chave continua estável entre execuções.
        vistos: Dict[str, int] = {}
        for e in out:
            n = vistos.get(e.numero, 0) + 1
            vistos[e.numero] = n
            if n > 1:
                e.numero = f"{e.numero}#{n}"
        return out

    def _click_pdf_icon(self, row) -> bool:
        """Clica no ícone/botão que gera o comprovante PDF na coluna Ações.

        Pelo diagnóstico, o texto da linha termina com 'Detalhar' e
        'Emitir' — então a coluna Ações tem botões de texto, não só
        ícones. 'Emitir' é o que gera o comprovante de pagamento.

        Estratégias (em ordem):
          1. Link/botão com texto 'Emitir' / 'Comprovante' / 'Imprimir'
          2. Atributos (title/alt/class) sugestivos de PDF
          3. Último clicável da última coluna
        """
        def _norm(s):
            try:
                n = unicodedata.normalize("NFKD", s or "")
                return "".join(c for c in n
                                if not unicodedata.combining(c)).lower()
            except Exception:
                return (s or "").lower()

        # Diagnóstico na 1ª linha
        if not DarfDownloader._row_diagnosed:
            self._log_row_inspection(row)
            DarfDownloader._row_diagnosed = True

        # Rola até a linha ficar visível (linhas no fim da tabela podem
        # estar fora da viewport — clique falha sem o scroll)
        try:
            self._driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", row)
            time.sleep(0.3)
        except Exception:
            pass

        # Estratégia 1: link/botão com TEXTO de ação que gera comprovante.
        # 'Emitir' é o alvo principal (gera o comprovante de pagamento).
        try:
            clickables = row.find_elements(
                By.XPATH, ".//a | .//button | .//*[@onclick] | "
                ".//*[@role='button']")
            # ordem de prioridade dos textos
            prioridades = ["emitir", "comprovante", "imprimir", "pdf",
                           "baixar"]
            melhor = None
            melhor_rank = 99
            for el in clickables:
                txt = _norm(el.text or "")
                attrs = _norm(" ".join([
                    el.get_attribute("title") or "",
                    el.get_attribute("aria-label") or "",
                ]))
                alvo = f"{txt} {attrs}"
                for rank, kw in enumerate(prioridades):
                    if kw in alvo:
                        if rank < melhor_rank:
                            melhor = el
                            melhor_rank = rank
                        break
            if melhor is not None:
                if self._click_element(melhor):
                    return True
        except Exception:
            pass

        # Estratégia 2: atributos sugestivos de PDF (ícones)
        try:
            clickables = row.find_elements(
                By.XPATH, ".//a | .//button | .//img | .//i | "
                ".//*[@onclick] | .//mat-icon | .//*[@role='button']")
            scored = []
            for el in clickables:
                attrs = _norm(" ".join([
                    el.get_attribute("title") or "",
                    el.get_attribute("alt") or "",
                    el.get_attribute("aria-label") or "",
                    el.get_attribute("class") or "",
                    (el.get_attribute("src") or "").split("/")[-1],
                    el.get_attribute("id") or "",
                    el.text or "",
                ]))
                score = 0
                for kw in ("pdf", "baixar", "comprovante", "download",
                           "imprimir", "documento", "emitir"):
                    if kw in attrs:
                        score += 40
                for kw in ("lupa", "visualizar", "consultar", "detalhe",
                           "detalhar", "search", "magnify", "olho",
                           "eye", "zoom"):
                    if kw in attrs:
                        score -= 35
                if score > 0:
                    scored.append((score, el))
            scored.sort(key=lambda t: t[0], reverse=True)
            for score, el in scored:
                if self._click_element(el):
                    return True
        except Exception:
            pass

        # Estratégia 3: último clicável da última coluna
        try:
            tds = row.find_elements(By.XPATH, "./td")
            if not tds:
                tds = row.find_elements(By.CSS_SELECTOR, "mat-cell, td")
            if tds:
                last = tds[-1]
                clickables = last.find_elements(
                    By.XPATH, ".//a | .//button | .//img | .//i | "
                    ".//*[@onclick] | .//mat-icon | .//*[@role='button']")
                if clickables:
                    if self._click_element(clickables[-1]):
                        return True
        except Exception:
            pass

        # Estratégia 4: se a linha não é <tr> (estrutura de divs), procura
        # o último clicável de toda a linha
        try:
            clickables = row.find_elements(
                By.XPATH, ".//a | .//button | .//*[@onclick] | "
                ".//*[@role='button']")
            if clickables:
                if self._click_element(clickables[-1]):
                    return True
        except Exception:
            pass

        return False

    def _log_row_inspection(self, row) -> None:
        """Diagnóstico (1x) — lista clicáveis da primeira linha."""
        try:
            self._log("   ── DIAGNÓSTICO da linha (primeira) ──", "warn")
            try:
                self._log(f"   Texto: {(row.text or '')[:150]!r}", "info")
            except Exception:
                pass
            tds = row.find_elements(By.XPATH, "./td")
            if not tds:
                tds = row.find_elements(By.CSS_SELECTOR, "mat-cell, td")
            self._log(f"   Colunas (td/mat-cell): {len(tds)}", "info")
            for ti, td in enumerate(tds):
                clickables = td.find_elements(
                    By.XPATH, ".//a | .//button | .//img | .//i | "
                    ".//*[@onclick] | .//mat-icon | .//*[@role='button']")
                td_text = (td.text or "").strip()[:40]
                if clickables or td_text:
                    self._log(f"   td[{ti}] text={td_text!r} "
                              f"clicáveis={len(clickables)}", "info")
                for ci, el in enumerate(clickables[:6]):
                    try:
                        attrs = {
                            "tag": el.tag_name,
                            "title": (el.get_attribute("title") or "")[:30],
                            "alt": (el.get_attribute("alt") or "")[:30],
                            "aria": (el.get_attribute("aria-label") or "")[:30],
                            "class": (el.get_attribute("class") or "")[:40],
                            "src": (el.get_attribute("src") or "").split("/")[-1][:35],
                            "onclick": (el.get_attribute("onclick") or "")[:40],
                        }
                        non_empty = {k: v for k, v in attrs.items() if v}
                        self._log(f"     [{ci}] {non_empty}", "info")
                    except Exception:
                        continue
        except Exception as e:
            self._log(f"   (falha no diagnóstico: {e})", "warn")

    def _download_one(self, row, entry: _DownloadEntry) -> bool:
        """Clica no ícone PDF e captura o comprovante.

        Detecta 3 cenários: nova aba, navegação na mesma aba, ou
        download direto pra pasta. Faz uma 2ª tentativa se a 1ª não
        causar resposta.
        """
        main_window = self._driver.current_window_handle

        def _tentar_clique() -> tuple:
            """Clica no ícone e aguarda resposta. Retorna
            (new_handle, same_tab, downloaded_file)."""
            handles_before = set(self._driver.window_handles)
            try:
                url_before = self._driver.execute_script(
                    "return window.top.location.href")
            except Exception:
                url_before = ""
            self._configure_download_dir()
            files_before = self._snapshot_dir_files()

            if not self._click_pdf_icon(row):
                return None, False, None, "no_icon"

            deadline = time.time() + 30.0
            nh = None
            stn = False
            dl = None
            while time.time() < deadline:
                time.sleep(0.4)
                had_alert, alert_text, expired = \
                    self._check_and_handle_alert()
                if had_alert:
                    if expired:
                        self._cancel_event.set()
                        return None, False, None, "expired"
                    self._log(f"    ⚠ Alert: {alert_text!r}", "warn")
                    return None, False, None, f"alert:{alert_text}"
                try:
                    nhs = set(self._driver.window_handles) - handles_before
                    if nhs:
                        nh = next(iter(nhs))
                        break
                except Exception:
                    pass
                try:
                    cur = self._driver.execute_script(
                        "return window.top.location.href")
                    if cur and cur != url_before:
                        stn = True
                        break
                except Exception:
                    pass
                novo = self._detect_new_download(files_before)
                if novo:
                    dl = novo
                    break
            return nh, stn, dl, "ok"

        # ── 1ª tentativa ──
        new_handle, same_tab, downloaded, status = _tentar_clique()
        if status == "expired":
            entry.status = "erro"
            entry.erro = "Sessão do eCAC expirada"
            return False
        if status == "no_icon":
            entry.status = "erro"
            entry.erro = "Ícone PDF não localizado na linha."
            return False
        if status.startswith("alert:"):
            entry.status = "erro"
            entry.erro = status
            return False

        # ── 2ª tentativa se a 1ª não respondeu ──
        if not new_handle and not same_tab and not downloaded:
            self._log("    ⟳ 1ª tentativa sem resposta, tentando "
                      "novamente...", "warn")
            new_handle, same_tab, downloaded, status = _tentar_clique()
            if status == "expired":
                entry.status = "erro"
                entry.erro = "Sessão do eCAC expirada"
                return False

        # ── CASO: download direto pra pasta ──
        if downloaded:
            try:
                target = self.out_dir / (entry.safe_basename() + ".pdf")
                if target.exists():
                    ts = time.strftime("%H%M%S")
                    target = self.out_dir / (
                        entry.safe_basename() + f"_{ts}.pdf")
                if downloaded.resolve() != target.resolve():
                    downloaded.rename(target)
                entry.arquivo = target.name
                entry.status = "baixado"
                size_kb = target.stat().st_size / 1024
                self._log(f"    ✓ salvo: {target.name} ({size_kb:.0f} KB)",
                          "ok")
                return True
            except Exception as e:
                entry.status = "erro"
                entry.erro = f"Erro ao mover download: {e}"
                return False

        if not new_handle and not same_tab:
            entry.status = "erro"
            entry.erro = ("Clique no ícone PDF não causou navegação "
                          "nem download (2 tentativas)")
            return False

        try:
            if new_handle:
                self._driver.switch_to.window(new_handle)
            time.sleep(1.5)
            try:
                WebDriverWait(self._driver, 15).until(
                    lambda d: d.execute_script(
                        "return document.readyState") == "complete")
            except (TimeoutException, UnexpectedAlertPresentException):
                pass
            time.sleep(1.0)

            had_alert, _, expired = self._check_and_handle_alert()
            if had_alert and expired:
                self._cancel_event.set()
                entry.status = "erro"
                entry.erro = "Sessão expirada"
                return False

            target = self.out_dir / (entry.safe_basename() + ".pdf")
            if target.exists():
                ts = time.strftime("%H%M%S")
                target = self.out_dir / (
                    entry.safe_basename() + f"_{ts}.pdf")

            ok = self._capture_pdf(target)
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
            if new_handle:
                try:
                    self._driver.close()
                except Exception:
                    pass
                try:
                    self._driver.switch_to.window(main_window)
                except Exception:
                    pass
            elif same_tab:
                try:
                    self._driver.back()
                except Exception:
                    pass
                time.sleep(1.5)

    def _configure_download_dir(self) -> None:
        """Configura o Chrome pra baixar arquivos direto na pasta de saída."""
        if not self.out_dir:
            return
        payload = {"behavior": "allow",
                   "downloadPath": str(self.out_dir),
                   "eventsEnabled": True}
        for cmd in ("Browser.setDownloadBehavior", "Page.setDownloadBehavior"):
            try:
                self._driver.execute_cdp_cmd(cmd, payload)
                return
            except Exception:
                continue

    def _snapshot_dir_files(self) -> set:
        """Snapshot dos nomes de arquivo na pasta de saída."""
        if not self.out_dir:
            return set()
        try:
            return {p.name for p in self.out_dir.iterdir() if p.is_file()}
        except Exception:
            return set()

    def _detect_new_download(self, files_before: set) -> Optional[Path]:
        """Verifica se um novo PDF apareceu na pasta (download concluído).
        Ignora arquivos .crdownload (download em andamento)."""
        if not self.out_dir:
            return None
        try:
            now = {p.name for p in self.out_dir.iterdir() if p.is_file()}
            novos = now - files_before
            concluidos = [n for n in novos
                          if not n.endswith(".crdownload")
                          and not n.endswith(".tmp")]
            pdfs = [n for n in concluidos if n.lower().endswith(".pdf")]
            escolha = pdfs or concluidos
            if escolha:
                escolha.sort(
                    key=lambda n: (self.out_dir / n).stat().st_mtime,
                    reverse=True)
                return self.out_dir / escolha[0]
        except Exception:
            pass
        return None

    def _click_element(self, el) -> bool:
        """Tenta clicar no elemento por várias estratégias."""
        try:
            self._driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", el)
        except Exception:
            pass
        try:
            el.click()
            return True
        except ElementClickInterceptedException:
            pass
        except Exception:
            pass
        try:
            self._driver.execute_script("arguments[0].click();", el)
            return True
        except Exception:
            pass
        return False

    def _capture_pdf_via_cdp(self, target_path: Path) -> bool:
        """Captura PDF via Page.printToPDF do CDP."""
        try:
            time.sleep(1.0)
            try:
                WebDriverWait(self._driver, 15).until(
                    lambda d: d.execute_script(
                        "return document.readyState") == "complete")
            except (TimeoutException, UnexpectedAlertPresentException):
                pass
            time.sleep(0.5)
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
            return target_path.stat().st_size > 1000
        except Exception as e:
            self._log(f"    erro Page.printToPDF: {e}", "error")
            return False

    def _tab_pdf_url(self):
        """Se a aba atual estiver exibindo um PDF (visualizador interno
        do Chrome), devolve a URL real do arquivo — 'https://...' ou
        'blob:...'. Devolve None quando a aba e uma pagina HTML comum."""
        try:
            info = self._driver.execute_script(
                "var e=document.querySelector('embed,object');"
                "return [document.contentType||'',"
                " window.location.href||'',"
                " (e&&(e.src||e.data))||''];")
        except Exception:
            return None
        ctype, href, embed_src = (info or ["", "", ""])
        ctype = (ctype or "").lower()
        href = href or ""
        embed_src = embed_src or ""
        if "application/pdf" in ctype:
            return embed_src or href
        low = href.lower()
        if low.startswith("blob:") or low.split("?")[0].endswith(".pdf"):
            return href
        low_e = embed_src.lower()
        if low_e.startswith("blob:") or low_e.split("?")[0].endswith(".pdf"):
            return embed_src
        return None

    def _fetch_pdf_bytes(self, pdf_url):
        """Baixa os BYTES ORIGINAIS do PDF fazendo um fetch dentro da
        propria aba (contexto ja autenticado no eCAC). Funciona tanto
        para URL 'https://' quanto 'blob:'. Devolve bytes ou None."""
        script = """
            var url = arguments[0];
            var done = arguments[arguments.length - 1];
            fetch(url).then(function(r){
                if(!r.ok){ throw new Error('HTTP '+r.status); }
                return r.blob();
            }).then(function(b){
                var fr = new FileReader();
                fr.onload  = function(){ done(fr.result); };
                fr.onerror = function(){ done('ERRO:falha ao ler o blob'); };
                fr.readAsDataURL(b);
            }).catch(function(e){ done('ERRO:'+e.message); });
        """
        try:
            self._driver.set_script_timeout(40)
            resultado = self._driver.execute_async_script(script, pdf_url)
        except Exception as e:
            self._log(f"    fetch do PDF falhou: {e}", "warn")
            return None
        if not resultado or str(resultado).startswith("ERRO:"):
            self._log(f"    fetch do PDF nao retornou o arquivo: "
                      f"{resultado}", "warn")
            return None
        try:
            import base64 as _b64
            return _b64.b64decode(str(resultado).split(",", 1)[1])
        except Exception:
            return None

    def _capture_pdf(self, target_path: Path) -> bool:
        """Salva o PDF da aba atual.

        Se a aba estiver exibindo um PDF de verdade (no visualizador do
        Chrome), captura os BYTES ORIGINAIS do arquivo — gera um PDF
        limpo, com camada de texto e sem a moldura do visualizador
        (miniatura lateral, barra de ferramentas, conteudo cortado).

        So recorre ao Page.printToPDF — que 'fotografa' a pagina e
        achata tudo em imagem — quando a aba e uma pagina HTML comum.
        """
        try:
            time.sleep(1.0)
            try:
                WebDriverWait(self._driver, 15).until(
                    lambda d: d.execute_script(
                        "return document.readyState") == "complete")
            except (TimeoutException, UnexpectedAlertPresentException):
                pass
            time.sleep(0.5)

            pdf_url = self._tab_pdf_url()
            if pdf_url:
                pdf_bytes = self._fetch_pdf_bytes(pdf_url)
                if pdf_bytes and pdf_bytes[:5] == b"%PDF-":
                    target_path.write_bytes(pdf_bytes)
                    if target_path.stat().st_size > 1000:
                        return True
                self._log("    ⚠ Nao consegui pegar o PDF original; "
                          "usando captura da pagina como reserva.", "warn")
        except Exception as e:
            self._log(f"    erro ao capturar o PDF original: {e}", "warn")

        # Reserva: pagina HTML comum ou o fetch falhou
        return self._capture_pdf_via_cdp(target_path)

    def _go_to_next_page(self) -> bool:
        """Avança pra próxima página da tabela de pagamentos.

        Detecta o botão de 'próxima página' por várias estratégias
        (ver _find_next_page_button) e confirma a virada checando se a
        1ª linha da tabela mudou. Se não achar o botão, despeja um
        diagnóstico no log pra permitir ajustar o seletor."""
        first_doc_before = ""
        t = self._find_listing_table()
        if t:
            rows = self._find_rows(t)
            if rows:
                e = self._parse_row(rows[0], 0)
                if e:
                    first_doc_before = e.numero

        nxt = self._find_next_page_button()
        if not nxt:
            self._log("    Botão de próxima página não encontrado "
                      "(pode ser realmente a última página).", "info")
            self._dump_pagination_diagnostic()
            return False

        self._log("    → Avançando para a próxima página...", "info")
        try:
            self._driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", nxt)
        except Exception:
            pass
        clicked = False
        try:
            nxt.click()
            clicked = True
        except Exception:
            try:
                self._driver.execute_script("arguments[0].click();", nxt)
                clicked = True
            except Exception:
                pass
        if not clicked:
            self._log("    ⚠ Não consegui clicar no botão de próxima "
                      "página.", "warn")
            return False

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
                    if e and e.numero and e.numero != first_doc_before:
                        return True
        self._log("    ⚠ Cliquei em 'próxima página' mas a tabela não "
                  "mudou no tempo esperado. Encerrando a paginação.",
                  "warn")
        return False

    # JS: localiza o melhor candidato a botão de "próxima página".
    _JS_FIND_NEXT = r"""
        function vis(el){
          if(!el) return false;
          var s=window.getComputedStyle(el);
          if(s.display==='none'||s.visibility==='hidden') return false;
          var r=el.getBoundingClientRect();
          return r.width>0&&r.height>0;
        }
        function ok(el){
          if(el.disabled) return false;
          if((el.getAttribute('aria-disabled')||'').toLowerCase()==='true')
            return false;
          var c=(el.className||'').toString().toLowerCase();
          if(c.indexOf('disabled')>=0) return false;
          return true;
        }
        var cands=Array.prototype.slice.call(
          document.querySelectorAll('button,a,[role=button]'));
        for(var i=0;i<cands.length;i++){
          var el=cands[i];
          if(!vis(el)||!ok(el)) continue;
          var hay=((el.getAttribute('aria-label')||'')+' '
            +(el.getAttribute('title')||'')+' '+(el.id||'')+' '
            +(el.className||'').toString()).toLowerCase();
          if(/anterior|previous|\bprev\b|chevron-left|arrow-left/.test(hay))
            continue;
          if(/pr[oó]xim|seguinte|\bnext\b|chevron-right|arrow-right|angle-right|btn-next-page/.test(hay))
            return el;
          var txt=(el.textContent||'').trim();
          if(txt==='›'||txt==='»'||txt==='>'||txt==='>>') return el;
          var ic=el.querySelector('i,span,svg');
          if(ic){
            var icc=(ic.className||'').toString().toLowerCase();
            if(/chevron-right|arrow-right|angle-right|caret-right/.test(icc)
               && icc.indexOf('left')<0) return el;
          }
        }
        var navs=document.querySelectorAll('nav,ul,div');
        for(var n=0;n<navs.length;n++){
          var nav=navs[n];
          if((nav.className||'').toString().toLowerCase()
               .indexOf('pag')<0) continue;
          var items=nav.querySelectorAll('li,a,button');
          var active=null, byNum={};
          for(var k=0;k<items.length;k++){
            var it=items[k];
            var tt=(it.textContent||'').trim();
            if(!/^[0-9]+$/.test(tt)) continue;
            var num=parseInt(tt,10);
            byNum[num]=it;
            var mark=((it.className||'').toString()+' '
              +((it.parentElement&&it.parentElement.className)||'')
                 .toString()).toLowerCase();
            if(/active|current|selected|is-active/.test(mark)
               || it.getAttribute('aria-current')) active=num;
          }
          if(active!=null && byNum[active+1]){
            var tgt=byNum[active+1];
            if(tgt.tagName==='LI'){
              var inner=tgt.querySelector('a,button');
              if(inner) tgt=inner;
            }
            if(vis(tgt)&&ok(tgt)) return tgt;
          }
        }
        return null;
    """

    def _find_next_page_button(self):
        """Localiza o botão 'próxima página' do paginador do eCAC.

        Cobre várias formas de paginador: botão com id 'btn-next-page',
        aria-label 'Página seguinte', ícone chevron/seta à direita,
        caracteres '›'/'»', e paginadores numerados (clica no número
        seguinte ao ativo). Retorna o elemento clicável, ou None se não
        houver próxima página."""
        try:
            el = self._driver.execute_script(
                "return (function(){" + self._JS_FIND_NEXT + "})();")
            return el or None
        except Exception:
            return None

    def _dump_pagination_diagnostic(self) -> None:
        """Despeja no log os elementos com cara de paginador — pra
        permitir ajustar o seletor quando a virada de página falha."""
        js = r"""
            var out=[];
            var cands=document.querySelectorAll(
              'button,a,[role=button],nav,ul');
            var seen=0;
            for(var i=0;i<cands.length && seen<40;i++){
              var el=cands[i];
              var cls=(el.className||'').toString().toLowerCase();
              var aria=(el.getAttribute('aria-label')||'');
              var id=el.id||'';
              var txt=(el.textContent||'').trim()
                       .replace(/\s+/g,' ').slice(0,28);
              var hay=(cls+' '+aria+' '+id+' '+txt).toLowerCase();
              if(/pag|next|pr[oó]xim|seguinte|chevron|arrow|carregar mais|mostrar mais|ver mais|»|›/.test(hay)){
                var dis=el.disabled
                  ||(el.getAttribute('aria-disabled')||'')==='true'
                  ||cls.indexOf('disabled')>=0;
                out.push('<'+el.tagName.toLowerCase()+'> id='+(id||'-')
                  +' aria="'+aria+'" txt="'+txt+'" cls="'
                  +cls.slice(0,55)+'" disabled='+dis);
                seen++;
              }
            }
            return out.length
              ? out.join(String.fromCharCode(10))
              : '(nenhum elemento com cara de paginacao foi encontrado)';
        """
        try:
            rel = self._driver.execute_script(js) or ""
        except Exception as e:
            rel = f"(falha ao inspecionar a página: {e})"
        self._log("    --- diagnóstico do paginador ---", "warn")
        for ln in str(rel).splitlines():
            self._log("      " + ln, "info")
        self._log("    --- fim do diagnóstico do paginador ---", "warn")





# ---- Sub-aba: DCTFWeb ---------------------------------------------------------
