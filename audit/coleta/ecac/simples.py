"""Robô e-CAC: download do módulo Simples Nacional (PGDAS-D/DAS/extratos) (roda em thread, callbacks injetados — sem Tkinter).

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


class SimplesNacionalDownloader:
    """Baixa o Extrato da apuração do PGDAS-D (Simples Nacional) do eCAC.

    ETAPA 1 — captura conservadora:
      Salva o Extrato que estiver ABERTO no Chrome (no visualizador de
      PDF), organizando-o pela pasta do CNPJ. Isso valida toda a
      tubulação — captura dos bytes originais do PDF, leitura do CNPJ
      de dentro do documento, roteamento de pastas — sem depender de
      conhecer a navegação interna do PGDAS-D.

    ETAPA 2 (futura) — iteração automática:
      Percorrer as competências de um intervalo de anos dentro do
      serviço 'PGDAS-D 2018 → Declarações Transmitidas', gerando e
      baixando o Extrato de cada uma. O esqueleto desta classe (threads,
      cancelamento, callbacks, pastas, log) já está pronto para receber
      esse laço.
    """

    # Competência no corpo do Extrato: "Período de Apuração (PA): MM/AAAA"
    _RE_COMPETENCIA = re.compile(
        r"(?:per[ií]odo\s+de\s+apura[cç][aã]o|compet[eê]ncia)"
        r"[^\d]{0,20}(\d{2})\s*/\s*(\d{4})",
        re.IGNORECASE)
    _RE_MMYYYY = re.compile(r"\b(\d{2})/(\d{4})\b")

    def __init__(self,
                 debug_port: int = 9222,
                 ano_inicial: Optional[int] = None,
                 ano_final: Optional[int] = None,
                 on_log: Callable[[str, str], None] = None,
                 on_progress: Callable[[dict], None] = None,
                 on_finished: Callable[[bool, dict], None] = None):
        self.debug_port = debug_port
        self.ano_inicial = ano_inicial
        self.ano_final = ano_final
        self.on_log = on_log or (lambda *a, **k: None)
        self.on_progress = on_progress or (lambda *a, **k: None)
        self.on_finished = on_finished or (lambda *a, **k: None)
        self._cancel_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._driver = None
        self.out_dir: Optional[Path] = None
        self._stats = {"baixados": 0, "erros": 0, "pulados": 0,
                       "ano_atual": 0, "competencia_atual": ""}
        self._downloaded_files: List[Path] = []
        self._cnpj: str = ""
        self._empresa: str = ""

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
        self._log("Cancelamento solicitado...", "warn")

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def get_downloaded_files(self) -> List[Path]:
        return list(self._downloaded_files)

    def get_cnpj(self) -> str:
        return self._cnpj

    # -------- Internos --------
    def _log(self, msg: str, level: str = "info") -> None:
        self.on_log(msg, level)

    def _emit_progress(self) -> None:
        self.on_progress(dict(self._stats))

    def _attach_to_chrome(self):
        options = SelOptions()
        options.add_experimental_option("debuggerAddress",
                                        f"localhost:{self.debug_port}")
        return webdriver.Chrome(options=options)

    # ---- captura de PDF (mesma técnica do DarfDownloader) ---------------
    def _tab_pdf_url(self):
        """Se a aba atual exibe um PDF, devolve a URL real ('https://...'
        ou 'blob:...'). Devolve None para páginas HTML comuns."""
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
        for cand in (href, embed_src):
            low = (cand or "").lower().split("?")[0]
            if cand and ((cand or "").lower().startswith("blob:")
                         or low.endswith(".pdf")):
                return cand
        return None

    def _fetch_pdf_bytes(self, pdf_url):
        """Baixa os bytes ORIGINAIS do PDF via fetch na própria aba."""
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
            self._log(f"    fetch do PDF não retornou o arquivo: "
                      f"{resultado}", "warn")
            return None
        try:
            import base64 as _b64
            return _b64.b64decode(str(resultado).split(",", 1)[1])
        except Exception:
            return None

    def _save_debug_snapshot(self, tag: str) -> None:
        """Salva HTML + screenshot da aba atual, pra calibrar seletores
        quando a navegacao falha."""
        try:
            target = self.out_dir or _dl_get_root_dir()
            target.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            (target / f"_debug_simples_{tag}_{ts}.html").write_text(
                self._driver.page_source, encoding="utf-8")
            try:
                self._driver.save_screenshot(
                    str(target / f"_debug_simples_{tag}_{ts}.png"))
            except Exception:
                pass
            self._log(f"   Snapshot de debug salvo: "
                      f"_debug_simples_{tag}_{ts}.html", "warn")
        except Exception:
            pass

    def _enter_pgdasd_frame(self) -> bool:
        """O PGDAS-D roda dentro de um <iframe> (id='frmApp'). Esta
        funcao coloca o driver DENTRO desse iframe — onde estao o campo
        'Ano-calendario', o botao 'Consultar' e as tabelas de PA.
        Deve ser chamada sempre que se volta para a aba do PGDAS-D.
        Retorna True se entrou no app (ou se ele ja esta no nivel raiz)."""
        try:
            self._driver.switch_to.default_content()
        except Exception:
            pass

        def _tem_tela(depth):
            """Verifica se o contexto atual tem a tela 'Consultar
            Declaracoes'; se nao, desce nos iframes ate depth niveis."""
            try:
                txt = self._driver.execute_script(
                    "return (document.body&&document.body.innerText)||'';"
                ) or ""
            except Exception:
                txt = ""
            low = txt.lower()
            if "ano-calend" in low or "consultar declara" in low \
                    or re.search(r"pa\s*\d{2}\s*/\s*\d{4}", low):
                return True
            if depth <= 0:
                return False
            try:
                frames = (self._driver.find_elements(By.TAG_NAME, "iframe")
                          + self._driver.find_elements(
                              By.TAG_NAME, "frame"))
            except Exception:
                frames = []
            for fr in frames:
                # ignora iframes de captcha/terceiros
                try:
                    src = (fr.get_attribute("src") or "").lower()
                except Exception:
                    src = ""
                if "hcaptcha" in src or "recaptcha" in src \
                        or "google" in src:
                    continue
                try:
                    self._driver.switch_to.frame(fr)
                except Exception:
                    continue
                if _tem_tela(depth - 1):
                    return True
                try:
                    self._driver.switch_to.parent_frame()
                except Exception:
                    try:
                        self._driver.switch_to.default_content()
                    except Exception:
                        pass
            return False

        return _tem_tela(3)

    def _switch_to_pgdasd_context(self) -> bool:
        """Localiza a aba do PGDAS-D e entra no iframe da aplicacao
        ('Consultar Declaracoes'). Deixa o driver posicionado dentro
        do iframe. Retorna True se encontrou."""
        for handle in self._driver.window_handles:
            try:
                self._driver.switch_to.window(handle)
            except Exception:
                continue
            try:
                url = (self._driver.current_url or "").lower()
            except Exception:
                continue
            if "receita.fazenda.gov.br" not in url:
                continue
            if self._enter_pgdasd_frame():
                return True
        return False

    def _detect_competencia(self, pdf_path: Path) -> str:
        """Le a competencia (AAAA.MM) de dentro do Extrato. Usa extracao
        de texto e cai para OCR via o leitor compartilhado se preciso.
        Devolve 'AAAA.MM' ou '' se nao encontrar."""
        texto = ""
        try:
            import pdfplumber
            with pdfplumber.open(str(pdf_path)) as pdf:
                partes = []
                for pg in pdf.pages[:2]:
                    partes.append(pg.extract_text() or "")
                texto = "\n".join(partes)
        except Exception:
            texto = ""
        m = self._RE_COMPETENCIA.search(texto or "")
        if not m:
            m = self._RE_MMYYYY.search(texto or "")
        if m:
            mes, ano = m.group(1), m.group(2)
            return f"{ano}.{mes}"
        return ""

    def _consultar_ano(self, ano: int) -> bool:
        """Consulta um ano-calendario no PGDAS-D.

        Preenche o campo #ano (name='anoDigitado') e SUBMETE o
        formulario de consulta. Em seguida espera ate o proprio
        PGDAS-D confirmar a troca — o <span id='helpBlock'> exibe
        'Exibindo dados das declaracoes de AAAA'. So retorna True
        quando esse texto bate com o ano pedido (evita ler a tela do
        ano anterior achando que recarregou).
        """
        if not self._enter_pgdasd_frame():
            self._log("    nao consegui entrar no iframe do PGDAS-D.",
                      "warn")
            return False
        js = r"""
            var ano = arguments[0];
            // 1) campo do ano: id='ano' / name='anoDigitado'.
            var inp = document.querySelector(
                "#ano, input[name='anoDigitado'], input.ano");
            if(!inp){
                // fallback: input de texto cujo rotulo/placeholder
                // mencione 'ano' (NUNCA o de localizar servico).
                var ins=document.querySelectorAll("input[type=text]");
                for(var i=0;i<ins.length;i++){
                    var hay=((ins[i].id||'')+' '+(ins[i].name||'')+' '
                        +(ins[i].placeholder||'')).toLowerCase();
                    if((hay.indexOf('ano')>=0 || /ex:?\s*20/.test(hay))
                       && hay.indexOf('pesquis')<0
                       && hay.indexOf('localizar')<0){
                        inp=ins[i]; break;
                    }
                }
            }
            if(!inp){ return 'ERRO:campo do ano nao encontrado'; }
            inp.focus(); inp.value=''+ano;
            inp.dispatchEvent(new Event('input',{bubbles:true}));
            inp.dispatchEvent(new Event('change',{bubbles:true}));

            // 2) submete o FORMULARIO que contem esse campo (POST real
            //    para .../Consulta) — mais confiavel que clicar.
            var form=inp.form;
            if(!form){
                var p=inp;
                for(var u=0;u<8&&p;u++){
                    if(p.tagName==='FORM'){ form=p; break; }
                    p=p.parentElement;
                }
            }
            if(form){
                // dispara o submit do framework, com fallback nativo
                try{
                    var btn=form.querySelector(
                        "button[type=submit],input[type=submit]");
                    if(btn){ btn.click(); }
                    else { form.submit(); }
                }catch(e){ try{ form.submit(); }catch(e2){} }
                return 'OK';
            }
            // sem form: clica no botao Consultar como ultimo recurso
            var b=null, cs=document.querySelectorAll('button,a');
            for(var k=0;k<cs.length;k++){
                var t=((cs[k].innerText||cs[k].textContent||'')
                    .replace(/[\uE000-\uF8FF]/g,' ')).toLowerCase();
                if(t.indexOf('consultar')>=0){ b=cs[k]; break; }
            }
            if(b){ b.click(); return 'OK'; }
            return 'ERRO:formulario de consulta nao encontrado';
        """
        try:
            r = self._driver.execute_script(js, str(ano))
        except Exception as e:
            self._log(f"    erro ao consultar o ano: {e}", "warn")
            return False
        if r != "OK":
            self._log(f"    {r}", "warn")
            return False

        # espera o PGDAS-D CONFIRMAR a troca de ano. O <span
        # id='helpBlock'> mostra "Exibindo dados das declaracoes de
        # AAAA" — so aceitamos quando AAAA == ano pedido.
        alvo = str(ano)
        deadline = time.time() + _DL_WAIT_TIMEOUT
        while time.time() < deadline:
            if self._cancel_event.is_set():
                return False
            time.sleep(0.7)
            if not self._enter_pgdasd_frame():
                continue
            try:
                info = self._driver.execute_script(
                    "var h=document.querySelector('#helpBlock');"
                    "var ht=h?h.textContent:'';"
                    "var bt=(document.body&&document.body.innerText)"
                    "||'';"
                    "return ht+String.fromCharCode(10)+bt;") or ""
            except Exception:
                info = ""
            mhelp = re.search(r"declara[cç][oõ]es?\s+de\s+(\d{4})",
                              info, re.IGNORECASE)
            if mhelp:
                if mhelp.group(1) == alvo:
                    return True
                # ainda mostra o ano anterior — segue esperando
                continue
            low = info.lower()
            if ("nenhuma declara" in low or "sem declara" in low
                    or "nao foram encontrad" in low
                    or "n\u00e3o foram encontrad" in low):
                # ano sem declaracoes — consulta concluiu mesmo assim
                return True
        self._log(f"    \u26a0 o PGDAS-D nao confirmou a troca para "
                  f"{ano} no tempo esperado.", "warn")
        return False

    # JS: localiza, para cada PA, o controle "Declaracao" da linha
    # "Declaracao Original". Marca cada um com data-agritax-extrato e
    # devolve [{mes:'MM', idx:N}, ...]. O MES vem do cabecalho
    # "PA MM/AAAA"; o ANO e o ano-calendario consultado (lado Python).
    _JS_COLETA = r"""
        var out=[];
        var idx=0;
        document.querySelectorAll('[data-agritax-extrato]').forEach(
            function(e){ e.removeAttribute('data-agritax-extrato'); });

        // No PGDAS-D, o link do Extrato e:
        //   <a href=".../pgdasd2018.app/Consulta/Declaracao?
        //            idDeclaracao=CCCCCCCCAAAAMMSSS&ano=AAAA">
        // O link de Recibo usa .../Consulta/Recibo?... — descartado.
        // A competencia (mes) vem do proprio idDeclaracao:
        //   8 digitos CNPJ + 4 ano + 2 mes + 3 sequencial.
        var links=document.querySelectorAll('a[href]');
        for(var i=0;i<links.length;i++){
            var a=links[i];
            var href=(a.getAttribute('href')||'');
            var low=href.toLowerCase();
            // tem que ser o link de Declaracao (e nao o de Recibo)
            if(low.indexOf('/consulta/declaracao')<0) continue;
            if(low.indexOf('/consulta/recibo')>=0) continue;

            var mes='', ano='';
            var mId=href.match(/iddeclaracao=(\d{8})(\d{4})(\d{2})/i);
            if(mId){ ano=mId[2]; mes=mId[3]; }
            if(!mes){
                var mAno=href.match(/[?&]ano=(\d{4})/i);
                if(mAno){ ano=mAno[1]; }
            }
            a.setAttribute('data-agritax-extrato',''+idx);
            out.push({mes:mes, ano:ano, idx:idx});
            idx++;
        }
        return out;
    """

    def _collect_declaracoes(self) -> list:
        """Marca, na pagina do PGDAS-D, o botao 'Declaracao' de cada PA
        e devolve [{competencia, idx}, ...]. Cada idx aponta para um
        elemento marcado com data-agritax-extrato."""
        if not self._enter_pgdasd_frame():
            self._log("    nao consegui entrar no iframe do PGDAS-D.",
                      "warn")
            return []
        # rola a pagina inteira antes de coletar — a lista de PAs e
        # longa e a coleta precisa que todos os blocos estejam
        # renderizados (alguns frameworks so renderizam ao rolar).
        try:
            for _ in range(8):
                self._driver.execute_script(
                    "window.scrollBy(0, document.body.scrollHeight);")
                time.sleep(0.25)
            self._driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(0.2)
        except Exception:
            pass
        try:
            lista = self._driver.execute_script(self._JS_COLETA) or []
        except Exception as e:
            self._log(f"    erro ao coletar declaracoes: {e}", "warn")
            return []
        # diagnostico util quando vier vazio
        if not lista:
            try:
                diag = self._driver.execute_script(
                    "var pa=(document.body.innerText||'')"
                    ".match(/PA\\s*\\d{2}\\s*\\/\\s*\\d{4}/g);"
                    "var pr=document.querySelectorAll('img,a,button,input');"
                    "var n=0;"
                    "for(var i=0;i<pr.length;i++){"
                    "var h=((pr[i].getAttribute('src')||'')+' '"
                    "+(pr[i].getAttribute('onclick')||'')+' '"
                    "+(pr[i].getAttribute('title')||'')).toLowerCase();"
                    "if(/print|imprim|pdf|declarac|extrato|recibo/"
                    ".test(h)){n++;}}"
                    "return 'PAs='+(pa?pa.length:0)+' print_cands='+n;")
                self._log(f"    diagnostico: {diag}", "warn")
            except Exception:
                pass
        return lista

    def _configure_download_dir(self) -> None:
        """Forca o Chrome a baixar arquivos direto na pasta simples/
        (sem perguntar). Usa CDP — funciona na sessao ja aberta."""
        if not self.out_dir:
            return
        payload = {"behavior": "allow",
                   "downloadPath": str(self.out_dir),
                   "eventsEnabled": True}
        for cmd in ("Browser.setDownloadBehavior",
                    "Page.setDownloadBehavior"):
            try:
                self._driver.execute_cdp_cmd(cmd, payload)
                return
            except Exception:
                continue

    def _arquivos_pdf_na_pasta(self) -> set:
        """Conjunto dos PDFs atualmente na pasta de saida."""
        try:
            return {p.name for p in self.out_dir.glob("*.pdf")}
        except Exception:
            return set()

    def _esperar_download(self, antes: set, timeout: int = 60):
        """Espera surgir um PDF novo na pasta e o download terminar
        (sem .crdownload). Devolve o Path do arquivo novo ou None."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._cancel_event.is_set():
                return None
            time.sleep(0.7)
            # download em andamento?
            try:
                baixando = any(self.out_dir.glob("*.crdownload"))
            except Exception:
                baixando = False
            if baixando:
                continue
            agora = self._arquivos_pdf_na_pasta()
            novos = agora - antes
            if novos:
                # pega o mais recente e confirma tamanho estavel
                novo = max((self.out_dir / n for n in novos),
                           key=lambda p: p.stat().st_mtime)
                t1 = novo.stat().st_size
                time.sleep(1.0)
                if novo.exists() and novo.stat().st_size == t1 \
                        and t1 > 0:
                    return novo
        return None

    def _capturar_extrato(self, idx: int):
        """Clica no botao 'Declaracao' marcado com data-agritax-extrato=idx.
        O PGDAS-D baixa o Extrato direto na pasta simples/ (configurada
        via CDP). Devolve o Path do PDF baixado, ou None."""
        # o botao foi marcado DENTRO do iframe do PGDAS-D
        if not self._enter_pgdasd_frame():
            self._log("    nao consegui entrar no iframe do PGDAS-D.",
                      "warn")
            return None
        try:
            el = self._driver.find_element(
                By.CSS_SELECTOR, f"[data-agritax-extrato='{idx}']")
        except Exception:
            self._log("    botao da declaracao nao encontrado.", "warn")
            return None

        antes = self._arquivos_pdf_na_pasta()
        try:
            self._driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", el)
        except Exception:
            pass
        try:
            el.click()
        except Exception:
            try:
                self._driver.execute_script("arguments[0].click();", el)
            except Exception:
                self._log("    nao consegui clicar no botao.", "warn")
                return None

        novo = self._esperar_download(antes, timeout=60)
        if not novo:
            self._log("    o Extrato nao terminou de baixar a tempo.",
                      "warn")
            return None
        # valida que e um PDF de verdade
        try:
            with open(novo, "rb") as fh:
                if fh.read(5) != b"%PDF-":
                    self._log("    o arquivo baixado nao e um PDF "
                              "valido.", "warn")
                    return None
        except Exception:
            return None
        return novo

    def _capturar_extrato_por_mes(self, mes: str):
        """Captura o Extrato de um mes (MM) sem reconsultar o ano.

        Refaz a coleta na tela atual — que pode ter recarregado apos o
        download anterior — para reidentificar o botao 'Declaracao'
        daquele PA, e entao clica nele. Assim o ano-calendario e
        consultado UMA unica vez por ano. Devolve o Path do PDF
        baixado, ou None."""
        decls = self._collect_declaracoes()
        idx = None
        # um mes pode ter varias declaracoes (original + retificadoras).
        # Fica com a ULTIMA — a retificadora vigente.
        for d in decls:
            if (d.get("mes") or "").zfill(2) == str(mes).zfill(2):
                idx = d["idx"]
        if idx is None:
            self._log(f"    PA do mes {mes} nao encontrado na tela.",
                      "warn")
            return None
        return self._capturar_extrato(idx)

    def _run(self) -> None:
        success = False
        summary: Dict = {}
        log_data: Dict = {}
        log_path: Optional[Path] = None
        try:
            self._log(f"Conectando ao Chrome em "
                      f"localhost:{self.debug_port}...", "info")
            try:
                self._driver = self._attach_to_chrome()
            except WebDriverException as e:
                self._log(f"\u2717 Nao consegui conectar ao Chrome: {e}",
                          "error")
                summary["erro"] = "Chrome inacessivel"
                return
            self._log(f"\u2713 Conectado. Aba: {self._driver.title!r}", "ok")

            # localiza a aba do PGDAS-D
            self._log("Procurando a tela do PGDAS-D...", "info")
            if not self._switch_to_pgdasd_context():
                self._log("\u2717 Tela do PGDAS-D nao localizada. Abra o "
                          "'PGDAS-D 2018' no eCAC e acesse 'Consultar "
                          "Declaracoes'.", "error")
                summary["erro"] = "Tela PGDAS-D nao localizada"
                return
            self._log("\u2713 Tela do PGDAS-D localizada.", "ok")

            # detecta CNPJ
            self._log("Detectando CNPJ ativo...", "info")
            cnpj, nome = _dl_detect_cnpj_from_ecac(self._driver, self._log)
            if not cnpj:
                self._log("\u2717 CNPJ nao detectado na tela do PGDAS-D.",
                          "error")
                self._save_debug_snapshot("cnpj_nao_detectado")
                summary["erro"] = "CNPJ nao detectado"
                return
            self._cnpj = cnpj
            self._empresa = nome
            self._log(f"\u2713 CNPJ: {cnpj}"
                      + (f" \u2014 {nome}" if nome else ""), "ok")

            # pastas + log
            try:
                paths = _dl_ensure_company_dirs(cnpj)
            except Exception as e:
                self._log(f"\u2717 Erro ao criar pastas: {e}", "error")
                summary["erro"] = f"Erro pasta: {e}"
                return
            self.out_dir = paths["simples_dir"]
            log_path = paths["log_simples"]
            self._log(f"\u2713 Pasta: {self.out_dir}", "ok")
            if log_path.exists():
                try:
                    log_data = json.loads(
                        log_path.read_text(encoding="utf-8"))
                except Exception:
                    log_data = {}
            self._log(f"   Ja baixados anteriormente: {len(log_data)}",
                      "info")

            # intervalo de anos
            ano_ini = self.ano_inicial or int(time.strftime("%Y"))
            ano_fim = self.ano_final or ano_ini
            if ano_ini > ano_fim:
                ano_ini, ano_fim = ano_fim, ano_ini

            for ano in range(ano_ini, ano_fim + 1):
                if self._cancel_event.is_set():
                    break
                self._stats["ano_atual"] = ano
                self._emit_progress()
                self._log(f"\u2501\u2501\u2501 Ano-calendario {ano} "
                          f"\u2501\u2501\u2501", "info")

                if not self._consultar_ano(ano):
                    self._log(f"  \u26a0 Nao consegui consultar o ano "
                              f"{ano}; pulando.", "warn")
                    self._save_debug_snapshot(f"consulta_{ano}")
                    self._stats["erros"] += 1
                    continue

                # garante que os downloads do PGDAS-D caiam na pasta
                # simples/ (a captura depende disso)
                self._configure_download_dir()

                # coleta a lista de meses do ano (consulta feita UMA vez)
                decls = self._collect_declaracoes()
                if not decls:
                    # distingue 'ano sem declaracoes' (legitimo — ex.:
                    # empresa aberta depois) de uma falha de seletor.
                    tela_vazia = False
                    try:
                        txt = (self._driver.execute_script(
                            "return (document.body&&document.body."
                            "innerText)||'';") or "").lower()
                        tela_vazia = ("nenhuma declara" in txt
                                      or "nao foram encontrad" in txt
                                      or "n\u00e3o foram encontrad" in txt
                                      or "sem declara" in txt)
                    except Exception:
                        pass
                    if tela_vazia:
                        self._log(f"  Ano {ano} sem declarações no "
                                  f"PGDAS-D — pulando.", "info")
                    else:
                        self._log(f"  \u26a0 Nenhuma declaracao "
                                  f"localizada para {ano} (a tela pode "
                                  f"nao ter carregado).", "warn")
                        self._save_debug_snapshot(f"sem_decls_{ano}")
                    continue
                meses = []
                for d in decls:
                    mm = (d.get("mes") or "").zfill(2)
                    if mm and mm not in meses:
                        meses.append(mm)
                total = len(meses)
                self._log(f"  {total} declaracao(oes) encontrada(s).",
                          "info")

                # baixa competencia por competencia. O PGDAS-D recarrega
                # a tela a cada download, entao a coleta e refeita (sem
                # reconsultar o ano) para reidentificar o botao do mes.
                for i, mes in enumerate(meses):
                    if self._cancel_event.is_set():
                        break
                    comp = f"{ano}.{mes}"
                    self._stats["competencia_atual"] = comp
                    self._emit_progress()
                    nome_arq = f"SIMPLES_{comp}_extrato.pdf"

                    if nome_arq in log_data:
                        self._log(f"  [PULAR] {comp} \u2014 ja baixado",
                                  "info")
                        self._stats["pulados"] += 1
                        self._emit_progress()
                        continue

                    self._log(f"  [{i+1}/{total}] Extrato competencia "
                              f"{comp}...", "info")
                    try:
                        baixado = self._capturar_extrato_por_mes(mes)
                    except Exception as ex:
                        self._log(f"    \u2717 erro: {ex}", "error")
                        baixado = None

                    if not baixado:
                        self._log(f"    \u2717 ERRO: Extrato de {comp} "
                                  f"nao foi baixado.", "error")
                        self._stats["erros"] += 1
                        self._emit_progress()
                        continue

                    # renomeia o PDF baixado para o padrao do AgriTax
                    destino = self.out_dir / nome_arq
                    try:
                        if destino.exists():
                            destino.unlink()
                        Path(baixado).rename(destino)
                    except Exception as ex:
                        self._log(f"    \u26a0 baixado como "
                                  f"{Path(baixado).name} (nao consegui "
                                  f"renomear: {ex})", "warn")
                        destino = Path(baixado)

                    log_data[nome_arq] = {
                        "competencia": comp,
                        "cnpj": cnpj,
                        "baixado_em": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    try:
                        log_path.write_text(
                            json.dumps(log_data, ensure_ascii=False,
                                       indent=2), encoding="utf-8")
                    except Exception:
                        pass

                    self._downloaded_files.append(destino)
                    self._stats["baixados"] += 1
                    self._log(f"    \u2713 salvo: {destino.name} "
                              f"({destino.stat().st_size // 1024} KB)",
                              "ok")
                    self._emit_progress()
                    time.sleep(1.0)

            success = not self._cancel_event.is_set()
        except Exception as e:
            self._log(f"\u2717 Erro fatal: {e}", "error")
            summary["erro"] = str(e)
        finally:
            summary.update(self._stats)
            summary["arquivos_baixados"] = list(self._downloaded_files)
            summary["cancelado"] = self._cancel_event.is_set()
            summary["cnpj"] = self._cnpj
            summary["empresa"] = self._empresa
            summary["pasta_saida"] = (str(self.out_dir)
                                      if self.out_dir else "")
            self.on_finished(success, summary)


