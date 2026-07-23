"""Robô e-CAC: download em massa de PER/DCOMP (roda em thread, callbacks injetados — sem Tkinter).

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


class PerdcompDownloader:
    """Baixa em massa os recibos de PERDCOMP do eCAC PER/DCOMP Web.

    Roda em thread separada. Comunica com a UI via callbacks injetados.
    Não importa nada de tkinter — pode ser usado em CLI também.
    """

    def __init__(self,
                 out_dir: Optional[Path] = None,
                 debug_port: int = 9222,
                 on_log: Callable[[str, str], None] = None,
                 on_progress: Callable[[dict], None] = None,
                 on_finished: Callable[[bool, dict], None] = None,
                 legacy_dir_for_migration: Optional[Path] = None):
        # out_dir agora é OPCIONAL — se None, será definido após detectar CNPJ.
        # Se passado, é usado como fallback caso a detecção falhe.
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
        # CNPJ + nome detectados após conectar ao Chrome
        self._cnpj: str = ""
        self._empresa: str = ""
        # Pasta antiga (~/AgriTax_Downloads/perdcomps) — será migrada se existir
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

            # ── Seleciona a aba do PER/DCOMP Web ANTES de detectar CNPJ ──
            # Crítico: a detecção de CNPJ lê o header da aba ATUAL. Se não
            # selecionarmos a aba do módulo primeiro, podemos detectar o
            # CNPJ de outra empresa aberta noutra aba e salvar os PDFs na
            # pasta errada.
            if not self._switch_to_perdcomp_iframe():
                self._log("✗ PER/DCOMP Web não localizado. Garanta que está "
                          "em Documentos Entregues > Pesquisar.", "error")
                summary["erro"] = "PER/DCOMP Web não localizado"
                return
            self._log("✓ Aba do PER/DCOMP Web localizada", "ok")

            # ── Detecta CNPJ ativo no eCAC (na aba do PER/DCOMP) ──────
            self._log("Detectando CNPJ ativo no eCAC...", "info")
            cnpj, nome = _dl_detect_cnpj_from_ecac(self._driver, self._log)
            if not cnpj:
                self._log("✗ CNPJ não detectado no header do eCAC. "
                          "Garanta que está logado e na tela de PER/DCOMP Web.",
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
            self.out_dir = paths["perdcomp_dir"]
            log_path = paths["log_perdcomp"]
            self._log(f"✓ Pasta: {self.out_dir}", "ok")
            self._log(f"   Log:  {log_path}", "info")

            # ── Carrega manifest e migra antigo se preciso ─────────────
            manifest = _DownloadManifest(log_path, files_root=self.out_dir)
            if self._legacy_dir and self._legacy_dir.exists():
                old_manifest = self._legacy_dir / "_manifest.json"
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

            # ── Configura download path no Chrome (CDP) ───────────────
            self._configure_download_dir()

            # Re-seleciona o iframe do PER/DCOMP (o CDP pode ter mudado
            # o contexto do Selenium)
            if not self._switch_to_perdcomp_iframe():
                self._log("✗ PER/DCOMP Web não localizado. Garanta que está em "
                          "Documentos Entregues > Pesquisar.", "error")
                summary["erro"] = "PER/DCOMP Web não localizado"
                return

            self._log("✓ iframe do PER/DCOMP Web pronto", "ok")

            page_num = 1
            # Conta falhas de timeout consecutivas. Se a sessão
            # eCAC esfriar, vários documentos seguidos dão timeout
            # — ao passar do limite, paramos de forma limpa em vez
            # de ficar minutos batendo cabeça com a sessão morta.
            falhas_seguidas = 0
            LIMITE_FALHAS_SEGUIDAS = 3
            while not self._cancel_event.is_set():
                self._stats["pagina_atual"] = page_num
                self._stats["linha_atual"] = 0
                self._log(f"━━━━━━ Página {page_num} ━━━━━━", "info")
                self._emit_progress()

                try:
                    WebDriverWait(self._driver, _DL_WAIT_TIMEOUT).until(
                        lambda d: self._find_listing_container() is not None
                        and len(self._find_rows(self._find_listing_container())) > 0
                    )
                except TimeoutException:
                    self._log(f"✗ Listagem da página {page_num} não carregou.", "error")
                    break

                entries = self._collect_entries()
                self._stats["linhas_pagina"] = len(entries)
                self._log(f"  {len(entries)} entrada(s) detectada(s)", "info")
                self._emit_progress()

                if not entries:
                    self._log("Página vazia — encerrando.", "warn")
                    break

                for idx in range(len(entries)):
                    if self._cancel_event.is_set():
                        break
                    self._stats["linha_atual"] = idx + 1
                    self._emit_progress()

                    container = self._find_listing_container()
                    if not container:
                        break
                    rows = self._find_rows(container)
                    if idx >= len(rows):
                        continue
                    row = rows[idx]
                    entry = self._parse_row(row) or entries[idx]
                    entry.pagina = page_num
                    entry.cnpj = self._cnpj

                    if manifest.is_done(entry.numero):
                        self._log(f"  [PULAR] {entry.numero} — já baixado", "info")
                        self._stats["pulados"] += 1
                        self._emit_progress()
                        continue

                    self._log(f"  [{idx+1}/{len(entries)}] {entry.numero} — "
                              f"{entry.tipo or '?'}", "info")

                    try:
                        ok = self._download_one(row, entry)
                    except Exception as ex:
                        self._log(f"    ✗ erro inesperado: {ex}", "error")
                        entry.status = "erro"
                        entry.erro = str(ex)
                        ok = False

                    # Verifica o CNPJ DENTRO do PDF e move pra pasta
                    # certa se for de outra empresa (à prova de falhas
                    # na detecção de CNPJ do eCAC).
                    if ok:
                        try:
                            self._verify_and_relocate_by_cnpj(entry)
                        except Exception as ex:
                            self._log(f"    ⚠ erro ao verificar CNPJ "
                                      f"do PDF: {ex}", "warn")

                    manifest.upsert(entry)
                    if ok and entry.status == "baixado":
                        self._stats["baixados"] += 1
                        falhas_seguidas = 0  # sucesso zera o contador
                        if entry.arquivo:
                            self._downloaded_files.append(
                                self.out_dir / entry.arquivo)
                    elif entry.status == "movido":
                        self._stats["baixados"] += 1
                        falhas_seguidas = 0
                        self._log("    ↪ contabilizado na pasta da "
                                  "empresa correta", "info")
                    else:
                        self._stats["erros"] += 1
                        self._log(f"    ✗ ERRO: {entry.erro}", "error")
                        # Timeout conta como possível sessão esfriando
                        if "Tempo esgotado" in (entry.erro or ""):
                            falhas_seguidas += 1
                        else:
                            falhas_seguidas = 0
                    self._emit_progress()

                    # Sessão eCAC esfriou? Para de forma limpa.
                    if falhas_seguidas >= LIMITE_FALHAS_SEGUIDAS:
                        self._log("", "info")
                        self._log(f"⚠ {falhas_seguidas} documentos seguidos "
                                  "deram tempo esgotado.", "warn")
                        self._log("  A sessão do eCAC provavelmente esfriou "
                                  "(ficou lenta demais).", "warn")
                        self._log("  Execução interrompida para não travar. "
                                  "O que já foi baixado está salvo.", "warn")
                        self._log("  → Atualize a página do eCAC (F5), "
                                  "confirme que está logado, e rode de novo. "
                                  "O sistema pula tudo que já baixou.", "info")
                        summary["sessao_esfriou"] = True
                        self._cancel_event.set()
                        break

                    time.sleep(_DL_DELAY_BETWEEN_DOWNLOADS)

                if self._cancel_event.is_set():
                    break

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
            if manifest is not None:
                try:
                    manifest.export_csv(self.out_dir / "_resumo.csv")
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

    def _configure_download_dir(self) -> None:
        payload = {"behavior": "allow", "downloadPath": str(self.out_dir),
                   "eventsEnabled": True}
        for cmd in ("Browser.setDownloadBehavior", "Page.setDownloadBehavior"):
            try:
                self._driver.execute_cdp_cmd(cmd, payload)
                return
            except Exception:
                continue

    def _switch_to_perdcomp_iframe(self) -> bool:
        for handle in self._driver.window_handles:
            try:
                self._driver.switch_to.window(handle)
            except Exception:
                continue
            url = (self._driver.current_url or "").lower()
            if "perdcomp" in url:
                return True
            try:
                self._driver.switch_to.default_content()
                iframes = self._driver.find_elements(By.TAG_NAME, "iframe")
                for fr in iframes:
                    src = (fr.get_attribute("src") or "").lower()
                    if "perdcomp" in src:
                        self._driver.switch_to.frame(fr)
                        return True
            except Exception:
                pass
        return False

    def _find_listing_container(self):
        for by, sel in [
            (By.TAG_NAME, "perdcomp-listar-docs-enviados"),
            (By.CSS_SELECTOR, "perdcomp-listar-docs-enviados .gs-container"),
            (By.CSS_SELECTOR, ".gs-container"),
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
        return None

    @staticmethod
    def _safe_displayed(el) -> bool:
        try:
            return el.is_displayed()
        except StaleElementReferenceException:
            return False

    def _find_rows(self, container) -> List:
        if not container:
            return []
        try:
            rows = container.find_elements(
                By.CSS_SELECTOR, "simple-collapsible.gs-striped")
            return [r for r in rows if self._safe_displayed(r)]
        except Exception:
            return []

    def _parse_row(self, row) -> Optional[_DownloadEntry]:
        try:
            text = row.text
        except StaleElementReferenceException:
            return None
        if not text:
            return None
        m = _DL_PERDCOMP_NUMBER_RE.search(text)
        if not m:
            return None
        numero = m.group(0)
        data = ""
        md = _DL_DATE_BR_RE.search(text)
        if md:
            data = md.group(0)
        tipo = ""
        try:
            ic = row.find_element(By.CSS_SELECTOR, "i.tipoDocumento")
            cls = ic.get_attribute("class") or ""
            for k, v in _DL_ICON_TIPO_MAP.items():
                if k in cls:
                    tipo = v
                    break
            if not tipo:
                tipo = cls
        except Exception:
            pass
        tipo_credito = ""
        periodo = ""
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            low = line.lower()
            if not tipo_credito and any(k in low for k in (
                "ressarcimento", "compensação", "compensacao", "saldo negativo",
                "irrf", "reintegra", "ipi", "previdenciária", "previdenciaria",
                "salário", "salario",
            )):
                tipo_credito = line
            if not periodo and ("trimestre" in low or "mês" in low or "mes" in low
                                or re.search(r"\b\d{2}/\d{4}\b", line)):
                periodo = line
        return _DownloadEntry(numero=numero, tipo=tipo, data_transmissao=data,
                              tipo_credito=tipo_credito, periodo=periodo)

    def _collect_entries(self) -> List[_DownloadEntry]:
        container = self._find_listing_container()
        if not container:
            return []
        rows = self._find_rows(container)
        out: List[_DownloadEntry] = []
        for r in rows:
            e = self._parse_row(r)
            if e:
                out.append(e)
        return out

    def _click_print(self, row) -> bool:
        try:
            icons = row.find_elements(By.CSS_SELECTOR, "i.icon-print")
        except Exception:
            return False
        if not icons:
            return False
        icon = icons[0]
        try:
            self._driver.execute_script(
                "arguments[0].scrollIntoView({block:'center', inline:'center'});",
                icon,
            )
        except Exception:
            pass
        try:
            icon.click()
            return True
        except ElementClickInterceptedException:
            pass
        except Exception:
            pass
        try:
            span = icon.find_element(By.XPATH, "./ancestor::span[1]")
            span.click()
            return True
        except Exception:
            pass
        try:
            self._driver.execute_script("arguments[0].click();", icon)
            return True
        except Exception:
            return False

    def _files_in_dir(self) -> set:
        return {p.name for p in self.out_dir.iterdir() if p.is_file()}

    def _wait_for_new_pdf(self, before: set, timeout: int) -> Optional[Path]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._cancel_event.is_set():
                return None
            time.sleep(0.5)
            now = self._files_in_dir()
            new = {n for n in (now - before) if not n.endswith(".crdownload")}
            pdfs = [n for n in new if n.lower().endswith(".pdf")]
            if pdfs:
                return self.out_dir / sorted(
                    pdfs,
                    key=lambda n: (self.out_dir / n).stat().st_mtime,
                    reverse=True,
                )[0]
            if new:
                return self.out_dir / sorted(
                    new,
                    key=lambda n: (self.out_dir / n).stat().st_mtime,
                    reverse=True,
                )[0]
        return None

    def _make_requests_session(self):
        s = requests.Session()
        for c in self._driver.get_cookies():
            s.cookies.set(c["name"], c["value"],
                          domain=c.get("domain", ""), path=c.get("path", "/"))
        try:
            ua = self._driver.execute_script("return navigator.userAgent")
        except Exception:
            ua = "Mozilla/5.0"
        s.headers.update({
            "User-Agent": ua,
            "Accept": "application/pdf,application/octet-stream,*/*",
        })
        return s

    def _download_one(self, row, entry: _DownloadEntry) -> bool:
        """Baixa um PER/DCOMP com até 2 tentativas.

        Se a 1ª tentativa falhar (geralmente timeout por sessão eCAC
        lenta), fecha eventuais abas órfãs, dá uma pausa curta e tenta
        mais uma vez. Não insiste além disso — esperas longas só
        esfriam ainda mais a sessão."""
        ok = self._download_one_attempt(row, entry)
        if ok:
            return True

        # Falhou — limpa abas órfãs antes de tentar de novo
        self._close_orphan_tabs()
        self._log("    ↻ 2ª tentativa...", "info")
        time.sleep(2.0)
        ok = self._download_one_attempt(row, entry)
        if not ok:
            self._close_orphan_tabs()
        return ok

    def _close_orphan_tabs(self) -> None:
        """Fecha abas extras que possam ter ficado abertas, voltando
        para a aba do PER/DCOMP Web."""
        try:
            handles = self._driver.window_handles
            if len(handles) <= 1:
                return
            # Mantém só a aba do PER/DCOMP; fecha o resto
            for h in list(handles):
                try:
                    self._driver.switch_to.window(h)
                    url = (self._driver.current_url or "").lower()
                    if "perdcomp" not in url and len(
                            self._driver.window_handles) > 1:
                        self._driver.close()
                except Exception:
                    continue
            self._switch_to_perdcomp_iframe()
        except Exception:
            pass

    def _download_one_attempt(self, row, entry: _DownloadEntry) -> bool:
        before = self._files_in_dir()
        main_window = self._driver.current_window_handle
        handles_before = set(self._driver.window_handles)

        if not self._click_print(row):
            entry.status = "erro"
            entry.erro = "Botão Imprimir não encontrado na linha."
            return False

        # Após clicar em Imprimir, o eCAC pode reagir de 2 formas:
        #   Caso A: abre uma nova aba com o PDF (visualizador)
        #   Caso B: dispara um download direto na pasta
        # O tempo até isso acontecer VARIA por documento. Monitoramos
        # as DUAS coisas continuamente, com timeout CURTO — timeout
        # longo esfria a sessão eCAC e trava a execução inteira.
        deadline = time.time() + _DL_DOWNLOAD_TIMEOUT
        new_handles = set()
        new_file = None
        while time.time() < deadline:
            if self._cancel_event.is_set():
                break
            # 1) Surgiu aba nova?
            new_handles = set(self._driver.window_handles) - handles_before
            if new_handles:
                break
            # 2) Surgiu arquivo na pasta? (ignora .crdownload em curso)
            now = self._files_in_dir()
            novos = {n for n in (now - before)
                     if not n.endswith(".crdownload")}
            if novos:
                pdfs = [n for n in novos if n.lower().endswith(".pdf")]
                escolhidos = pdfs if pdfs else list(novos)
                new_file = self.out_dir / sorted(
                    escolhidos,
                    key=lambda n: (self.out_dir / n).stat().st_mtime,
                    reverse=True)[0]
                break
            time.sleep(0.5)

        # ── Caso A: nova aba (visualizador) ──
        if new_handles:
            nh = next(iter(new_handles))
            self._driver.switch_to.window(nh)
            # A aba pode ainda estar carregando — dá um instante
            time.sleep(_DL_DELAY_AFTER_CLICK)
            url = self._driver.current_url
            self._log(f"    -> nova aba: {url}", "info")
            try:
                sess = self._make_requests_session()
                r = sess.get(url, timeout=60, stream=True)
                r.raise_for_status()
                target = self.out_dir / (entry.safe_basename() + ".pdf")
                with target.open("wb") as f:
                    for chunk in r.iter_content(8192):
                        if chunk:
                            f.write(chunk)
                entry.arquivo = target.name
                entry.status = "baixado"
                self._log(f"    ✓ salvo: {target.name}", "ok")
            except Exception as e:
                entry.status = "erro"
                entry.erro = f"Download via requests: {e}"
            finally:
                try:
                    self._driver.close()
                except Exception:
                    pass
                try:
                    self._driver.switch_to.window(main_window)
                except Exception:
                    pass
                self._switch_to_perdcomp_iframe()
            return entry.status == "baixado"

        # ── Caso B: download direto na pasta ──
        if new_file:
            # Pode ainda estar sendo escrito — espera estabilizar
            new_file = self._wait_file_stable(new_file)
            target = self.out_dir / (entry.safe_basename() + ".pdf")
            if target.exists():
                try:
                    target.unlink()
                except Exception:
                    pass
            try:
                new_file.rename(target)
            except Exception:
                target = new_file
            entry.arquivo = target.name
            entry.status = "baixado"
            self._log(f"    ✓ salvo: {target.name}", "ok")
            return True

        entry.status = "erro"
        entry.erro = "Tempo esgotado aguardando o PDF."
        return False

    def _wait_file_stable(self, path: Path, timeout: float = 15.0) -> Path:
        """Espera o arquivo parar de crescer (download concluído)."""
        deadline = time.time() + timeout
        ultimo = -1
        while time.time() < deadline:
            try:
                tam = path.stat().st_size
            except Exception:
                tam = -1
            if tam > 0 and tam == ultimo:
                return path
            ultimo = tam
            time.sleep(0.5)
        return path

    def _verify_and_relocate_by_cnpj(self, entry: _DownloadEntry) -> None:
        """Após baixar um PERDCOMP, lê o CNPJ de DENTRO do PDF e — se for
        de empresa diferente da pasta atual — move o arquivo pra pasta
        correta. À prova de falhas na detecção de CNPJ do eCAC."""
        if entry.status != "baixado" or not entry.arquivo:
            return
        pdf_atual = self.out_dir / entry.arquivo
        status, destino = _dl_relocate_pdf_by_cnpj(
            pdf_atual, self._cnpj, "perdcomp_dir", self._log)
        if status == "movido":
            entry.cnpj = _dl_read_cnpj_from_pdf(destino) or ""
            entry.arquivo = ""  # não está mais na out_dir desta empresa
            entry.status = "movido"
            entry.erro = f"PDF de outra empresa — movido para {destino}"

    def _get_active_page_label(self) -> Optional[str]:
        try:
            a = self._driver.find_element(
                By.CSS_SELECTOR,
                "ngb-pagination li.page-item.active a.page-link",
            )
            return (a.text or "").strip()
        except Exception:
            return None

    def _find_next_button(self):
        try:
            lis = self._driver.find_elements(
                By.XPATH,
                "//ngb-pagination//li[contains(@class,'page-item') and "
                "not(contains(@class,'disabled'))]"
                "[.//a[@aria-label='Próximo' or @aria-label='Próxima']]",
            )
            for li in lis:
                link = li.find_element(By.TAG_NAME, "a")
                if link.is_displayed():
                    return link
        except Exception:
            pass
        return None

    def _go_to_next_page(self) -> bool:
        nxt = self._find_next_button()
        if not nxt:
            return False
        label_before = self._get_active_page_label()
        first_numero_before = ""
        c = self._find_listing_container()
        if c:
            rows = self._find_rows(c)
            if rows:
                try:
                    m = _DL_PERDCOMP_NUMBER_RE.search(rows[0].text or "")
                    if m:
                        first_numero_before = m.group(0)
                except Exception:
                    pass
        try:
            self._driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", nxt)
            nxt.click()
        except Exception:
            return False

        deadline = time.time() + _DL_PAGE_CHANGE_TIMEOUT
        while time.time() < deadline:
            if self._cancel_event.is_set():
                return False
            time.sleep(0.4)
            new_label = self._get_active_page_label()
            if label_before and new_label and new_label != label_before:
                return True
            cc = self._find_listing_container()
            if cc:
                rows = self._find_rows(cc)
                if rows:
                    try:
                        m = _DL_PERDCOMP_NUMBER_RE.search(rows[0].text or "")
                        if m and m.group(0) != first_numero_before:
                            return True
                    except Exception:
                        pass
        return False


# ---- Backend: DctfDownloader (v2 — Aplicacao.aspx?id=14) ---------------------
# Baixa recibos de DCTF clássica via eCAC > Aplicacao.aspx?id=14&origem=pesquisa.
# A página é ASP.NET WebForms com tabela GridView. Cada linha tem 3 ícones
# de ação (lupa, impressora, documento). O ícone de impressora abre uma nova
# aba com a "Impressão da Declaração" — uma página HTML que precisa ser
# convertida em PDF via CDP Page.printToPDF (sem diálogo do navegador).
