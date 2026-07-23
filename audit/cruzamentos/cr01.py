"""CR-01 — ECD × ECF: resultado contábil × resultado na apuração fiscal.

O que revela (PT-AF-003): divergência contabilidade × apuração fiscal — o
resultado que a ECF usa como ponto de partida do e-Lalur deve ser o mesmo
da escrituração contábil (ECD). Divergência = base de IRPJ/CSLL suspeita
(malha) ou escriturações de versões diferentes.

Comparação por (CNPJ, exercício):
    ECD: linha "RESULTADO DO EXERCÍCIO" do último demonstrativo J150
         (último par valor/indicador D-C = acumulado do exercício)
    ECF: linha código 3 "RESULTADO LÍQUIDO DO PERÍODO" do último período
         (A00 quando existe; senão o último trimestre) — L300 (Real) ou
         P150 (Presumido), mesmos layouts nos arquivos reais do BX.

Os dois lados são lidos direto do raw/ (a ECD não vira fatos — é insumo
contábil); múltiplas versões do mesmo exercício usam o arquivo de nome
maior (timestamp de transmissão embutido nos nomes reais).
"""
from __future__ import annotations

import re
from pathlib import Path

from audit.core.modelo import Achado

from .motor import brl

TOL = 0.10
BASE_LEGAL = "IN RFB 2.003/2021 (ECD); IN RFB 2.004/2021 (ECF); art. 8º DL 1.598/77"

# SPED corta zeros finais: "327482,2" e "40403,2" são valores válidos
_RE_VALOR = re.compile(r"^-?[\d.]*\d,\d{1,2}$|^-?\d+$|^-?\d+\.\d+$")


def _brl(s: str) -> float:
    s = (s or "").strip()
    if not s:
        return 0.0
    try:
        return float(s.replace(".", "").replace(",", ".")) if "," in s else float(s)
    except ValueError:
        return 0.0


def _norm(s: str) -> str:
    return s.upper().translate(str.maketrans("ÁÀÂÃÉÊÍÓÔÕÚÜÇ", "AAAAEEIOOOUUC")).strip()


def _ultimo_valor_dc(campos: list[str]) -> float | None:
    """Último par (valor, D/C) da linha: C = lucro (+), D = prejuízo (−)."""
    for i in range(len(campos) - 1, 0, -1):
        if campos[i] in ("C", "D") and i >= 1 and _RE_VALOR.match(campos[i - 1] or ""):
            v = _brl(campos[i - 1])
            return -v if campos[i] == "D" else v
    return None


def _linhas(path: Path) -> list[str]:
    return path.read_bytes().decode("latin-1", errors="replace") \
        .replace("\r\n", "\n").split("\n")


def _pares_valor_dc(campos: list[str]) -> list[float]:
    """Todos os pares (valor, D/C) da linha, em ordem. C = +, D = −."""
    pares = []
    for i in range(1, len(campos)):
        if campos[i] in ("C", "D") and _RE_VALOR.match(campos[i - 1] or ""):
            v = _brl(campos[i - 1])
            pares.append(-v if campos[i] == "D" else v)
    return pares


