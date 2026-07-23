"""Infraestrutura dos robôs e-CAC: Chrome em modo debug, manifesto de downloads.

Extraído do AgriTax Audit v5 consolidado, sem alterações de lógica (M4).
"""
import csv
import json
import os
import unicodedata
import platform
import re
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Callable

from audit.parsers._ocr import _get_ocr_env, _pytesseract

try:
    import requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

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


# ---- Constantes do downloader -----------------------------------------------
_DL_PERDCOMP_NUMBER_RE = re.compile(r"\d{5}\.\d{5}\.\d{6}\.\d+\.\d+\.\d+-\d{4}")
_DL_DATE_BR_RE = re.compile(r"\d{2}/\d{2}/\d{4}")
_DL_ICON_TIPO_MAP = {
    "icon-DeclaraCompensacao": "Declaração de Compensação",
    "icon-PedidoResarcimento": "Pedido de Ressarcimento",
    "icon-PedidoRestituicao":  "Pedido de Restituição",
    "icon-PedidoReembolso":    "Pedido de Reembolso",
    "icon-PedidoCancelamento": "Pedido de Cancelamento",
}
_DL_WAIT_TIMEOUT = 45
_DL_PAGE_CHANGE_TIMEOUT = 30
_DL_DOWNLOAD_TIMEOUT = 25   # s — espera curta: timeout longo esfria a
                            # sessão eCAC e faz a execução inteira travar.
                            # Um PDF que vai abrir, abre em poucos segundos.
_DL_DELAY_AFTER_CLICK = 1.0
_DL_DELAY_BETWEEN_DOWNLOADS = 1.5


# ---- Helpers de Chrome -------------------------------------------------------
def _dl_find_chrome_executable() -> Optional[str]:
    """Localiza o chrome.exe / google-chrome no sistema."""
    sysname = platform.system()
    candidates: List[str] = []
    if sysname == "Windows":
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
    elif sysname == "Darwin":
        candidates = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    else:
        candidates = ["/usr/bin/google-chrome", "/usr/bin/chromium-browser",
                      "/usr/bin/chromium", "/snap/bin/chromium"]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return None


def _dl_open_chrome_in_debug_mode(port: int = 9222,
                                  user_data_dir: Optional[str] = None) -> tuple:
    """Abre o Chrome em modo debug. Retorna (ok: bool, msg: str)."""
    exe = _dl_find_chrome_executable()
    if not exe:
        return False, ("Não localizei o Chrome no sistema.\n"
                       "Verifique se ele está instalado.")
    if user_data_dir is None:
        if platform.system() == "Windows":
            user_data_dir = r"C:\chrome-debug-perdcomp"
        else:
            user_data_dir = "/tmp/chrome-debug-perdcomp"
    try:
        subprocess.Popen([exe,
                          f"--remote-debugging-port={port}",
                          f"--user-data-dir={user_data_dir}"])
        return True, (f"Chrome aberto com debug remoto na porta {port}.\n"
                      f"Perfil: {user_data_dir}")
    except Exception as e:
        return False, f"Falha ao abrir Chrome: {e}"


def _dl_is_debug_port_open(port: int = 9222, timeout: float = 1.5) -> bool:
    """Verifica se há Chrome em modo debug respondendo na porta."""
    if not REQUESTS_OK:
        return False
    try:
        r = requests.get(f"http://localhost:{port}/json/version", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


# ---- Auto-instalador de dependências -----------------------------------------
def _dl_install_deps_subprocess(packages: List[str],
                                on_log: Callable[[str], None],
                                on_done: Callable[[bool, int], None]) -> None:
    """Roda 'sys.executable -m pip install <packages>' em thread, com output
    em tempo real via callback. Garante que a instalação acontece NO MESMO
    Python que está rodando o AgriTax (evitando o problema clássico de pip
    em interpretador errado no Windows).
    """
    def worker():
        try:
            on_log(f"Python ativo: {sys.executable}")
            on_log(f"Pacotes: {', '.join(packages)}")
            on_log("=" * 60)
            cmd = [sys.executable, "-m", "pip", "install", "--upgrade"] + packages
            on_log("Comando: " + " ".join(cmd))
            on_log("")

            kwargs = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
                "bufsize": 1,
                "universal_newlines": True,
            }
            # Evita janela CMD piscando no Windows
            if platform.system() == "Windows":
                kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW

            proc = subprocess.Popen(cmd, **kwargs)
            for line in proc.stdout:
                on_log(line.rstrip())
            proc.wait()
            on_done(proc.returncode == 0, proc.returncode)
        except Exception as e:
            on_log(f"ERRO inesperado: {e}")
            on_done(False, -1)

    threading.Thread(target=worker, daemon=True).start()


