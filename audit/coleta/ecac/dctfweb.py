"""Robô e-CAC: download em massa de DCTFWeb (roda em thread, callbacks injetados — sem Tkinter).

Extraído do AgriTax Audit v5 consolidado, sem alterações de lógica (M4).
"""
import re
import shutil
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


class DctfWebDownloader:
    """Baixa em massa os recibos de DCTFWeb (Débitos, Créditos, Completa)."""

    # Palavras-chave pra identificar contexto/iframe
    URL_KEYWORDS = ("dctfweb", "dctf-web", "dctf_web",
                    "listadctfs.aspx", "listadctfs", "aplicacoesweb/dctfweb")
    # Cabeçalho da tabela típica
    TABLE_HEADER_KEYWORDS = (
        "categoria", "período", "periodo", "transmissão", "transmissao",
        "situação", "situacao", "número", "numero", "recibo", "tipo", "data",
        "vencimento", "apuração", "apuracao",
    )
    # Item do dropdown "Relatórios" a baixar.
    # ESTRATÉGIA ATUAL: baixar SOMENTE o "Download XML de Saída" —
    # é um arquivo único por DCTFWeb, com todos os dados estruturados.
    # Reduz drasticamente a navegação (1 download em vez de 3), o que
    # mantém a sessão do eCAC "quente" e evita disparar o captcha.
    SUBTYPE_KEYWORDS = {
        "XML": ("download xml de saída", "download xml de saida",
                "xml de saída", "xml de saida", "download do xml"),
    }

    # Diagnóstico (logado UMA vez na primeira linha pra ajudar debug)
    _row_diagnosed: bool = False
    _detail_diagnosed: bool = False
    _dropdown_diagnosed: bool = False

    def __init__(self,
                 out_dir: Optional[Path] = None,
                 debug_port: int = 9222,
                 subtipos: Optional[List[str]] = None,
                 on_log: Callable[[str, str], None] = None,
                 on_progress: Callable[[dict], None] = None,
                 on_finished: Callable[[bool, dict], None] = None,
                 legacy_dir_for_migration: Optional[Path] = None):
        self.out_dir = Path(out_dir) if out_dir else None
        self.debug_port = debug_port
        # Quais relatórios baixar — default: só o XML de Saída
        self.subtipos = subtipos or ["XML"]
        self.on_log = on_log or (lambda *a, **k: None)
        self.on_progress = on_progress or (lambda *a, **k: None)
        self.on_finished = on_finished or (lambda *a, **k: None)
        self._cancel_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._driver = None
        self._stats = {
            "baixados": 0, "erros": 0, "pulados": 0,
            "pagina_atual": 0, "linha_atual": 0, "linhas_pagina": 0,
            "subtipos_baixados": 0, "subtipos_erros": 0,
        }
        self._downloaded_files: List[Path] = []
        self._cnpj: str = ""
        self._empresa: str = ""
        self._legacy_dir = legacy_dir_for_migration
        # Manifesto de trabalho atual (provisório até o CNPJ ser lido do
        # 1º XML; depois passa a ser o manifesto definitivo da empresa).
        self._manifest: Optional[_DownloadManifest] = None
        # True enquanto o CNPJ ainda não foi lido de dentro de um XML.
        self._cnpj_pendente: bool = False

    # -------- API pública --------
    def start(self) -> None:
        if not (SELENIUM_OK and REQUESTS_OK):
            self.on_finished(False,
                {"erro": "Dependências (selenium, requests) não instaladas."})
            return
        if self._thread and self._thread.is_alive():
            self._log("Já há um download em andamento.", "warn")
            return
        DctfWebDownloader._row_diagnosed = False
        DctfWebDownloader._detail_diagnosed = False
        DctfWebDownloader._dropdown_diagnosed = False
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
            (target_dir / f"_debug_dctfweb_{tag}_{ts}.html").write_text(
                self._driver.page_source, encoding="utf-8")
            try:
                self._driver.save_screenshot(
                    str(target_dir / f"_debug_dctfweb_{tag}_{ts}.png"))
            except Exception:
                pass
            self._log(f"Snapshot debug salvo: _debug_dctfweb_{tag}_{ts}.html",
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

            # ── Seleciona a aba da DCTFWeb ANTES de detectar o CNPJ ──
            # A detecção lê o header da aba atual; selecionar primeiro a
            # aba do módulo evita detectar o CNPJ de outra empresa.
            if not self._switch_to_listing_context():
                self._log("✗ Tela de listagem da DCTFWeb não localizada. "
                          "Garanta que está na aba do módulo DCTFWeb com a "
                          "tabela de declarações visível.", "error")
                self._save_debug_snapshot("contexto_nao_localizado")
                summary["erro"] = "Listagem DCTFWeb não localizada"
                return
            self._log("✓ Aba da DCTFWeb localizada", "ok")

            # CNPJ: NÃO é detectado pela tela do eCAC (instável). Os XMLs
            # são baixados numa área provisória; após o 1º download o CNPJ
            # é lido de DENTRO do próprio XML e tudo é movido para a pasta
            # da empresa — a organização por empresa é preservada.
            self._cnpj = ""
            self._empresa = ""
            self._cnpj_pendente = True
            self.out_dir = _dl_get_root_dir() / "_dctfweb_provisorio"
            try:
                self.out_dir.mkdir(parents=True, exist_ok=True)
                # manifesto provisório começa limpo a cada execução
                (self.out_dir / "log_dctfweb.json").unlink()
            except FileNotFoundError:
                pass
            except Exception as e:
                self._log(f"✗ Erro ao preparar pasta provisória: {e}",
                          "error")
                summary["erro"] = f"Erro pasta: {e}"
                return
            log_path = self.out_dir / "log_dctfweb.json"
            self._log("ⓘ Os XMLs serão baixados e organizados por empresa "
                      "automaticamente — o CNPJ é lido de dentro do XML.",
                      "info")
            self._log(f"   Subtipos a baixar: {', '.join(self.subtipos)}",
                      "info")

            # Manifest de trabalho (provisório). Após detectar o CNPJ no
            # 1º XML, é substituído pelo manifesto definitivo da empresa.
            manifest = _DownloadManifest(log_path, files_root=self.out_dir)
            self._manifest = manifest

            for entry in manifest.entries.values():
                if entry.status == "baixado" and entry.arquivo:
                    p = self.out_dir / entry.arquivo
                    if p.exists():
                        self._downloaded_files.append(p)
            self._log(f"   Já baixados anteriormente: "
                      f"{len(self._downloaded_files)} PDF(s)", "info")

            # Re-seleciona o contexto da listagem (garantia)
            if not self._switch_to_listing_context():
                self._log("✗ Tela de listagem da DCTFWeb não localizada.",
                          "error")
                self._save_debug_snapshot("contexto_nao_localizado")
                summary["erro"] = "Listagem DCTFWeb não localizada"
                return

            self._log("✓ Contexto DCTFWeb pronto", "ok")

            page_num = 1
            self._dctfweb_page_num = 1  # rastreio p/ paginação segura
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
                    self._log(f"✗ Listagem página {page_num} não carregou.",
                              "error")
                    self._save_debug_snapshot(f"page{page_num}_nao_carregou")
                    break

                entries = self._collect_entries()
                self._stats["linhas_pagina"] = len(entries)
                self._log(f"  {len(entries)} DCTFWeb detectada(s) na página",
                          "info")
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

                    container = self._find_listing_container()
                    if not container:
                        break
                    rows = self._find_rows(container)
                    if idx >= len(rows):
                        continue
                    row = rows[idx]
                    entry_base = self._parse_row(row, idx) or entries[idx]
                    entry_base.pagina = page_num
                    entry_base.kind = "DCTFWEB"
                    entry_base.cnpj = self._cnpj

                    label = (f"{entry_base.periodo or '?'} | "
                             f"{entry_base.categoria or '?'} | "
                             f"{entry_base.tipo or '?'}")
                    self._log(f"[{idx+1}/{len(entries)}] {label}", "info")

                    # Antes de abrir o detalhe, checa se já baixou todos os
                    # subtipos solicitados — se sim, pula sem clicar
                    all_done = True
                    for sub in self.subtipos:
                        e_sub = self._make_subtipo_entry(entry_base, sub)
                        if not manifest.is_done(e_sub.numero):
                            all_done = False
                            break
                    if all_done:
                        self._log(f"  [PULAR] todos os {len(self.subtipos)} "
                                  f"subtipos já baixados", "info")
                        self._stats["pulados"] += len(self.subtipos)
                        self._emit_progress()
                        continue

                    # Abre o detalhe e baixa os 3 (ou os que faltarem)
                    try:
                        self._process_dctfweb_row(row, entry_base)
                    except Exception as ex:
                        self._log(f"  ✗ erro inesperado: {ex}", "error")
                        for sub in self.subtipos:
                            e_sub = self._make_subtipo_entry(entry_base, sub)
                            if not self._manifest.is_done(e_sub.numero):
                                e_sub.status = "erro"
                                e_sub.erro = str(ex)
                                self._manifest.upsert(e_sub)
                                self._stats["erros"] += 1
                        self._emit_progress()

                    # Re-sincroniza: o manifesto pode ter sido trocado pelo
                    # definitivo da empresa após a leitura do CNPJ no XML.
                    manifest = self._manifest
                    time.sleep(_DL_DELAY_BETWEEN_DOWNLOADS)

                if self._cancel_event.is_set():
                    break

                if self._go_to_next_page():
                    page_num += 1
                    continue
                self._log("Última página alcançada (ou paginação não localizada).", "info")
                self._log(self._diagnose_pagination(), "info")
                break

            success = not self._cancel_event.is_set()
        except Exception as e:
            self._log(f"✗ Erro fatal: {e}", "error")
            summary["erro"] = str(e)
        finally:
            if manifest is not None and self.out_dir is not None:
                try:
                    manifest.export_csv(self.out_dir / "_resumo_dctfweb.csv")
                except Exception:
                    pass
            summary.update(self._stats)
            summary["arquivos_baixados"] = list(self._downloaded_files)
            summary["cancelado"] = self._cancel_event.is_set()
            summary["cnpj"] = self._cnpj
            summary["empresa"] = self._empresa
            summary["pasta_saida"] = str(self.out_dir) if self.out_dir else ""
            self.on_finished(success, summary)

    def _make_subtipo_entry(self, base: _DownloadEntry,
                            subtipo: str) -> _DownloadEntry:
        """Cria uma entry derivada da base, marcando um subtipo específico.
        A chave única no manifest é (período + categoria + subtipo).
        """
        from dataclasses import replace
        new = replace(base)
        new.subtipo = subtipo
        # Chave única: período_categoria_subtipo (sem espaços/barras)
        per_norm = _DownloadEntry._normalize_periodo_aaaamm(base.periodo)
        cat_norm = re.sub(r"[^A-Za-z0-9]", "_",
                          (base.categoria or "SEM_CAT").upper())
        new.numero = f"{per_norm}_{cat_norm}_{subtipo}"
        return new

    def _attach_to_chrome(self):
        options = SelOptions()
        options.add_experimental_option("debuggerAddress",
                                        f"localhost:{self.debug_port}")
        return webdriver.Chrome(options=options)

    # ---- Navegação / contexto -------------------------------------------
    def _switch_to_listing_context(self) -> bool:
        """Procura a aba/iframe da DCTFWeb."""
        for handle in self._driver.window_handles:
            try:
                self._driver.switch_to.window(handle)
            except Exception:
                continue
            url = (self._driver.current_url or "").lower()
            if any(kw in url for kw in self.URL_KEYWORDS):
                if self._find_listing_container() is not None:
                    return True
                # Desce nos iframes
                try:
                    self._driver.switch_to.default_content()
                    iframes = self._driver.find_elements(By.TAG_NAME, "iframe")
                    for i, fr in enumerate(iframes):
                        try:
                            self._driver.switch_to.frame(fr)
                            if self._find_listing_container() is not None:
                                self._log(f"   ✓ Container em iframe[{i}]",
                                          "ok")
                                return True
                            self._driver.switch_to.default_content()
                        except Exception:
                            try:
                                self._driver.switch_to.default_content()
                            except Exception:
                                pass
                            continue
                    self._driver.switch_to.default_content()
                    return True  # URL bate mesmo sem achar container
                except Exception:
                    pass

        # Critério secundário: qualquer iframe com 'dctfweb' no src
        for handle in self._driver.window_handles:
            try:
                self._driver.switch_to.window(handle)
            except Exception:
                continue
            try:
                self._driver.switch_to.default_content()
                iframes = self._driver.find_elements(By.TAG_NAME, "iframe")
                for i, fr in enumerate(iframes):
                    src = (fr.get_attribute("src") or "").lower()
                    if any(kw in src for kw in self.URL_KEYWORDS):
                        try:
                            self._driver.switch_to.frame(fr)
                            return True
                        except Exception:
                            continue
            except Exception:
                pass
        return False

    # ---- Tabela / linhas ------------------------------------------------
    def _find_listing_container(self):
        """Localiza o container da listagem 'Relação de Declarações'.

        DCTFWeb é ASP.NET WebForms (não Angular como inicialmente assumido) —
        prioriza <table> HTML com cabeçalho contendo as colunas conhecidas.
        Mantém fallbacks Angular caso outras telas variem.
        """
        # Estratégia 1: <table> com cabeçalho contendo colunas conhecidas
        # (Período de Apuração, Data Transmissão, Categoria, ...)
        try:
            tables = self._driver.find_elements(By.TAG_NAME, "table")
            for tbl in tables:
                try:
                    if not tbl.is_displayed():
                        continue
                    header_text = " ".join(
                        th.text.lower()
                        for th in tbl.find_elements(By.XPATH, ".//th")
                    )
                    if not header_text:
                        # Sem <th> — pega primeiro tr
                        first_row = tbl.find_elements(By.XPATH, ".//tr[1]/td")
                        header_text = " ".join(c.text.lower()
                                               for c in first_row)
                    matches = sum(1 for kw in self.TABLE_HEADER_KEYWORDS
                                  if kw in header_text)
                    if matches >= 3:
                        return tbl
                except StaleElementReferenceException:
                    continue
        except Exception:
            pass

        # Estratégia 2: fallbacks Angular + ID com Grid
        candidates = [
            (By.CSS_SELECTOR, "table[id*='Grid']"),
            (By.CSS_SELECTOR, "table[id*='grid']"),
            (By.CSS_SELECTOR, "table[id*='Lista']"),
            (By.CSS_SELECTOR, "table[id*='Declar']"),
            (By.TAG_NAME, "dctfweb-listar"),
            (By.TAG_NAME, "app-listar"),
            (By.CSS_SELECTOR, "[class*='listar'] .gs-container"),
            (By.CSS_SELECTOR, ".gs-container"),
        ]
        for by, sel in candidates:
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

        # Estratégia 3: maior tabela visível (último recurso)
        try:
            tables = self._driver.find_elements(By.TAG_NAME, "table")
            largest, largest_n = None, 0
            for tbl in tables:
                try:
                    if not tbl.is_displayed():
                        continue
                    n = len(tbl.find_elements(By.XPATH, ".//tbody/tr"))
                    if n == 0:
                        n = max(0, len(tbl.find_elements(By.TAG_NAME, "tr"))
                                - 1)
                    if n > largest_n:
                        largest, largest_n = tbl, n
                except StaleElementReferenceException:
                    continue
            if largest and largest_n >= 1:
                return largest
        except Exception:
            pass
        return None

    @staticmethod
    def _safe_displayed(el) -> bool:
        try:
            return el.is_displayed()
        except StaleElementReferenceException:
            return False

    def _find_rows(self, container) -> List:
        """Retorna SOMENTE as linhas de DADOS da listagem.

        DCTFWeb é ASP.NET GridView (id='ctl00_..._tabelaListagemDctf_...').
        Estrutura típica tem múltiplas <tr> que não são dados:
          • Linha de cabeçalho (Período, Data Transmissão, Categoria, ...)
          • Linha de "marcar todas" (com checkboxes)
          • Linha de totalizador / paginação

        Filtros aplicados:
          1. <tr> precisa ter <td> filhos DIRETOS (não só descendentes)
          2. Pelo menos N colunas (a tabela tem 9: Per | Data | Cat | Orig
             | Tipo | Sit | Déb | Saldo | Serviços)
          3. Texto da linha precisa ter padrão de DADOS:
             - Data DD/MM/AAAA (data de transmissão) OU
             - Valor monetário (R$ ou ponto/vírgula decimal) OU
             - Número de 4 dígitos isolado (ano)
          4. Não pode conter palavras-chave de cabeçalho
             (Período de Apuração, Saldo a Pagar, Serviços, etc.)
        """
        if not container:
            return []
        try:
            # Padrão Angular do eCAC (igual PER/DCOMP Web) — se existir
            ang_rows = container.find_elements(
                By.CSS_SELECTOR, "simple-collapsible.gs-striped")
            if ang_rows:
                return [r for r in ang_rows if self._safe_displayed(r)]

            # ASP.NET: pega TODAS as <tr> mas filtra agressivamente
            all_trs = container.find_elements(By.TAG_NAME, "tr")
            if not all_trs:
                return []

            # Conta colunas (td filho direto) de cada tr — descobre o
            # número modal de colunas (linhas de dados terão todas)
            rows_with_td = []
            for tr in all_trs:
                try:
                    direct_tds = tr.find_elements(By.XPATH, "./td")
                    if direct_tds:
                        rows_with_td.append((tr, len(direct_tds)))
                except StaleElementReferenceException:
                    continue

            if not rows_with_td:
                return []

            max_tds = max(c for _, c in rows_with_td)
            # Linhas de dados têm o número máximo (ou perto) de colunas
            min_cols = max(5, max_tds - 1)

            # Keywords que NUNCA aparecem juntas em linha de dados
            # (são marcadores de cabeçalho)
            header_keywords = (
                "período de apuração", "periodo de apuracao",
                "data transmissão", "data transmissao",
                "saldo a pagar", "débito apurado", "debito apurado",
                "marcar tod",
            )

            filtered = []
            for tr, n_td in rows_with_td:
                if n_td < min_cols:
                    continue
                if not self._safe_displayed(tr):
                    continue
                try:
                    text = (tr.text or "").strip()
                except StaleElementReferenceException:
                    continue
                if not text:
                    continue
                text_low = text.lower()
                # Pula cabeçalhos/totais
                if any(kw in text_low for kw in header_keywords):
                    continue
                # Linha de dados tem que ter: data DD/MM/AAAA OU ano
                # 4 dígitos OU valor decimal
                tem_data = bool(re.search(r"\b\d{2}/\d{2}/\d{4}\b", text))
                tem_ano = bool(re.search(r"\b20\d{2}\b", text))
                tem_valor = bool(re.search(r"\d{1,3}(?:\.\d{3})*,\d{2}", text))
                if not (tem_data or tem_ano or tem_valor):
                    continue
                filtered.append(tr)
            return filtered
        except Exception:
            return []

    def _parse_row(self, row, idx: int = 0) -> Optional[_DownloadEntry]:
        """Extrai metadados de uma linha da listagem.
        Detecta período, categoria e situação por heurísticas.
        """
        try:
            text = row.text
        except StaleElementReferenceException:
            return None
        if not text:
            return None

        # Texto sem acento pra match tolerante
        try:
            text_norm = unicodedata.normalize("NFKD", text)
            text_norm = "".join(c for c in text_norm
                                if not unicodedata.combining(c))
        except Exception:
            text_norm = text
        text_low = text_norm.lower()

        # ── Período ──
        # DCTFWeb pode aparecer como:
        #   • "Janeiro/2024"           (mensal)
        #   • "01/2024"                (mensal, formato curto)
        #   • "1º Trimestre/2024"      (trimestral)
        #   • "2024"                   (anual — 13º salário, etc.)
        periodo = ""
        for nome in ("janeiro", "fevereiro", "marco", "abril", "maio",
                     "junho", "julho", "agosto", "setembro", "outubro",
                     "novembro", "dezembro"):
            m = re.search(rf"({nome})\s*/?\s*(\d{{4}})", text_low,
                          re.IGNORECASE)
            if m:
                # Capitaliza primeira letra (Marco → Marco)
                periodo = f"{m.group(1).capitalize()}/{m.group(2)}"
                break
        if not periodo:
            m = re.search(r"\b(\d{2})/(\d{4})\b", text)
            if m:
                periodo = f"{m.group(1)}/{m.group(2)}"
        if not periodo:
            m = re.search(r"(\d)[ºo°]?\s*trimestre[\s/]*(\d{4})",
                          text_low, re.IGNORECASE)
            if m:
                periodo = f"{m.group(1)}º Trimestre/{m.group(2)}"
        if not periodo:
            # Anual (só o ano) — pega o primeiro número de 4 dígitos
            # que pareça ano (2000-2099)
            m = re.search(r"\b(20\d{2})\b", text)
            if m:
                periodo = m.group(1)  # ex: "2024"

        # ── Categoria ──
        categoria = ""
        for kw, nome in [
            ("13º sal", "13_SALARIO"), ("13o sal", "13_SALARIO"),
            ("13 sal", "13_SALARIO"), ("13o salario", "13_SALARIO"),
            ("13º salario", "13_SALARIO"),
            ("decimo terceiro", "13_SALARIO"),
            ("13º", "13_SALARIO"), ("13o", "13_SALARIO"),
            ("afericao", "AFERICAO"),
            ("reclamat", "RECLAMATORIA"),
            ("espolio", "ESPOLIO"),
            ("comercializ", "COMERCIALIZACAO"),
            ("rural", "RURAL"),
            ("geral", "GERAL"),
        ]:
            if kw in text_low:
                categoria = nome
                break
        if not categoria:
            categoria = "GERAL"  # default mais comum

        # ── Data de transmissão ──
        # Aceita "DD/MM/AAAA HH:MM:SS" ou "DD/MM/AAAA"
        data = ""
        m = re.search(r"\b(\d{2}/\d{2}/\d{4})(?:\s+\d{2}:\d{2}(?::\d{2})?)?",
                      text)
        if m:
            data = m.group(1)
        else:
            dates = _DL_DATE_BR_RE.findall(text)
            if dates:
                data = dates[0]

        # ── Tipo/Situação ──
        situacao = ""
        for kw in ("Original", "Retificadora", "Ativa", "Cancelada",
                   "Em Andamento", "Transmitida", "Em andamento"):
            if kw.lower().replace(" ", "") in text_low.replace(" ", ""):
                situacao = kw
                break
        if not situacao:
            # Combina Tipo + Situação se aparecerem juntos
            partes = []
            for kw in ("original", "retificadora"):
                if kw in text_low:
                    partes.append(kw.capitalize())
                    break
            for kw in ("ativa", "cancelada", "em andamento"):
                if kw in text_low:
                    partes.append(kw.capitalize())
                    break
            if partes:
                situacao = " ".join(partes)

        # Chave base (período + categoria) — o subtipo será apendado em
        # _make_subtipo_entry
        per_norm = _DownloadEntry._normalize_periodo_aaaamm(periodo)
        numero_base = f"{per_norm}_{categoria}_linha_{idx}"

        return _DownloadEntry(
            numero=numero_base,
            tipo=situacao,
            data_transmissao=data,
            periodo=periodo,
            kind="DCTFWEB",
            categoria=categoria,
        )

    def _collect_entries(self) -> List[_DownloadEntry]:
        container = self._find_listing_container()
        if not container:
            return []
        rows = self._find_rows(container)
        out: List[_DownloadEntry] = []
        for i, r in enumerate(rows):
            e = self._parse_row(r, i)
            if e:
                out.append(e)
        return out

    # ---- Processamento de uma DCTFWeb (abrir detalhe + 3 recibos) -------
    def _process_dctfweb_row(self, row, entry_base: _DownloadEntry) -> None:
        """Para a DCTFWeb da linha:
          1. Abre o detalhe (clica na linha ou no ícone de visualizar)
          2. Para cada subtipo solicitado, baixa o recibo
          3. Volta pra listagem
        """
        manifest = self._manifest
        main_window = self._driver.current_window_handle
        handles_before = set(self._driver.window_handles)
        try:
            url_top_before = self._driver.execute_script(
                "return window.top.location.href")
        except Exception:
            url_top_before = ""

        # DIAGNÓSTICO da linha (só na primeira) — lista todos os clicáveis
        # da linha pra ajudar a identificar o ícone do olho
        if not DctfWebDownloader._row_diagnosed:
            self._log_row_inspection(row, "(primeira)")
            DctfWebDownloader._row_diagnosed = True

        # Clica pra abrir o detalhe
        if not self._click_to_open_detail(row):
            self._log("  ✗ Não consegui abrir o detalhe da DCTFWeb",
                      "error")
            for sub in self.subtipos:
                e_sub = self._make_subtipo_entry(entry_base, sub)
                if not manifest.is_done(e_sub.numero):
                    e_sub.status = "erro"
                    e_sub.erro = "Não consegui abrir o detalhe da linha"
                    manifest.upsert(e_sub)
                    self._stats["erros"] += 1
            return

        # Aguarda detalhe carregar (nova aba OU URL muda OU container muda)
        deadline = time.time() + 15.0
        new_handle = None
        same_tab_navigated = False
        while time.time() < deadline:
            time.sleep(0.4)
            had_alert, alert_text, expired = self._check_and_handle_alert()
            if had_alert:
                self._log(f"  ⚠ Alert: {alert_text!r}", "warn")
                if expired:
                    self._cancel_event.set()
                    self._log("  ✗ SESSÃO DO eCAC EXPIROU.", "error")
                    return
                continue
            try:
                new_handles = set(self._driver.window_handles) - handles_before
                if new_handles:
                    new_handle = next(iter(new_handles))
                    break
            except Exception:
                pass
            try:
                cur = self._driver.execute_script(
                    "return window.top.location.href")
                if cur and cur != url_top_before:
                    same_tab_navigated = True
                    break
            except Exception:
                pass

        if new_handle:
            self._driver.switch_to.window(new_handle)
        # Aguarda renderização do detalhe
        time.sleep(2.0)

        # VERIFICAÇÃO: confirma que estamos REALMENTE na tela de detalhe
        # (o dropdown 'Relatórios' precisa existir). Se não estiver — pode
        # ter caído no captcha ou a navegação não completou — tenta
        # esperar/recuperar antes de prosseguir.
        if not self._wait_for_detail_screen():
            self._log("  ✗ Tela de detalhe não carregou (dropdown "
                      "'Relatórios' ausente). Pode ter caído no captcha.",
                      "error")
            for sub in self.subtipos:
                e_sub = self._make_subtipo_entry(entry_base, sub)
                if not manifest.is_done(e_sub.numero):
                    e_sub.status = "erro"
                    e_sub.erro = ("Tela de detalhe não carregou "
                                  "(dropdown Relatórios ausente)")
                    manifest.upsert(e_sub)
                    self._stats["erros"] += 1
            self._emit_progress()
            # Tenta voltar pra listagem mesmo assim
            if new_handle:
                try:
                    self._driver.close()
                except Exception:
                    pass
                try:
                    self._driver.switch_to.window(main_window)
                except Exception:
                    pass
            else:
                self._return_to_listing_via_back()
            self._switch_to_listing_context()
            return

        # Diagnóstico (1ª vez) — lista botões da tela de detalhe pra debug
        if not DctfWebDownloader._detail_diagnosed:
            self._log_detail_inspection()
            DctfWebDownloader._detail_diagnosed = True

        # Pra cada subtipo, tenta baixar
        for sub in self.subtipos:
            if self._cancel_event.is_set():
                break
            e_sub = self._make_subtipo_entry(entry_base, sub)
            if manifest.is_done(e_sub.numero):
                self._log(f"   [PULAR sub] {sub} — já baixado", "info")
                self._stats["pulados"] += 1
                self._emit_progress()
                continue

            self._log(f"   → baixando {sub}...", "info")
            try:
                ok = self._download_subtipo(sub, e_sub)
            except Exception as ex:
                self._log(f"     ✗ erro: {ex}", "error")
                e_sub.status = "erro"
                e_sub.erro = str(ex)
                ok = False

            manifest.upsert(e_sub)
            if ok:
                self._stats["baixados"] += 1
                self._stats["subtipos_baixados"] += 1
                if e_sub.arquivo:
                    self._downloaded_files.append(self.out_dir / e_sub.arquivo)
                # CNPJ ainda desconhecido: lê de DENTRO deste XML e
                # organiza os arquivos por empresa (move para a pasta
                # definitiva e troca o manifesto pelo da empresa).
                if self._cnpj_pendente and e_sub.arquivo:
                    cnpj_xml = _dl_read_cnpj_from_zip(
                        self.out_dir / e_sub.arquivo, self._log)
                    if cnpj_xml:
                        self._promote_to_company_folder(cnpj_xml)
                        manifest = self._manifest
                    else:
                        self._log("   ⚠ CNPJ não localizado dentro do XML "
                                  "— tento ler no próximo download.", "warn")
            else:
                self._stats["erros"] += 1
                self._stats["subtipos_erros"] += 1
                self._log(f"     ✗ ERRO: {e_sub.erro}", "error")
            self._emit_progress()
            time.sleep(0.5)

        # Volta pra listagem
        if new_handle:
            try:
                self._driver.close()
            except Exception:
                pass
            try:
                self._driver.switch_to.window(main_window)
            except Exception:
                pass
        elif same_tab_navigated:
            # IMPORTANTE: a volta pra listagem TEM que ser via botão Voltar
            # do NAVEGADOR (driver.back()). O botão "Voltar" da própria
            # página DCTFWeb leva pra tela de Captcha.aspx ("Sou humano"),
            # não pra listagem. Por isso usamos back() do browser e
            # verificamos se realmente voltamos pra listagem.
            self._return_to_listing_via_back()
        self._switch_to_listing_context()

    def _promote_to_company_folder(self, cnpj: str) -> None:
        """Lido o CNPJ de dentro de um XML: cria a pasta definitiva da
        empresa, move para lá os arquivos da área provisória e passa a
        usar o manifesto definitivo da empresa. Chamado uma única vez,
        após o 1º download bem-sucedido."""
        import shutil
        try:
            paths = _dl_ensure_company_dirs(cnpj)
        except Exception as e:
            self._log(f"   ⚠ CNPJ {cnpj} inválido p/ criar pasta ({e}) — "
                      f"arquivos ficam na área provisória.", "warn")
            return

        prov_dir = self.out_dir
        new_dir = paths["dctfweb_dir"]
        new_log = paths["log_dctfweb"]

        # Manifesto definitivo da empresa — pode já existir de execuções
        # anteriores; é o que garante o resume entre execuções.
        company_manifest = _DownloadManifest(new_log, files_root=new_dir)

        # Migra do manifesto de uma pasta legada, se houver.
        if self._legacy_dir and self._legacy_dir.exists():
            old_manifest = self._legacy_dir / "_manifest_dctfweb.json"
            if old_manifest.exists():
                try:
                    n = company_manifest.migrate_from(
                        old_manifest, self._legacy_dir)
                    if n > 0:
                        self._log(f"   ✓ Migradas {n} entradas do manifesto "
                                  f"antigo", "ok")
                except Exception:
                    pass

        # Move os arquivos da área provisória para a pasta da empresa.
        movidos = 0
        for f in sorted(prov_dir.glob("*")):
            if (f.is_dir() or f.suffix.lower() == ".json"
                    or f.name.startswith("_resumo")):
                continue
            destino = new_dir / f.name
            if destino.exists():
                # Já baixado numa execução anterior — descarta a cópia nova
                try:
                    f.unlink()
                except Exception:
                    pass
                continue
            try:
                shutil.move(str(f), str(destino))
                movidos += 1
            except Exception as e:
                self._log(f"   ⚠ não consegui mover {f.name}: {e}", "warn")

        # Migra as entradas do manifesto provisório, sem sobrescrever as
        # da empresa que já constem como baixadas.
        for num, e in self._manifest.entries.items():
            ja = company_manifest.entries.get(num)
            if ja is not None and ja.status == "baixado":
                continue
            e.cnpj = cnpj
            company_manifest.entries[num] = e
        try:
            company_manifest._save()
        except Exception:
            pass

        # Re-aponta os caminhos dos arquivos já baixados nesta execução.
        self._downloaded_files = [new_dir / pp.name
                                  for pp in self._downloaded_files]

        # Passa a usar a pasta e o manifesto definitivos.
        self.out_dir = new_dir
        self._manifest = company_manifest
        self._cnpj = cnpj
        self._cnpj_pendente = False

        # Limpa a área provisória (manifesto/resumo e a pasta, se vazia).
        for lixo in ("log_dctfweb.json", "_resumo_dctfweb.csv"):
            try:
                (prov_dir / lixo).unlink()
            except Exception:
                pass
        try:
            prov_dir.rmdir()
        except Exception:
            pass

        self._log(f"   ✓ CNPJ lido do XML: {cnpj}", "ok")
        if movidos:
            self._log(f"   ✓ {movidos} arquivo(s) organizado(s) em: "
                      f"{new_dir}", "ok")
        else:
            self._log(f"   ✓ Pasta da empresa: {new_dir}", "ok")

    def _wait_for_detail_screen(self, timeout: float = 20.0) -> bool:
        """Espera a tela de DETALHE da DCTFWeb carregar — confirmada pela
        presença do dropdown 'Relatórios'. Retorna True se carregou."""
        def _norm(s):
            try:
                n = unicodedata.normalize("NFKD", s or "")
                return "".join(c for c in n
                                if not unicodedata.combining(c)).lower()
            except Exception:
                return (s or "").lower()

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._cancel_event.is_set():
                return False
            # Checa alert no caminho
            had_alert, alert_text, expired = self._check_and_handle_alert()
            if had_alert and expired:
                self._cancel_event.set()
                return False
            # Caiu no captcha? Espera o usuário resolver — NÃO dá back
            # (back sairia ainda mais longe da tela certa).
            try:
                url = (self._driver.current_url or "").lower()
                if "captcha" in url:
                    self._log("     ⚠ Verificação 'Sou humano' apareceu. "
                              "Resolva no Chrome para continuar.", "warn")
                    if not self._wait_user_solves_captcha():
                        return False
                    # Usuário resolveu — recomeça a checagem do detalhe
                    continue
            except Exception:
                pass
            # Procura o dropdown 'Relatórios'
            try:
                toggles = self._driver.find_elements(
                    By.CSS_SELECTOR,
                    ".dropdown-toggle, [class*='dropdown-toggle']")
                for t in toggles:
                    try:
                        if t.is_displayed() and \
                           "relatorio" in _norm(t.text or ""):
                            return True
                    except Exception:
                        continue
            except Exception:
                pass
            time.sleep(0.6)
        return False

    def _return_to_listing_via_back(self) -> None:
        """Volta pra a listagem de DCTFWeb com UM ÚNICO clique no botão
        Voltar do NAVEGADOR.

        IMPORTANTE (confirmado pelo usuário): da tela de detalhe, basta
        1 clique no Voltar do navegador pra voltar à listagem. Dar
        back() múltiplas vezes FAZ SAIR da listagem e cair no captcha —
        por isso aqui é UM back() só, seguido de espera e verificação
        (sem re-clicar)."""
        try:
            self._driver.back()
        except Exception:
            pass

        # Aguarda a listagem recarregar (sem dar back() de novo)
        deadline = time.time() + _DL_WAIT_TIMEOUT
        while time.time() < deadline:
            if self._cancel_event.is_set():
                return
            time.sleep(0.6)
            try:
                url = (self._driver.current_url or "").lower()
            except Exception:
                url = ""
            # Caiu no captcha? Avisa, mas NÃO dá back de novo
            # (voltar mais sairia ainda mais longe da listagem).
            if "captcha" in url:
                self._log("     ⚠ A página foi pra verificação 'Sou "
                          "humano'. Resolva o captcha no Chrome — o "
                          "download continua sozinho depois.", "warn")
                # Espera o usuário resolver o captcha manualmente
                self._wait_user_solves_captcha()
                return
            # Chegou na listagem?
            if "listadctfs" in url:
                try:
                    WebDriverWait(self._driver, 10).until(
                        lambda d: self._find_listing_container() is not None)
                except TimeoutException:
                    pass
                return
            # Sem URL clara — checa se a tabela está presente
            try:
                cont = self._find_listing_container()
                if cont is not None and self._find_rows(cont):
                    return
            except Exception:
                pass
        # Timeout — pode ter caído em algo inesperado
        self._log("     ⚠ Listagem não recarregou após o Voltar. "
                  "Verifique o Chrome.", "warn")

    def _wait_user_solves_captcha(self, timeout: float = 300.0) -> bool:
        """Pausa e espera o usuário resolver o captcha 'Sou humano'
        manualmente no Chrome. Detecta automaticamente quando a tela
        sai do captcha e volta pra listagem. Timeout de 5 min.

        Retorna True se a listagem reapareceu, False se desistiu."""
        self._log("     ⏸ PAUSADO — aguardando você resolver o captcha "
                  "no Chrome (até 5 min)...", "warn")
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._cancel_event.is_set():
                return False
            time.sleep(1.5)
            try:
                url = (self._driver.current_url or "").lower()
            except Exception:
                url = ""
            # Ainda no captcha — continua esperando
            if "captcha" in url:
                continue
            # Saiu do captcha — verifica se voltou pra listagem
            if "listadctfs" in url:
                self._log("     ▶ Captcha resolvido — retomando "
                          "download.", "ok")
                try:
                    WebDriverWait(self._driver, 15).until(
                        lambda d: self._find_listing_container() is not None)
                except TimeoutException:
                    pass
                return True
            # Saiu do captcha mas não é a listagem — checa a tabela
            try:
                cont = self._find_listing_container()
                if cont is not None and self._find_rows(cont):
                    self._log("     ▶ Captcha resolvido — retomando.",
                              "ok")
                    return True
            except Exception:
                pass
        self._log("     ⚠ Tempo esgotado aguardando o captcha (5 min). "
                  "Encerrando — rode novamente para continuar de onde "
                  "parou.", "warn")
        self._cancel_event.set()
        return False

    def _click_to_open_detail(self, row) -> bool:
        """Clica no ícone do OLHO (visualizar) na linha da DCTFWeb.

        Pelos prints da DOM real, a coluna 'Serviços' tem 4 ícones:
        👁 olho (visualizar) | 📊 gráfico | 📄 recibo | 📋 documento
        O OLHO é o que abre a tela de detalhe — sempre o 1º da coluna.

        Estratégias em ordem de robustez:
          1. Ícone (img/i/button) com title/alt/class contendo
             'visualiz', 'olho', 'eye', 'fa-eye'
          2. Primeiro ícone clicável da última coluna (coluna 'Serviços')
          3. Fallback: link/botão da linha (último recurso)
        """
        # 1) Procura por title/alt/class sugestivos do "olho"
        try:
            for el in row.find_elements(
                    By.XPATH, ".//i | .//img | .//button | .//a | "
                    ".//*[@onclick]"):
                attrs = " ".join([
                    el.get_attribute("title") or "",
                    el.get_attribute("alt") or "",
                    el.get_attribute("aria-label") or "",
                    el.get_attribute("class") or "",
                    (el.get_attribute("src") or "").split("/")[-1],
                ]).lower()
                # Sinais fortes do ícone do olho:
                if any(kw in attrs for kw in (
                        "visualiz", "fa-eye", "icon-view", "icon-eye",
                        "icone-olho", "olho", "ver-declaracao",
                        "visualizar declaração", "visualizar declaracao")):
                    # Penalização: pular se for de outro ícone óbvio
                    if any(skip in attrs for skip in (
                            "grafico", "gráfico", "chart", "extrato",
                            "recibo", "documento", "anexo",
                            "imprim", "cancel")):
                        continue
                    if self._click_element(el):
                        return True
        except Exception:
            pass

        # 2) Primeiro elemento clicável da última td (coluna Serviços)
        try:
            tds = row.find_elements(By.XPATH, "./td")
            if tds:
                last = tds[-1]
                clickables = last.find_elements(
                    By.XPATH, ".//a | .//button | .//img | "
                    ".//input[@type='image'] | .//*[@onclick]")
                if clickables:
                    if self._click_element(clickables[0]):
                        return True
        except Exception:
            pass

        # 3) Texto explícito (fallback se houver botão de texto)
        lower_xpath = (
            "translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZÁÉÍÓÚÂÊÔÃÕÇ', "
            "'abcdefghijklmnopqrstuvwxyzaeiouaeoaoc')"
        )
        for kw in ("visualizar", "detalhar", "detalhes", "abrir"):
            try:
                els = row.find_elements(
                    By.XPATH,
                    f".//a[contains({lower_xpath}, '{kw}')] | "
                    f".//button[contains({lower_xpath}, '{kw}')]")
                for el in els:
                    if el.is_displayed() and el.is_enabled():
                        if self._click_element(el):
                            return True
            except Exception:
                continue

        return False

    def _click_voltar_button(self) -> bool:
        """Procura botão Voltar na tela de detalhe."""
        lower_xpath = (
            "translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZÁÉÍÓÚÂÊÔÃÕÇ', "
            "'abcdefghijklmnopqrstuvwxyzaeiouaeoaoc')"
        )
        try:
            for kw in ("voltar", "cancelar", "fechar"):
                els = self._driver.find_elements(
                    By.XPATH,
                    f"//a[contains({lower_xpath}, '{kw}')] | "
                    f"//button[contains({lower_xpath}, '{kw}')]")
                for el in els:
                    if el.is_displayed() and el.is_enabled():
                        if self._click_element(el):
                            return True
        except Exception:
            pass
        return False

    def _log_row_inspection(self, row, idx_label: str = "") -> None:
        """Diagnóstico (1x) — lista todos os clicáveis de uma linha da
        listagem, com seus atributos. Ajuda a identificar onde está o
        ícone do olho (visualizar).
        """
        try:
            self._log(f"   ── DIAGNÓSTICO da linha {idx_label} ──", "warn")
            try:
                tag = row.tag_name
                row_class = (row.get_attribute("class") or "")[:60]
                row_text = (row.text or "").strip()[:150]
                self._log(f"   Tag: <{tag}> class={row_class!r}", "info")
                self._log(f"   Texto: {row_text!r}", "info")
            except Exception:
                pass

            tds = row.find_elements(By.TAG_NAME, "td")
            direct_tds = row.find_elements(By.XPATH, "./td")
            self._log(f"   <td> descendentes={len(tds)}  diretos="
                      f"{len(direct_tds)}", "info")

            for ti, td in enumerate(direct_tds or tds):
                clickables = td.find_elements(
                    By.XPATH, ".//a | .//button | .//img | .//i | "
                    ".//input[@type='image'] | .//*[@onclick]")
                td_text = (td.text or "").strip()[:50]
                if clickables or td_text:
                    self._log(f"   td[{ti}] text={td_text!r} "
                              f"clicáveis={len(clickables)}", "info")
                for ci, el in enumerate(clickables[:6]):
                    try:
                        attrs = {
                            "tag": el.tag_name,
                            "alt": (el.get_attribute("alt") or "")[:30],
                            "title": (el.get_attribute("title") or "")[:30],
                            "src": (el.get_attribute("src") or "").split("/")[-1][:40],
                            "class": (el.get_attribute("class") or "")[:50],
                            "id": (el.get_attribute("id") or "")[:40],
                            "onclick": (el.get_attribute("onclick") or "")[:50],
                            "href": (el.get_attribute("href") or "")[:50],
                            "aria-label": (el.get_attribute("aria-label") or "")[:30],
                        }
                        non_empty = {k: v for k, v in attrs.items() if v}
                        self._log(f"     [{ci}] {non_empty}", "info")
                    except Exception:
                        continue
        except Exception as e:
            self._log(f"   (falha no diagnóstico da linha: {e})", "warn")

    def _log_detail_inspection(self) -> None:
        """Lista TODOS os botões/links da tela de detalhe (debug 1x)."""
        try:
            self._log("   ── DIAGNÓSTICO da tela de detalhe ──", "warn")
            clickables = self._driver.find_elements(
                By.XPATH, "//a | //button | //input[@type='button' or "
                "@type='submit' or @type='image']")
            self._log(f"   Total de clicáveis: {len(clickables)}", "info")
            shown = 0
            for el in clickables:
                if shown >= 25:
                    break
                try:
                    if not el.is_displayed():
                        continue
                    txt = (el.text or "").strip()[:40]
                    title = (el.get_attribute("title") or "")[:30]
                    cls = (el.get_attribute("class") or "")[:30]
                    if txt or title:
                        self._log(f"     [{shown}] {el.tag_name!r} "
                                  f"text={txt!r} title={title!r} "
                                  f"class={cls!r}", "info")
                        shown += 1
                except Exception:
                    continue
        except Exception:
            pass

    def _log_dropdown_inspection(self) -> None:
        """Lista itens visíveis após abrir o dropdown Relatórios (debug 1x)."""
        try:
            self._log("   ── DIAGNÓSTICO do dropdown Relatórios ──", "warn")
            # Procura .dropdown-menu visível
            menus_found = False
            for sel in [".dropdown-menu", "ul.dropdown-menu",
                        "[class*='dropdown'][class*='menu']"]:
                try:
                    menus = self._driver.find_elements(By.CSS_SELECTOR, sel)
                    for menu in menus:
                        if not menu.is_displayed():
                            continue
                        menus_found = True
                        items = menu.find_elements(
                            By.XPATH, ".//a | .//button | .//li")
                        self._log(f"   Menu visível com {len(items)} item(ns):",
                                  "info")
                        for ii, it in enumerate(items[:15]):
                            try:
                                if not it.is_displayed():
                                    continue
                                txt = (it.text or "").strip()[:60]
                                title = (it.get_attribute("title") or "")[:30]
                                if txt or title:
                                    self._log(f"     [{ii}] text={txt!r} "
                                              f"title={title!r}", "info")
                            except Exception:
                                continue
                except Exception:
                    continue
            if not menus_found:
                # Fallback: lista clicáveis com texto que sejam visíveis
                self._log("   (menu não localizado — listando clicáveis "
                          "visíveis)", "info")
                clickables = self._driver.find_elements(
                    By.XPATH, "//a | //button")
                shown = 0
                for el in clickables:
                    if shown >= 25:
                        break
                    try:
                        if not el.is_displayed():
                            continue
                        txt = (el.text or "").strip()[:60]
                        if not txt:
                            continue
                        self._log(f"     {txt!r}", "info")
                        shown += 1
                    except Exception:
                        continue
        except Exception as e:
            self._log(f"   (falha no diagnóstico dropdown: {e})", "warn")

    def _open_relatorios_dropdown(self) -> bool:
        """Abre o dropdown 'Relatórios' na tela de detalhe da DCTFWeb.

        Pelo diagnóstico real:
          [4] 'a' text='Relatórios' title='' class='dropdown-toggle'

        Estratégia: lê os atributos via Python (não XPath translate, que
        não normaliza acentos corretamente) — filtra por texto contendo
        'relatório' ou 'relatorio' depois de normalizar.
        """
        time.sleep(0.4)

        def _normalize(s: str) -> str:
            """Remove acentos e lowercase pra comparar texto."""
            try:
                n = unicodedata.normalize("NFKD", s)
                n = "".join(c for c in n if not unicodedata.combining(c))
                return n.lower().strip()
            except Exception:
                return (s or "").lower().strip()

        # 1) Procura todos os elementos com class='dropdown-toggle'
        try:
            candidates = self._driver.find_elements(
                By.CSS_SELECTOR, ".dropdown-toggle, [class*='dropdown-toggle']")
            for el in candidates:
                try:
                    if not el.is_displayed():
                        continue
                    txt = _normalize(el.text or "")
                    if "relatorio" in txt:
                        if self._click_element(el):
                            time.sleep(0.8)
                            if self._verify_dropdown_open(el):
                                return True
                            # Toggle pode ter fechado num race — tenta de novo
                            time.sleep(0.5)
                            if self._click_element(el):
                                time.sleep(0.8)
                                return True
                except Exception:
                    continue
        except Exception:
            pass

        # 2) Fallback amplo: qualquer <a>/<button> com texto 'Relatórios'
        try:
            for tag in ("a", "button"):
                els = self._driver.find_elements(By.TAG_NAME, tag)
                for el in els:
                    try:
                        if not el.is_displayed():
                            continue
                        txt = _normalize(el.text or "")
                        if "relatorio" in txt:
                            if self._click_element(el):
                                time.sleep(0.8)
                                return True
                    except Exception:
                        continue
        except Exception:
            pass

        return False

    def _verify_dropdown_open(self, dropdown_el) -> bool:
        """Confere se o dropdown está aberto (aria-expanded=true ou menu visível)."""
        try:
            aria = (dropdown_el.get_attribute("aria-expanded") or "").lower()
            if aria == "true":
                return True
        except Exception:
            pass
        # Procura .dropdown-menu visível em qualquer lugar
        try:
            menus = self._driver.find_elements(
                By.CSS_SELECTOR, ".dropdown-menu, ul.dropdown-menu")
            for menu in menus:
                try:
                    if menu.is_displayed():
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    def _download_subtipo(self, subtipo: str,
                          entry: _DownloadEntry) -> bool:
        """Baixa um subtipo (DEBITOS/CREDITOS/COMPLETA).

        Fluxo:
          1. Abre o dropdown 'Relatórios' (3 recibos ficam dentro dele)
          2. Procura item do menu com texto matching o subtipo
          3. Clica, aguarda navegação, captura PDF via CDP
        """
        keywords = self.SUBTYPE_KEYWORDS.get(subtipo, ())
        if not keywords:
            entry.status = "erro"
            entry.erro = f"Subtipo desconhecido: {subtipo}"
            return False

        # PASSO 1: abre o dropdown "Relatórios" pra revelar os itens
        if not self._open_relatorios_dropdown():
            entry.status = "erro"
            entry.erro = "Dropdown 'Relatórios' não encontrado na tela de detalhe"
            return False

        # Diagnóstico (1ª vez por execução): lista o que apareceu
        if not DctfWebDownloader._dropdown_diagnosed:
            self._log_dropdown_inspection()
            DctfWebDownloader._dropdown_diagnosed = True

        # PASSO 2: localiza item do dropdown com palavra-chave do subtipo
        button = self._find_button_by_text(keywords)
        if not button:
            entry.status = "erro"
            entry.erro = (f"Item '{subtipo}' não encontrado no dropdown "
                          f"Relatórios (palavras-chave: {keywords})")
            return False

        # Estado ANTES do clique
        handles_before = set(self._driver.window_handles)
        try:
            url_before = self._driver.execute_script(
                "return window.top.location.href")
        except Exception:
            url_before = ""

        # Garante que downloads do Chrome caiam na nossa pasta
        self._configure_download_dir()
        # Snapshot dos arquivos na pasta ANTES do clique (pra detectar
        # download direto)
        files_before = self._snapshot_dir_files()

        # Força navegação na MESMA aba caso o link tenha target='_blank'.
        try:
            self._driver.execute_script(
                "if (arguments[0].tagName === 'A') {"
                "  arguments[0].setAttribute('target', '_self');"
                "}", button)
        except Exception:
            pass

        if not self._click_element(button):
            entry.status = "erro"
            entry.erro = f"Falha ao clicar no item '{subtipo}'."
            return False

        # Aguarda: nova aba | URL muda | título muda | DOWNLOAD na pasta
        # TIMEOUT CURTO (12s): esperas longas esfriam a sessão do eCAC e
        # disparam o captcha. Quando o recibo funciona, responde em
        # poucos segundos. Se não respondeu em 12s, é falha — desistir
        # rápido preserva a sessão pras próximas DCTFWeb.
        deadline = time.time() + 12.0
        new_handle = None
        same_tab_navigated = False
        downloaded_file = None
        try:
            title_before = self._driver.title or ""
        except Exception:
            title_before = ""

        while time.time() < deadline:
            time.sleep(0.4)
            had_alert, alert_text, expired = self._check_and_handle_alert()
            if had_alert:
                if expired:
                    self._cancel_event.set()
                    entry.status = "erro"
                    entry.erro = "Sessão do eCAC expirada"
                    return False
                self._log(f"     ⚠ Alert: {alert_text!r}", "warn")
                entry.status = "erro"
                entry.erro = f"Alert ao baixar {subtipo}: {alert_text}"
                return False
            # 1) Nova aba?
            try:
                new_handles = set(self._driver.window_handles) - handles_before
                if new_handles:
                    new_handle = next(iter(new_handles))
                    break
            except Exception:
                pass
            # 2) URL mudou?
            try:
                cur = self._driver.execute_script(
                    "return window.top.location.href")
                if cur and cur != url_before:
                    same_tab_navigated = True
                    self._log(f"     URL mudou: {cur[:80]}", "info")
                    break
            except Exception:
                pass
            # 3) Título mudou?
            try:
                cur_title = self._driver.title or ""
                if cur_title and cur_title != title_before:
                    t_low = cur_title.lower()
                    if any(kw in t_low for kw in (
                            "recibo", "pdf", "comprovante", "declaração",
                            "declaracao", "relatório", "relatorio")):
                        same_tab_navigated = True
                        self._log(f"     Título mudou: {cur_title[:80]}",
                                  "info")
                        break
            except Exception:
                pass
            # 4) DOWNLOAD direto na pasta? (PDF caiu no disco)
            novo = self._detect_new_download(files_before)
            if novo:
                downloaded_file = novo
                self._log(f"     Download detectado: {novo.name}", "info")
                break

        # ── CASO D: download direto pra pasta ─────────────────────────
        if downloaded_file:
            try:
                # Preserva a extensão real do arquivo baixado
                # (XML de Saída vem como .xml; recibos vêm como .pdf)
                ext = downloaded_file.suffix or ".pdf"
                target = self.out_dir / (entry.safe_basename() + ext)
                if target.exists():
                    ts = time.strftime("%H%M%S")
                    target = self.out_dir / (
                        entry.safe_basename() + f"_{ts}{ext}")
                if downloaded_file.resolve() != target.resolve():
                    downloaded_file.rename(target)
                entry.arquivo = target.name
                entry.status = "baixado"
                size_kb = target.stat().st_size / 1024
                self._log(f"     ✓ salvo: {target.name} ({size_kb:.0f} KB)",
                          "ok")
                return True
            except Exception as e:
                entry.status = "erro"
                entry.erro = f"Erro ao mover download: {e}"
                return False

        # SEM 2ª tentativa: re-clicar adiciona +35s de espera morta, o
        # que esfria a sessão e dispara o captcha. Se não respondeu em
        # 12s, marca erro e segue — preserva a sessão pras próximas.
        if not new_handle and not same_tab_navigated:
            entry.status = "erro"
            entry.erro = (f"'{subtipo}' não respondeu em 12s — pulando "
                          f"pra preservar a sessão do eCAC")
            return False

        if False:  # bloco da 2ª tentativa desativado (mantido p/ histórico)
            files_before = self._snapshot_dir_files()
            if self._open_relatorios_dropdown():
                button2 = self._find_button_by_text(keywords)
                if button2:
                    try:
                        self._driver.execute_script(
                            "if (arguments[0].tagName === 'A') {"
                            "  arguments[0].setAttribute('target','_self');}",
                            button2)
                    except Exception:
                        pass
                    self._click_element(button2)
                    deadline2 = time.time() + 12.0
                    while time.time() < deadline2:
                        time.sleep(0.4)
                        had_alert, alert_text, expired = \
                            self._check_and_handle_alert()
                        if had_alert:
                            if expired:
                                self._cancel_event.set()
                                entry.status = "erro"
                                entry.erro = "Sessão do eCAC expirada"
                                return False
                            entry.status = "erro"
                            entry.erro = f"Alert: {alert_text}"
                            return False
                        try:
                            nh = set(self._driver.window_handles) \
                                - handles_before
                            if nh:
                                new_handle = next(iter(nh))
                                break
                        except Exception:
                            pass
                        try:
                            cur = self._driver.execute_script(
                                "return window.top.location.href")
                            if cur and cur != url_before:
                                same_tab_navigated = True
                                break
                        except Exception:
                            pass
                        novo = self._detect_new_download(files_before)
                        if novo:
                            downloaded_file = novo
                            break

        if downloaded_file:
            try:
                target = self.out_dir / (entry.safe_basename() + ".pdf")
                if target.exists():
                    ts = time.strftime("%H%M%S")
                    target = self.out_dir / (
                        entry.safe_basename() + f"_{ts}.pdf")
                if downloaded_file.resolve() != target.resolve():
                    downloaded_file.rename(target)
                entry.arquivo = target.name
                entry.status = "baixado"
                size_kb = target.stat().st_size / 1024
                self._log(f"     ✓ salvo: {target.name} ({size_kb:.0f} KB)",
                          "ok")
                return True
            except Exception as e:
                entry.status = "erro"
                entry.erro = f"Erro ao mover download: {e}"
                return False

        if not new_handle and not same_tab_navigated:
            entry.status = "erro"
            entry.erro = (f"Clique em '{subtipo}' não causou navegação "
                          f"nem download (2 tentativas, servidor lento?)")
            return False

        try:
            if new_handle:
                self._driver.switch_to.window(new_handle)
                time.sleep(1.5)

            # Aguarda render
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

            # Pode ter virado um download enquanto a aba abria
            novo = self._detect_new_download(files_before)
            if novo:
                ext = novo.suffix or ".pdf"
                target = self.out_dir / (entry.safe_basename() + ext)
                if target.exists():
                    ts = time.strftime("%H%M%S")
                    target = self.out_dir / (
                        entry.safe_basename() + f"_{ts}{ext}")
                try:
                    if novo.resolve() != target.resolve():
                        novo.rename(target)
                    entry.arquivo = target.name
                    entry.status = "baixado"
                    size_kb = target.stat().st_size / 1024
                    self._log(f"     ✓ salvo: {target.name} "
                              f"({size_kb:.0f} KB)", "ok")
                    return True
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
                self._log(f"     ✓ salvo: {target.name} ({size_kb:.0f} KB)",
                          "ok")
            else:
                entry.status = "erro"
                entry.erro = "Falha ao capturar PDF via CDP."
            return ok
        finally:
            # Fecha aba ou volta
            if new_handle:
                try:
                    self._driver.close()
                except Exception:
                    pass
                handles_now = self._driver.window_handles
                if handles_now:
                    try:
                        target_handle = None
                        for h in handles_now:
                            if h != new_handle:
                                target_handle = h
                                break
                        if target_handle:
                            self._driver.switch_to.window(target_handle)
                    except Exception:
                        pass
            elif same_tab_navigated:
                # Volta pro detalhe da DCTFWeb. Se cair no captcha,
                # volta de novo (o detalhe é onde está o dropdown
                # Relatórios — precisamos dele pro próximo subtipo).
                self._return_from_pdf_to_detail()

    def _return_from_pdf_to_detail(self) -> None:
        """Após capturar um PDF que navegou na MESMA aba, volta pra a
        tela de DETALHE da DCTFWeb com UM clique no Voltar.

        OBS: na maioria dos casos o recibo abre em NOVA ABA — aí esta
        função nem é chamada (a aba é só fechada). Ela só roda quando
        o recibo navegou na mesma aba."""
        try:
            self._driver.back()
        except Exception:
            pass
        time.sleep(1.5)
        # Detecta captcha
        try:
            url = (self._driver.current_url or "").lower()
        except Exception:
            url = ""
        if "captcha" in url:
            self._log("     ⚠ Verificação 'Sou humano' apareceu. "
                      "Resolva no Chrome para continuar.", "warn")
            self._wait_user_solves_captcha()

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
        """Verifica se um novo arquivo apareceu na pasta (download
        concluído). Ignora arquivos .crdownload (download em andamento).

        O 'Download XML de Saída' da DCTFWeb vem como arquivo .zip —
        por isso o .zip tem prioridade na detecção."""
        if not self.out_dir:
            return None
        try:
            now = {p.name for p in self.out_dir.iterdir() if p.is_file()}
            novos = now - files_before
            # Ignora downloads em andamento
            concluidos = [n for n in novos
                          if not n.endswith(".crdownload")
                          and not n.endswith(".tmp")
                          and not n.endswith(".part")]
            # Prioriza ZIP (XML de Saída vem zipado), depois XML, depois PDF
            zips = [n for n in concluidos if n.lower().endswith(".zip")]
            xmls = [n for n in concluidos if n.lower().endswith(".xml")]
            pdfs = [n for n in concluidos if n.lower().endswith(".pdf")]
            escolha = zips or xmls or pdfs or concluidos
            if escolha:
                # Pega o mais recente
                escolha.sort(
                    key=lambda n: (self.out_dir / n).stat().st_mtime,
                    reverse=True)
                return self.out_dir / escolha[0]
        except Exception:
            pass
        return None

    def _find_button_by_text(self, keywords: tuple):
        """Procura botão/link cujo texto/title/aria-label contenha alguma
        das palavras-chave (case-insensitive, tolerante a acentos).

        Usa filtragem Python pra normalizar texto — XPath translate() não
        normaliza acentos corretamente quando o texto da página já está
        em minúsculas (ex: 'relatórios' não vira 'relatorios').
        """
        def _normalize(s: str) -> str:
            try:
                n = unicodedata.normalize("NFKD", s or "")
                n = "".join(c for c in n if not unicodedata.combining(c))
                return n.lower().strip()
            except Exception:
                return (s or "").lower().strip()

        # Normaliza keywords (todas em minúsculo, sem acento)
        keywords_norm = [_normalize(kw) for kw in keywords if kw]

        # 1) Procura por texto visível em <a>, <button>, <li>, <span>
        for tag in ("a", "button", "li", "span"):
            try:
                els = self._driver.find_elements(By.TAG_NAME, tag)
                for el in els:
                    try:
                        if not el.is_displayed():
                            continue
                        # Pode estar dentro de dropdown que tem visibility
                        # condicional — confirma is_enabled também
                        if tag in ("a", "button") and not el.is_enabled():
                            continue
                        txt = _normalize(el.text or "")
                        if not txt:
                            continue
                        # Match: texto contém alguma keyword
                        for kw_norm in keywords_norm:
                            if kw_norm and kw_norm in txt:
                                return el
                    except Exception:
                        continue
            except Exception:
                continue

        # 2) Procura por title / aria-label / value (em qualquer elemento)
        try:
            els = self._driver.find_elements(
                By.CSS_SELECTOR, "[title], [aria-label], [value]")
            for el in els:
                try:
                    if not el.is_displayed():
                        continue
                    attrs_combined = _normalize(
                        " ".join([
                            el.get_attribute("title") or "",
                            el.get_attribute("aria-label") or "",
                            el.get_attribute("value") or "",
                        ])
                    )
                    for kw_norm in keywords_norm:
                        if kw_norm and kw_norm in attrs_combined:
                            return el
                except Exception:
                    continue
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
            self._log(f"     erro Page.printToPDF: {e}", "error")
            return False

    # ---- Paginação ------------------------------------------------------
    def _diagnose_pagination(self) -> str:
        """Gera um relatório dos elementos de paginação encontrados na
        página — ajuda a debugar quando o avanço de página falha."""
        linhas = ["  ── DIAGNÓSTICO da paginação ──"]
        try:
            # Lista <a> com texto numérico (prováveis links de página)
            num_links = []
            for a in self._driver.find_elements(By.TAG_NAME, "a"):
                try:
                    if not a.is_displayed():
                        continue
                    txt = (a.text or "").strip()
                    if txt.isdigit() and len(txt) <= 3:
                        href = (a.get_attribute("href") or "")[:50]
                        onclick = (a.get_attribute("onclick") or "")[:50]
                        num_links.append(
                            f"    nº {txt!r} href={href!r} "
                            f"onclick={onclick!r}")
                except Exception:
                    continue
            if num_links:
                linhas.append(f"  Links numéricos ({len(num_links)}):")
                linhas.extend(num_links[:12])
            else:
                linhas.append("  Nenhum link numérico encontrado.")

            # Lista <a> com texto de navegação
            nav_links = []
            for a in self._driver.find_elements(By.TAG_NAME, "a"):
                try:
                    if not a.is_displayed():
                        continue
                    txt = (a.text or "").strip()
                    tl = txt.lower()
                    if tl in ("próxima", "proxima", "próximo", "proximo",
                              ">", ">>", "»", "anterior", "<", "<<"):
                        linhas.append(f"  Link navegação: {txt!r}")
                        nav_links.append(txt)
                except Exception:
                    continue
            if not nav_links:
                linhas.append("  Nenhum link 'Próxima/>>' encontrado.")
        except Exception as e:
            linhas.append(f"  Erro no diagnóstico: {e}")
        return "\n".join(linhas)

    def _go_to_next_page(self) -> bool:
        """Avança pra próxima página da listagem de DCTFWeb.

        A DCTFWeb é ASP.NET WebForms — paginação por links numerados
        (1 2 3 ...) que disparam __doPostBack.

        SEGURANÇA: o downloader rastreia o número da página atual em
        self._dctfweb_page_num. Só avança se existir um link para
        (página_atual + 1). NUNCA usa fallback "chuta o número 2" —
        isso causava loop infinito reprocessando páginas.
        """
        def _norm(s):
            try:
                n = unicodedata.normalize("NFKD", s or "")
                return "".join(c for c in n
                                if not unicodedata.combining(c)).lower()
            except Exception:
                return (s or "").lower()

        # Página que estamos AGORA (controlada pelo downloader)
        pagina_atual = getattr(self, "_dctfweb_page_num", 1)
        proxima = pagina_atual + 1

        # Assinatura do conteúdo atual da tabela (pra confirmar a virada)
        def _assinatura_tabela() -> str:
            try:
                c = self._find_listing_container()
                if not c:
                    return ""
                rows = self._find_rows(c)
                # Junta o texto das 3 primeiras linhas
                amostra = []
                for r in rows[:3]:
                    try:
                        amostra.append((r.text or "")[:60])
                    except Exception:
                        pass
                return " | ".join(amostra)
            except Exception:
                return ""

        assinatura_antes = _assinatura_tabela()

        # ── Coleta links numéricos de paginação (postback) ──
        num_links = {}
        try:
            for a in self._driver.find_elements(By.TAG_NAME, "a"):
                try:
                    if not a.is_displayed():
                        continue
                    txt = (a.text or "").strip()
                    if txt.isdigit() and len(txt) <= 3:
                        href = a.get_attribute("href") or ""
                        onclick = a.get_attribute("onclick") or ""
                        if "doPostBack" in href or "doPostBack" in onclick \
                           or "Page$" in href:
                            num_links[int(txt)] = a
                except Exception:
                    continue
        except Exception:
            pass

        nxt = None

        # ── Estratégia 1: clica no link da PRÓXIMA página exata ──
        # Só avança se existir link para (pagina_atual + 1).
        if proxima in num_links:
            nxt = num_links[proxima]
            self._log(f"  → avançando para página {proxima}...", "info")
        else:
            # A próxima página pode estar num "bloco" seguinte de
            # paginação (ex: GridView mostra 1-10, depois '...').
            # Procura link "Próxima"/">>" APENAS se ele não estiver
            # desabilitado. Se não houver, é a última página.
            try:
                for a in self._driver.find_elements(By.TAG_NAME, "a"):
                    try:
                        if not (a.is_displayed() and a.is_enabled()):
                            continue
                        txt = _norm(a.text or "").strip()
                        if txt in ("proxima", "proximo", ">", ">>", "»",
                                   "..."):
                            href = a.get_attribute("href") or ""
                            onclick = a.get_attribute("onclick") or ""
                            if "doPostBack" in href \
                               or "doPostBack" in onclick:
                                nxt = a
                                self._log(f"  → avançando via "
                                          f"'{a.text.strip()}'...", "info")
                                break
                    except Exception:
                        continue
            except Exception:
                pass

        # Sem link pra próxima página → acabou
        if not nxt:
            return False

        # Clica
        try:
            self._driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", nxt)
            time.sleep(0.3)
            nxt.click()
        except Exception:
            try:
                self._driver.execute_script("arguments[0].click();", nxt)
            except Exception:
                return False

        # Aguarda a virada — o conteúdo da tabela TEM que mudar
        deadline = time.time() + _DL_PAGE_CHANGE_TIMEOUT
        while time.time() < deadline:
            if self._cancel_event.is_set():
                return False
            time.sleep(0.5)
            # Captcha?
            try:
                url = (self._driver.current_url or "").lower()
                if "captcha" in url:
                    self._log("     ⚠ Captcha ao trocar de página. "
                              "Resolva no Chrome.", "warn")
                    if not self._wait_user_solves_captcha():
                        return False
            except Exception:
                pass
            assinatura_depois = _assinatura_tabela()
            if assinatura_depois and \
               assinatura_depois != assinatura_antes:
                # Virou de página de verdade
                self._dctfweb_page_num = proxima
                return True

        # Timeout — o conteúdo não mudou. NÃO conta como virada
        # (evita reprocessar a mesma página em loop).
        self._log("     ⚠ A página não mudou após o clique — "
                  "encerrando a paginação.", "warn")
        return False


# ---- Sub-aba: PERDCOMP --------------------------------------------------------