def resultado_ecd(path: Path) -> dict | None:
    """{cnpj, exercicio, resultado} — resultado do exercício na DRE (J150).

    Estrutura real (validada no BX): um bloco J150 por período J005
    (trimestres + demonstração anual). A linha-resultado é o totalizador
    RAIZ do bloco (IND_COD_AGL='T', nível 1 — nomes variam: RESULTADO/
    LUCRO/PREJUÍZO DO EXERCÍCIO). Cada linha traz até 2 pares (valor, D/C)
    e o LADO que representa o período corrente muda entre leiautes — o lado
    certo é o que fecha: valor anual ≈ Σ valores dos trimestres."""
    cnpj = exercicio = dt_ini = dt_fin = ""
    blocos: list[dict] = []   # {"ini", "fim", "pares": [..]}
    atual: dict | None = None
    for ln in _linhas(path):
        if not ln.startswith("|"):
            continue
        c = ln.split("|")[1:-1] if ln.rstrip().endswith("|") else ln.split("|")[1:]
        if not c:
            continue
        if c[0] == "0000" and len(c) > 5:
            dt_ini, dt_fin = (c[2] or ""), (c[3] or "")
            exercicio = dt_ini[4:8]
            cnpj = re.sub(r"\D", "", c[5])
        elif c[0] == "J005" and len(c) > 2:
            atual = {"ini": c[1], "fim": c[2], "pares": None, "fallback": None}
            blocos.append(atual)
        elif c[0] == "J150" and atual is not None and len(c) > 7:
            eh_totalizador = c[3] == "T" and c[4] == "1"
            pares = _pares_valor_dc(c)
            if not pares:
                continue
            eh_resultado = bool(re.search(r"RESULTADO|LUCRO|PREJU", _norm(c[6])))
            if eh_totalizador and eh_resultado:
                atual["pares"] = pares          # linha-resultado raiz do bloco
            elif eh_totalizador and atual["fallback"] is None:
                atual["fallback"] = pares       # raiz sem nome de resultado
            elif eh_resultado and atual["fallback"] is None:
                atual["fallback"] = pares
    if not cnpj:
        return None
    uteis = [{**b, "pares": b["pares"] or b["fallback"]} for b in blocos]
    uteis = [b for b in uteis if b["pares"]]
    if not uteis:
        return None
    anual = next((b for b in uteis if b["ini"] == dt_ini and b["fim"] == dt_fin),
                 uteis[-1])
    parciais = [b for b in uteis if b is not anual]
    n_lados = max(len(b["pares"]) for b in uteis)
    melhor_lado, melhor_erro = None, None
    for lado in range(n_lados):
        try:
            v_anual = anual["pares"][lado]
            soma = sum(b["pares"][lado] for b in parciais)
        except IndexError:
            continue
        erro = abs(v_anual - soma) if parciais else None
        if parciais and erro <= TOL:
            melhor_lado = lado
            break
        if erro is not None and (melhor_erro is None or erro < melhor_erro):
            melhor_lado, melhor_erro = lado, erro
    if melhor_lado is None:
        melhor_lado = len(anual["pares"]) - 1   # sem parciais: último par (atual)
    return {"cnpj": cnpj, "exercicio": exercicio,
            "resultado": anual["pares"][melhor_lado]}


def resultado_ecf(path: Path) -> dict | None:
    """{cnpj, exercicio, resultado, periodo} — resultado líquido anual da ECF.

    L300/P150 código 3 trazem o resultado DO PERÍODO: com apuração anual usa
    o A00; com trimestral, o resultado do exercício é a SOMA dos T0x
    presentes (validado nos arquivos reais: Σ trimestres = DRE anual da ECD)."""
    cnpj = exercicio = ""
    per_atual = ""
    por_periodo: dict = {}
    for ln in _linhas(path):
        if not ln.startswith("|"):
            continue
        c = ln.split("|")[1:-1] if ln.rstrip().endswith("|") else ln.split("|")[1:]
        if not c:
            continue
        reg = c[0]
        if reg == "0000" and len(c) > 10:
            cnpj = re.sub(r"\D", "", c[3])
            exercicio = (c[10] or "")[4:8]
        elif len(reg) == 4 and reg.endswith("030") and len(c) >= 4:
            per_atual = c[3].strip()
        elif reg in ("L300", "P150") and len(c) > 7 and c[1].strip() == "3" \
                and "RESULTADO LIQUIDO DO PERIODO" in _norm(c[2]):
            v = _ultimo_valor_dc(c)
            if v is not None:
                por_periodo[per_atual] = v
    if not cnpj or not por_periodo:
        return None
    if "A00" in por_periodo:
        return {"cnpj": cnpj, "exercicio": exercicio,
                "resultado": por_periodo["A00"], "periodo": "A00"}
    tris = {p: v for p, v in por_periodo.items() if re.fullmatch(r"T\d{2}", p)}
    if tris:
        return {"cnpj": cnpj, "exercicio": exercicio,
                "resultado": round(sum(tris.values()), 2),
                "periodo": "Σ" + "+".join(sorted(tris))}
    per, v = sorted(por_periodo.items())[-1]
    return {"cnpj": cnpj, "exercicio": exercicio, "resultado": v, "periodo": per}