# ---- Pasta raiz centralizada (C:\AgriTaxAudit) ------------------------------
# Estrutura: C:\AgriTaxAudit\<8digitos_cnpj>\
#                              ├── log_perdcomp.json
#                              ├── log_dctf.json
#                              ├── perdcomp\
#                              └── dctf\

def _dl_get_root_dir() -> Path:
    """Pasta raiz onde tudo é organizado por CNPJ.

    Padrão: C:\\AgriTaxAudit no Windows; ~/AgriTaxAudit em outros SOs
    (Linux/macOS) — mantém a mesma estrutura embaixo dela.
    """
    if platform.system() == "Windows":
        return Path("C:/AgriTaxAudit")
    return Path.home() / "AgriTaxAudit"


def _dl_cnpj_root_8digits(cnpj: str) -> str:
    """Extrai os 8 primeiros dígitos do CNPJ (raiz da empresa, sem filial).

    '42.545.254/0001-35' -> '42545254'

    Matriz e filiais da mesma empresa compartilham a pasta (mesma raiz).
    """
    digits = re.sub(r"\D", "", cnpj or "")
    if len(digits) < 8:
        return ""
    return digits[:8]


def _dl_cnpj_is_valid(cnpj: str) -> bool:
    """Valida os 14 dígitos de um CNPJ pelos dígitos verificadores.
    Usado para descartar números de 14 dígitos que não são CNPJ ao
    ler o documento (evita falso-positivo na detecção)."""
    d = re.sub(r"\D", "", cnpj or "")
    if len(d) != 14 or len(set(d)) == 1:
        return False

    def _dv(base: str, pesos: list) -> str:
        s = sum(int(c) * p for c, p in zip(base, pesos))
        r = s % 11
        return "0" if r < 2 else str(11 - r)

    p1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    p2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    return d[12] == _dv(d[:12], p1) and d[13] == _dv(d[:13], p2)