def run(engaj_dir: str | Path) -> tuple[list[dict], list[Achado]]:
    from audit.parsers.central import identificar_tipo
    engaj_dir = Path(engaj_dir)
    ecds: dict = {}
    ecfs: dict = {}
    for arq in sorted((engaj_dir / "raw").rglob("*.txt")):
        tipo = identificar_tipo(arq)
        if tipo == "ecd":
            r = resultado_ecd(arq)
            if r:   # nome maior = versão mais recente (substituta)
                ecds[(r["cnpj"], r["exercicio"])] = {**r, "arquivo": arq.name}
        elif tipo == "ecf":
            r = resultado_ecf(arq)
            if r:
                ecfs[(r["cnpj"], r["exercicio"])] = {**r, "arquivo": arq.name}

    linhas, achados = [], []
    for chave in sorted(set(ecds) | set(ecfs)):
        cnpj, exercicio = chave
        e_cd, e_cf = ecds.get(chave), ecfs.get(chave)
        if e_cd and e_cf:
            dif = round(e_cf["resultado"] - e_cd["resultado"], 2)
            situacao = "Conforme" if abs(dif) <= TOL else "Divergente"
        elif e_cd:
            dif, situacao = None, "ECD sem ECF"
        else:
            dif, situacao = None, "ECF sem ECD"
        linhas.append({
            "cnpj": cnpj, "exercicio": exercicio,
            "resultado_ecd": e_cd["resultado"] if e_cd else None,
            "resultado_ecf": e_cf["resultado"] if e_cf else None,
            "diferenca": dif, "situacao": situacao,
            "arquivo_ecd": e_cd["arquivo"] if e_cd else "",
            "arquivo_ecf": e_cf["arquivo"] if e_cf else "",
        })
        if situacao == "Conforme":
            continue
        if situacao == "Divergente":
            achados.append(Achado(
                ref="CR-01", cnpj=cnpj, competencia=exercicio, tributo="IRPJ/CSLL",
                titulo=f"Resultado contábil ≠ apuração fiscal em {exercicio}",
                descricao=(f"ECD (resultado do exercício) R$ {brl(e_cd['resultado'])} × "
                           f"ECF (resultado líquido, período {e_cf['periodo']}) "
                           f"R$ {brl(e_cf['resultado'])} — Δ R$ {brl(abs(dif))}. "
                           f"Arquivos: {e_cd['arquivo'][:40]} × {e_cf['arquivo'][:40]}."),
                valores={"escriturado": e_cd["resultado"],
                         "declarado": e_cf["resultado"]},
                diferenca=dif, risco="", base_legal=BASE_LEGAL,
                acao_proposta=("Conferir versões (ECD substituta × ECF retificadora) "
                               "e a recuperação da ECD na ECF — base de IRPJ/CSLL "
                               "parte desse resultado"),
                prioridade="ALTA"))
        else:
            faltante = "ECF" if situacao == "ECD sem ECF" else "ECD"
            achados.append(Achado(
                ref="CR-01", cnpj=cnpj, competencia=exercicio, tributo="IRPJ/CSLL",
                titulo=f"{faltante} do exercício {exercicio} ausente da base",
                descricao=(f"Há {('ECD' if faltante == 'ECF' else 'ECF')} de "
                           f"{exercicio} mas não {faltante} — série incompleta "
                           f"(CB-02) ou obrigação não entregue."),
                risco="R3", base_legal=BASE_LEGAL,
                acao_proposta=(f"Baixar a {faltante} via ReceitaNetBX; se não "
                               f"transmitida, regularizar a entrega (multa por atraso)"),
                prioridade="MEDIA"))
    ordem = {"Divergente": 0, "ECD sem ECF": 1, "ECF sem ECD": 2, "Conforme": 3}
    linhas.sort(key=lambda r: (ordem[r["situacao"]], r["cnpj"], r["exercicio"]))
    return linhas, achados