def _dl_format_cnpj(cnpj: str) -> str:
    """Formata 14 dígitos como XX.XXX.XXX/XXXX-XX. '' se não tiver 14."""
    d = re.sub(r"\D", "", cnpj or "")
    if len(d) != 14:
        return ""
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def _dl_read_cnpj_from_zip(zip_path, log_fn=None) -> str:
    """Lê o CNPJ do contribuinte de DENTRO de um .zip de DCTFWeb (XML de
    Saída) — a forma à prova de falhas, independente da tela do eCAC.

    Procura, em ordem:
      1. Dígitos logo após uma tag/rótulo de contribuinte/declarante.
      2. CNPJ formatado logo após esse rótulo.
      3. Se houver um único CNPJ válido em todo o conteúdo, usa-o.

    Aceita também .zip que na verdade é o XML puro (o eCAC às vezes
    entrega assim). Retorna o CNPJ formatado ou '' se não conseguir.
    """
    import zipfile

    def _log(m, n="info"):
        if log_fn:
            try:
                log_fn(m, n)
            except Exception:
                pass

    p = Path(zip_path)
    if not p.exists():
        return ""

    textos, nomes_internos = [], []
    try:
        with zipfile.ZipFile(str(p)) as zf:
            for nome in zf.namelist():
                nomes_internos.append(nome)
                if nome.lower().endswith((".xml", ".txt", ".csv")):
                    try:
                        textos.append(zf.read(nome).decode("utf-8", "ignore"))
                    except Exception:
                        pass
    except zipfile.BadZipFile:
        # Não era um zip — tenta tratar como XML/texto puro
        try:
            textos.append(p.read_bytes().decode("utf-8", "ignore"))
        except Exception:
            return ""
    except Exception:
        return ""

    blob = "\n".join(textos)

    # 1) Dígitos logo após um rótulo/tag de contribuinte/declarante
    rotulo = (r"(?:cnpj(?:contribuinte|declarante)?|ni(?:contribuinte|"
              r"declarante)?|nrinscricao|nuinscricao|inscricao|"
              r"contribuinte|declarante|empregador)")
    m = re.search(rotulo + r"[^0-9]{0,18}(\d{14})\b", blob, re.IGNORECASE)
    if m and _dl_cnpj_is_valid(m.group(1)):
        return _dl_format_cnpj(m.group(1))
    m = re.search(rotulo + r"[^0-9]{0,18}(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})",
                  blob, re.IGNORECASE)
    if m:
        d = re.sub(r"\D", "", m.group(1))
        if _dl_cnpj_is_valid(d):
            return _dl_format_cnpj(d)

    # 2) Qualquer CNPJ válido — formatado, ou 14 dígitos, no conteúdo e
    #    nos nomes de arquivo (do zip e dos arquivos internos).
    candidatos = set()
    for c in re.findall(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", blob):
        d = re.sub(r"\D", "", c)
        if _dl_cnpj_is_valid(d):
            candidatos.add(d)
    for d in re.findall(r"(?<!\d)\d{14}(?!\d)", blob):
        if _dl_cnpj_is_valid(d):
            candidatos.add(d)
    for nm in nomes_internos + [p.name]:
        for d in re.findall(r"(?<!\d)\d{14}(?!\d)", nm):
            if _dl_cnpj_is_valid(d):
                candidatos.add(d)

    if len(candidatos) == 1:
        return _dl_format_cnpj(next(iter(candidatos)))
    if len(candidatos) > 1:
        _log("   ⚠ Vários CNPJs no XML — não dá pra definir o "
             "contribuinte com segurança: "
             + ", ".join(_dl_format_cnpj(c) for c in sorted(candidatos)),
             "warn")
    return ""


def _dl_get_company_paths(cnpj: str) -> Dict[str, Path]:
    """Calcula todas as pastas/logs para um CNPJ.

    A pasta usa a raiz de 8 dígitos do CNPJ — matriz e filiais da
    mesma empresa compartilham a pasta.

    Retorna dict com chaves:
      root           -> C:/AgriTaxAudit/42545254
      perdcomp_dir   -> C:/AgriTaxAudit/42545254/perdcomp
      dctf_dir       -> C:/AgriTaxAudit/42545254/dctf
      dctfweb_dir    -> C:/AgriTaxAudit/42545254/dctfweb
      darf_dir       -> C:/AgriTaxAudit/42545254/darf
      log_perdcomp   -> C:/AgriTaxAudit/42545254/log_perdcomp.json
      log_dctf       -> C:/AgriTaxAudit/42545254/log_dctf.json
      log_dctfweb    -> C:/AgriTaxAudit/42545254/log_dctfweb.json
      log_darf       -> C:/AgriTaxAudit/42545254/log_darf.json
    """
    raiz = _dl_cnpj_root_8digits(cnpj)
    if not raiz:
        raise ValueError(f"CNPJ inválido (não tem 8 dígitos): {cnpj!r}")
    base = _dl_get_root_dir() / raiz
    return {
        "root":          base,
        "perdcomp_dir":  base / "perdcomp",
        "dctf_dir":      base / "dctf",
        "dctfweb_dir":   base / "dctfweb",
        "darf_dir":      base / "darf",
        "simples_dir":   base / "simples",
        "log_perdcomp":  base / "log_perdcomp.json",
        "log_dctf":      base / "log_dctf.json",
        "log_dctfweb":   base / "log_dctfweb.json",
        "log_darf":      base / "log_darf.json",
        "log_simples":   base / "log_simples.json",
    }


def _dl_ensure_company_dirs(cnpj: str) -> Dict[str, Path]:
    """Cria a estrutura de pastas para o CNPJ se ainda não existir.
    Retorna o mesmo dict de _dl_get_company_paths.
    """
    paths = _dl_get_company_paths(cnpj)
    paths["root"].mkdir(parents=True, exist_ok=True)
    paths["perdcomp_dir"].mkdir(parents=True, exist_ok=True)
    paths["dctf_dir"].mkdir(parents=True, exist_ok=True)
    paths["dctfweb_dir"].mkdir(parents=True, exist_ok=True)
    paths["darf_dir"].mkdir(parents=True, exist_ok=True)
    paths["simples_dir"].mkdir(parents=True, exist_ok=True)
    return paths


# Regex pra detectar o CNPJ ativo (eCAC / DCTFWeb). Aceita varios
# rotulos: "Titular:", "Contribuinte:", "Atuando como ...".
_DL_TITULAR_RE = re.compile(
    r"(?:Titular|Contribuinte|Atuando como)\b[^:\d]{0,40}:?\s*"
    r"(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})\s*-?\s*([^\r\n|<]+)?",
    re.IGNORECASE,
)
_DL_CNPJ_LABEL_RE = re.compile(
    r"CNPJ\b[^:\d]{0,20}:?\s*(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})",
    re.IGNORECASE,
)
_DL_ANY_CNPJ_RE = re.compile(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b")


def _dl_detect_cnpj_from_ecac(driver, log_fn=None) -> tuple:
    """Detecta o CNPJ ativo na aba ATUAL do navegador (eCAC / DCTFWeb).

    Procura, em ordem:
      1. Um rotulo explicito ('Titular:', 'Contribuinte:', 'CNPJ:')
         seguido do numero — a forma mais confiavel.
      2. Se nao houver rotulo, coleta TODOS os CNPJs da aba. Se houver
         exatamente UM CNPJ distinto, ele e o contribuinte ativo.

    A varredura cobre iframes aninhados (a DCTFWeb roda dentro de
    iframe) e tambem valores de <input>/<textarea>/<select> e atributos
    — a DCTFWeb e ASP.NET WebForms, e parte do texto fica em campos,
    fora do innerText.

    IMPORTANTE: varre SOMENTE a aba atual e seus iframes — nunca as
    outras abas — para nao pegar o CNPJ de outra empresa aberta em
    paralelo.

    Retorna (cnpj_formatado, nome_empresa) ou ('', '').
    """
    def _log(msg, nivel="info"):
        if log_fn:
            try:
                log_fn(msg, nivel)
            except Exception:
                pass

    JS_DUMP = (
        "var t=(document.body&&document.body.innerText)||'';"
        "var vals=[];"
        "document.querySelectorAll("
        "'input,textarea,select,[title],[aria-label],[value]')"
        ".forEach(function(el){"
        "['value','title','aria-label','placeholder'].forEach(function(a){"
        "var v=el.getAttribute&&el.getAttribute(a);"
        "if(v){vals.push(v);}"
        "});"
        "if(el.value){vals.push(el.value);}"
        "if(el.tagName==='SELECT'&&el.selectedOptions){"
        "for(var i=0;i<el.selectedOptions.length;i++){"
        "vals.push(el.selectedOptions[i].text);}}"
        "});"
        "return t+String.fromCharCode(10)+vals.join(String.fromCharCode(10));"
    )

    titular = {"cnpj": "", "nome": ""}
    todos = set()

    def _scan_current():
        """Le o contexto Selenium atual e acumula CNPJs encontrados."""
        textos = []
        try:
            textos.append(driver.execute_script(JS_DUMP) or "")
        except Exception:
            pass
        try:
            textos.append(driver.page_source or "")
        except Exception:
            pass
        for txt in textos:
            if not txt:
                continue
            if not titular["cnpj"]:
                m = _DL_TITULAR_RE.search(txt)
                if m:
                    titular["cnpj"] = m.group(1).strip()
                    nome = (m.group(2) or "").strip()
                    nome = re.split(r"\s{2,}|[<>|]", nome)[0].strip()
                    titular["nome"] = nome
                else:
                    m = _DL_CNPJ_LABEL_RE.search(txt)
                    if m:
                        titular["cnpj"] = m.group(1).strip()
            for c in _DL_ANY_CNPJ_RE.findall(txt):
                todos.add(c)

    def _walk(depth):
        """Varre o contexto atual e desce recursivamente nos iframes."""
        _scan_current()
        if depth <= 0:
            return
        try:
            frames = (driver.find_elements(By.TAG_NAME, "iframe")
                      + driver.find_elements(By.TAG_NAME, "frame"))
        except Exception:
            frames = []
        for fr in frames:
            try:
                driver.switch_to.frame(fr)
            except Exception:
                continue
            try:
                _walk(depth - 1)
            finally:
                try:
                    driver.switch_to.parent_frame()
                except Exception:
                    try:
                        driver.switch_to.default_content()
                    except Exception:
                        pass

    try:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        _walk(3)
    finally:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass

    # 1) Rotulo explicito venceu — forma mais confiavel
    if titular["cnpj"]:
        return titular["cnpj"], titular["nome"]

    # 2) Um unico CNPJ na aba inteira — e o contribuinte ativo
    if len(todos) == 1:
        unico = next(iter(todos))
        _log(f"    CNPJ detectado (unico na aba, sem rotulo): {unico}",
             "info")
        return unico, ""

    # 3) Falhou — diagnostico pra ajustar a deteccao
    if len(todos) > 1:
        _log("    \u26a0 Varios CNPJs na aba e nenhum rotulo identifica "
             "o ativo: " + ", ".join(sorted(todos)), "warn")
    else:
        _log("    \u26a0 Nenhum CNPJ encontrado na aba (nem no texto, nem "
             "em campos de formulario nem nos iframes).", "warn")
    return "", ""


def _dl_relocate_pdf_by_cnpj(pdf_path: Path, cnpj_pasta_atual: str,
                             tipo_pasta: str, log_fn=None):
    """Verifica o CNPJ DENTRO de um PDF baixado e, se for de empresa
    diferente da pasta atual, move o arquivo pra pasta correta.

    Funciona pra qualquer módulo que gere PDF com CNPJ legível
    (PERDCOMP, DCTF, DARF). A DCTFWeb baixa .zip, então não usa isto.

    Parâmetros:
      pdf_path          -> caminho do PDF recém-baixado
      cnpj_pasta_atual  -> CNPJ que o downloader detectou (= pasta atual)
      tipo_pasta        -> 'perdcomp_dir' | 'dctf_dir' | 'darf_dir'
      log_fn            -> função de log opcional: log_fn(msg, nivel)

    Retorna uma tupla (status, novo_caminho):
      ('ok',      pdf_path)   -> CNPJ confere, nada mudou
      ('movido',  destino)    -> era de outra empresa, foi movido
      ('sem_cnpj',pdf_path)   -> não deu pra ler o CNPJ, ficou onde está
      ('erro',    pdf_path)   -> falha ao mover
    """
    def _log(msg, nivel="info"):
        if log_fn:
            try:
                log_fn(msg, nivel)
            except Exception:
                pass

    if not pdf_path or not pdf_path.exists():
        return ("erro", pdf_path)

    cnpj_pdf = _dl_read_cnpj_from_pdf(pdf_path)
    if not cnpj_pdf:
        _log(f"    ⚠ Não consegui ler o CNPJ de dentro de "
             f"{pdf_path.name} — mantido na pasta atual.", "warn")
        return ("sem_cnpj", pdf_path)

    raiz_pdf = _dl_cnpj_root_8digits(cnpj_pdf)
    raiz_pasta = _dl_cnpj_root_8digits(cnpj_pasta_atual or "")

    if raiz_pdf and raiz_pdf == raiz_pasta:
        return ("ok", pdf_path)  # CNPJ confere

    # É de outra empresa — move
    _log(f"    ⚠ CNPJ do PDF ({cnpj_pdf}) difere da pasta atual "
         f"(raiz {raiz_pasta}). Movendo para a pasta correta...", "warn")
    try:
        paths_corretos = _dl_ensure_company_dirs(cnpj_pdf)
        destino_dir = paths_corretos[tipo_pasta]
        destino = destino_dir / pdf_path.name
        if destino.exists():
            i = 1
            while destino.exists():
                destino = destino_dir / (
                    pdf_path.stem + f"_({i})" + pdf_path.suffix)
                i += 1
        import shutil
        shutil.move(str(pdf_path), str(destino))
        _log(f"    ✓ Movido para: {destino}", "ok")
        return ("movido", destino)
    except Exception as e:
        _log(f"    ✗ Falha ao mover o PDF: {e}", "error")
        return ("erro", pdf_path)


def _dl_match_cnpj(texto: str) -> str:
    """Procura um CNPJ num bloco de texto. Prioriza o que vem logo
    apos o rotulo 'CNPJ' (pra nao pegar CPF nem outro numero)."""
    if not texto:
        return ""
    m = re.search(r"CNPJ\s*:?\s*(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})",
                  texto, re.IGNORECASE)
    if m:
        return m.group(1)
    m = _DL_ANY_CNPJ_RE.search(texto)
    if m:
        return m.group(0)
    return ""


def _dl_read_cnpj_from_pdf(pdf_path: Path) -> str:
    """Le o CNPJ de dentro de um PDF ja baixado (PERDCOMP, DCTF, DARF).

    Todo comprovante traz 'CNPJ XX.XXX.XXX/XXXX-XX' no topo. Ler o
    CNPJ do proprio documento e a forma A PROVA DE FALHAS de saber a
    empresa — nao depende da deteccao no eCAC, que pode pegar o CNPJ
    errado.

    Estrategia em duas etapas:
      1. Extracao de texto direta (pdfplumber) — PDFs com camada de
         texto, que e o caso quando o download pega o arquivo original.
      2. Fallback OCR (pytesseract) — para PDFs sem texto (imagem ou
         curvas vetoriais).

    Retorna o CNPJ formatado (XX.XXX.XXX/XXXX-XX) ou '' se nao achar.
    """
    try:
        import pdfplumber
    except Exception:
        return ""

    # -- Etapa 1: texto direto --
    texto = ""
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            if not pdf.pages:
                return ""
            texto = pdf.pages[0].extract_text() or ""
    except Exception:
        texto = ""

    cnpj = _dl_match_cnpj(texto)
    if cnpj:
        return cnpj

    # -- Etapa 2: OCR da 1a pagina (PDF sem camada de texto) --
    try:
        env = _get_ocr_env()
        if not env.get("pytesseract_ok") or not env.get("tesseract_exe"):
            return ""
        ocr_lang = "por" if env.get("has_por") else "eng"
        with pdfplumber.open(str(pdf_path)) as pdf:
            if not pdf.pages:
                return ""
            img = pdf.pages[0].to_image(resolution=250).original
        texto_ocr = _pytesseract.image_to_string(
            img, lang=ocr_lang, config="--psm 6 --oem 3")
    except Exception:
        return ""

    return _dl_match_cnpj(texto_ocr)


# ---- Modelo de dados ---------------------------------------------------------
@dataclass
class _DownloadEntry:
    numero: str
    tipo: str = ""
    data_transmissao: str = ""
    tipo_credito: str = ""
    periodo: str = ""
    arquivo: str = ""
    pagina: int = 0
    status: str = "pendente"  # pendente | baixado | erro
    erro: str = ""
    kind: str = "PERDCOMP"  # PERDCOMP | DCTF | DCTFWEB | DARF | ...
    cnpj: str = ""           # CNPJ formatado (XX.XXX.XXX/XXXX-XX)
    data_download: str = ""  # YYYY-MM-DD HH:MM:SS — quando foi baixado pelo AgriTax
    tamanho_bytes: int = 0   # tamanho do PDF baixado
    # Específicos da DCTFWeb:
    categoria: str = ""      # GERAL | 13_SALARIO | AFERICAO | RECLAMATORIA | ...
    subtipo: str = ""        # DEBITOS | CREDITOS | COMPLETA (3 recibos por DCTFWeb)

    def safe_basename(self) -> str:
        clean = re.sub(r"[^A-Za-z0-9._-]", "_", self.numero) or "SEM_NUMERO"
        if self.kind == "DCTFWEB":
            # Formato: DCTFWEB_AAAA.MM_<CATEGORIA>_<SUBTIPO>
            # OBS: o campo `numero` da DCTFWeb JÁ é período_categoria_subtipo
            # (definido em _make_subtipo_entry como chave única). Por isso
            # NÃO concatenamos `clean` de novo — senão o nome duplica.
            partes = ["DCTFWEB"]
            if self.periodo:
                partes.append(self._normalize_periodo_aaaamm(self.periodo))
            if self.categoria:
                partes.append(re.sub(r"[^A-Za-z0-9]", "_",
                                     self.categoria.upper()))
            if self.subtipo:
                partes.append(re.sub(r"[^A-Za-z0-9]", "_",
                                     self.subtipo.upper()))
            # Se por algum motivo não temos período/categoria/subtipo,
            # cai pro número como fallback pra garantir unicidade.
            if len(partes) == 1:
                partes.append(clean)
            return "_".join(partes)
        if self.kind in ("DARF", "DAS"):
            # Formato: DARF_AAAA.MM_<codigo_receita>_<numero_documento>
            # periodo = período de apuração; tipo_credito = código receita
            partes = [self.kind]
            if self.periodo:
                partes.append(self._normalize_periodo_aaaamm(self.periodo))
            if self.tipo_credito:  # reaproveitado: código de receita
                partes.append(re.sub(r"[^A-Za-z0-9]", "_",
                                     self.tipo_credito))
            partes.append(clean)
            return "_".join(partes)
        if self.kind == "DCTF" and self.periodo:
            periodo_norm = self._normalize_periodo_aaaamm(self.periodo)
            return f"{self.kind}_{periodo_norm}_{clean}"
        return f"{self.kind}_{clean}"

    @staticmethod
    def _normalize_periodo_aaaamm(periodo: str) -> str:
        """Converte 'Março/2012', '03/2012', '1º Trimestre/2012' etc.
        para o formato 'AAAA.MM' (ou 'AAAA.TX' para trimestres).

        Exemplos:
          'Março/2012'           -> '2012.03'
          'janeiro/2025'         -> '2025.01'
          '03/2012'              -> '2012.03'
          '2012/03'              -> '2012.03'
          '1º Trimestre/2012'    -> '2012.T1'
          'Trimestre 2 2024'     -> '2024.T2'

        Em caso de fallback, retorna o período sanitizado.
        """
        if not periodo:
            return "SEM_PERIODO"

        # Remove acentos pra match mais robusto
        try:
            txt = unicodedata.normalize("NFKD", periodo)
            txt = "".join(c for c in txt if not unicodedata.combining(c))
        except Exception:
            txt = periodo
        txt_low = txt.lower().strip()

        meses = {
            "janeiro": "01", "fevereiro": "02", "marco": "03", "abril": "04",
            "maio": "05", "junho": "06", "julho": "07", "agosto": "08",
            "setembro": "09", "outubro": "10", "novembro": "11", "dezembro": "12",
        }

        # Caso 1: "Mês/AAAA" ou "Mês AAAA"
        for nome, num in meses.items():
            m = re.search(rf"{nome}\s*/?\s*(\d{{4}})", txt_low)
            if m:
                return f"{m.group(1)}.{num}"

        # Caso 2: "MM/AAAA"
        m = re.search(r"\b(\d{1,2})/(\d{4})\b", txt_low)
        if m:
            mes, ano = m.group(1).zfill(2), m.group(2)
            if 1 <= int(mes) <= 12:
                return f"{ano}.{mes}"

        # Caso 3: "AAAA/MM"
        m = re.search(r"\b(\d{4})/(\d{1,2})\b", txt_low)
        if m:
            ano, mes = m.group(1), m.group(2).zfill(2)
            if 1 <= int(mes) <= 12:
                return f"{ano}.{mes}"

        # Caso 4: "Nº Trimestre/AAAA" ou "Trimestre N AAAA"
        m = re.search(r"(\d)[oº°]?\s*trimestre[\s/]+(?:de\s+)?(\d{4})", txt_low)
        if m:
            return f"{m.group(2)}.T{m.group(1)}"
        m = re.search(r"trimestre\s*(\d)[\s/]+(?:de\s+)?(\d{4})", txt_low)
        if m:
            return f"{m.group(2)}.T{m.group(1)}"

        # Caso 5: período ANUAL — só o ano (ex: "2025").
        # Ocorre na DCTFWeb de 13º salário, que não tem mês.
        # Usa o sufixo ".13" pra indicar período anual / 13º — assim o
        # nome mantém o padrão AAAA.MM e ordena depois dos 12 meses.
        m = re.fullmatch(r"\s*(20\d{2})\s*", txt_low)
        if m:
            return f"{m.group(1)}.13"

        # Fallback: remove caracteres ruins do período original
        return re.sub(r"[^A-Za-z0-9._-]", "_", periodo) or "SEM_PERIODO"


# ---- Manifesto (log persistente entre execuções) -----------------------------
class _DownloadManifest:
    """Log JSON dos arquivos já baixados.

    Por padrão pula re-download (is_done = True) somente se:
      • Item está no log com status='baixado' E
      • O arquivo PDF correspondente AINDA EXISTE no disco

    Isso evita que o usuário ache que tem o PDF baixado quando alguém apagou
    a pasta — nesse caso o downloader baixa de novo automaticamente.
    """

    def __init__(self, path: Path, files_root: Optional[Path] = None):
        self.path = path
        self.files_root = files_root or path.parent
        self.entries: Dict[str, _DownloadEntry] = {}
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                for k, v in raw.items():
                    # Tolera entries antigas sem alguns campos novos
                    valid_keys = {f.name for f in
                                  __import__("dataclasses").fields(_DownloadEntry)}
                    v_clean = {kk: vv for kk, vv in v.items() if kk in valid_keys}
                    self.entries[k] = _DownloadEntry(**v_clean)
            except Exception:
                pass

    def is_done(self, numero: str) -> bool:
        """True se o item já foi baixado E o arquivo ainda existe na pasta."""
        e = self.entries.get(numero)
        if not (e and e.status == "baixado" and e.arquivo):
            return False
        # Verifica se o arquivo ainda existe
        try:
            full_path = self.files_root / e.arquivo
            if not full_path.exists():
                # Arquivo foi apagado — re-baixa
                return False
            # Se o tamanho registrado bate, assume que é o mesmo
            if e.tamanho_bytes > 0:
                actual = full_path.stat().st_size
                if abs(actual - e.tamanho_bytes) > 1024:  # 1KB de margem
                    return False
        except Exception:
            return False
        return True

    def upsert(self, e: _DownloadEntry) -> None:
        # Preenche metadados se está marcado como baixado e tem arquivo
        if e.status == "baixado" and e.arquivo:
            try:
                full_path = self.files_root / e.arquivo
                if full_path.exists():
                    e.tamanho_bytes = full_path.stat().st_size
                    if not e.data_download:
                        e.data_download = time.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
        self.entries[e.numero] = e
        self._save()

    def _save(self) -> None:
        data = {k: asdict(v) for k, v in self.entries.items()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def export_csv(self, csv_path: Path) -> None:
        if not self.entries:
            return
        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["cnpj", "numero", "tipo", "data_transmissao",
                        "tipo_credito", "periodo", "pagina", "arquivo",
                        "tamanho_bytes", "data_download", "status", "erro"])
            for e in self.entries.values():
                w.writerow([e.cnpj, e.numero, e.tipo, e.data_transmissao,
                            e.tipo_credito, e.periodo, e.pagina, e.arquivo,
                            e.tamanho_bytes, e.data_download, e.status, e.erro])

    def migrate_from(self, old_manifest_path: Path,
                     old_files_root: Optional[Path] = None) -> int:
        """Migra entries de um manifest antigo (formato legado).

        Adiciona apenas itens que ainda não existem no log atual.
        Retorna número de entries migradas.
        """
        if not old_manifest_path.exists():
            return 0
        try:
            raw = json.loads(old_manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return 0

        migrated = 0
        valid_keys = {f.name for f in
                      __import__("dataclasses").fields(_DownloadEntry)}
        for k, v in raw.items():
            if k in self.entries:
                continue
            v_clean = {kk: vv for kk, vv in v.items() if kk in valid_keys}
            try:
                e = _DownloadEntry(**v_clean)
            except Exception:
                continue
            # Tenta copiar o arquivo da pasta antiga se a nova não tiver
            if old_files_root and e.arquivo and e.status == "baixado":
                old_pdf = old_files_root / e.arquivo
                new_pdf = self.files_root / e.arquivo
                if old_pdf.exists() and not new_pdf.exists():
                    try:
                        new_pdf.parent.mkdir(parents=True, exist_ok=True)
                        import shutil
                        shutil.copy2(str(old_pdf), str(new_pdf))
                    except Exception:
                        pass
            self.entries[k] = e
            migrated += 1
        if migrated:
            self._save()
        return migrated


# ---- Backend: PerdcompDownloader ---------------------------------------------
