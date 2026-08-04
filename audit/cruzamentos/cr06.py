"""CR-06 — PER/DCOMP × saldos de origem nos arquivos (pedido × lastro).

O que revela (PT-AF-003): compensação/pedido SEM LASTRO escriturado (glosa +
multa isolada de 50% — R6) e crédito escriturado DISPONÍVEL não pleiteado
(oportunidade com decadência — R7).

Comparação por (CNPJ, tributo PIS/COFINS, período do crédito):
    PLEITEADO  = créditos pedidos em PER/DCOMP (parser PERDCOMP)
    LASTRO     = Σ saldos credores M100/M500 (SLD_CRED) das competências do
                 período, na versão ATIVA de cada EFD

Nota metodológica: o lastro soma os saldos mensais do Bloco M — aproximação
conservadora do crédito passível de pedido no período (saldos transportados
entre meses via 1100/1500 não são reconstituídos aqui; o achado indica
verificação, não glosa automática).
"""
from __future__ import annotations

import re
import sqlite3
from collections import defaultdict

from audit.core.modelo import Achado

from .motor import TOL, brl, carregar_lado, dedupe_perdcomp

SIT_SEM_LASTRO = "Pedido acima do lastro"
SIT_COM_LASTRO = "Pedido com lastro"
SIT_SEM_PEDIDO = "Crédito escriturado sem pedido"
SIT_SEM_EFD = "Sem escrituração no período"

BASE_LEGAL = "IN RFB 2.055/2021; Lei 9.430/96, art. 74 (§17: multa de 50%)"

_PRAZO_DECADENCIAL_ANOS = 5


def run(con: sqlite3.Connection, status_map: dict | None = None
        ) -> tuple[list[dict], list[Achado]]:
    status_map = status_map or {}
    pleiteado = _pedidos_ativos(dedupe_perdcomp(
        carregar_lado(con, "PERDCOMP", "PLEITEADO")), status_map)
    creditos = carregar_lado(con, "EFD_CONTRIBUICOES", "CREDITO",
                             apenas_arquivo_ativo=True)

    # lastro por (cnpj, tributo, competência mensal) — POR FATO: M100 (PIS) e
    # M500 (COFINS) usam o MESMO código de crédito (ex.: 101), então um grupo
    # por código mistura os dois tributos; o tributo vem de cada fato
    lastro_mensal: dict = defaultdict(float)
    for (cnpj, _cod, comp), g in creditos.items():
        for f in g["fatos"]:
            lastro_mensal[(cnpj, f["tributo"], comp)] += f["valor"]

    # cobertura da série: competências mensais com EFD no banco (qualquer natureza)
    cobertura = {(r["cnpj"], r["competencia"]) for r in con.execute(
        "SELECT DISTINCT cnpj, competencia FROM fatos "
        "WHERE fonte='EFD_CONTRIBUICOES'")}

    # pedidos por (cnpj, tributo, período) — cada crédito conta UMA vez:
    # DCOMPs vinculadas a um PER (numero_pedido_vinculado) reutilizam o mesmo
    # crédito; somá-las duplicaria o pedido (é o "pedido líquido" do processo)
    pedidos: dict = defaultdict(lambda: {"valor": 0.0, "numeros": []})
    for (cnpj, _cod, comp), g in pleiteado.items():
        for f in g["fatos"]:
            trib = _tributo_do_pedido(f["tributo"])
            if not trib or not comp:
                continue
            if str(f["detalhes"].get("numero_pedido_vinculado", "")).strip():
                continue   # crédito já contado no PER inicial
            p = pedidos[(cnpj, trib, comp)]
            p["valor"] += f["valor"]
            num = f["detalhes"].get("numero_perdcomp", "")
            if num:
                p["numeros"].append(num)

    linhas, achados = [], []
    periodos_com_pedido = set()
    for (cnpj, trib, comp), p in sorted(pedidos.items()):
        meses = _meses_do_periodo(comp)
        meses_cobertos = [m for m in meses if (cnpj, m) in cobertura]
        lastro = round(sum(lastro_mensal.get((cnpj, trib, m), 0.0) for m in meses), 2)
        excesso = round(p["valor"] - lastro, 2)
        periodos_com_pedido.update((cnpj, trib, m) for m in meses)
        if not meses_cobertos:
            situacao = SIT_SEM_EFD    # série incompleta (CB-02), não irregularidade
        elif excesso > TOL:
            situacao = SIT_SEM_LASTRO
        else:
            situacao = SIT_COM_LASTRO
        linhas.append({"cnpj": cnpj, "tributo": trib, "competencia": comp,
                       "pedido": p["valor"], "lastro_escriturado": lastro,
                       "excesso": max(excesso, 0.0), "situacao": situacao,
                       "meses_sem_efd": [m for m in meses if (cnpj, m) not in cobertura],
                       "perdcomps": sorted(set(p["numeros"]))})
        if situacao == SIT_SEM_EFD:
            achados.append(Achado(
                ref="CR-06", cnpj=cnpj, competencia=comp, tributo=trib,
                titulo=f"PER/DCOMP sem EFD da competência na base: {trib} {comp}",
                descricao=(f"Pedido de R$ {brl(p['valor'])} mas nenhuma "
                           f"EFD-Contribuições do período está na base — série "
                           f"incompleta (CB-02): obter a escrituração antes de "
                           f"avaliar o lastro."),
                valores={"compensado": p["valor"], "escriturado": 0.0},
                risco="", base_legal=BASE_LEGAL,
                acao_proposta="Baixar via ReceitaNetBX as EFDs do período e reexecutar",
                prioridade="MEDIA"))
            continue
        if situacao != SIT_SEM_LASTRO:
            continue
        achados.append(Achado(
            ref="CR-06", cnpj=cnpj, competencia=comp, tributo=trib,
            titulo=f"Pedido acima do lastro escriturado: {trib} {comp}",
            descricao=(f"PER/DCOMP pede R$ {brl(p['valor'])} × saldo credor "
                       f"escriturado no período R$ {brl(lastro)} "
                       f"(excesso R$ {brl(excesso)}). "
                       f"PER/DCOMP: {', '.join(sorted(set(p['numeros']))[:4])}."),
            valores={"compensado": p["valor"], "escriturado": lastro},
            diferenca=excesso, risco="R6", base_legal=BASE_LEGAL,
            acao_proposta=("Verificar lastro (saldos transportados/retificações) "
                           "antes de novo pedido; compensação não homologada gera "
                           "multa isolada de 50% sobre o débito"),
            prioridade="ALTA"))

    # créditos escriturados sem pedido no período (oportunidade R7)
    sem_pedido: dict = defaultdict(float)
    for (cnpj, trib, comp), v in lastro_mensal.items():
        if v > TOL and (cnpj, trib, comp) not in periodos_com_pedido:
            sem_pedido[(cnpj, trib, _trimestre(comp))] += v
    for (cnpj, trib, tri), v in sorted(sem_pedido.items()):
        v = round(v, 2)
        decadencia = _decadencia(tri)
        linhas.append({"cnpj": cnpj, "tributo": trib, "competencia": tri,
                       "pedido": 0.0, "lastro_escriturado": v, "excesso": 0.0,
                       "situacao": SIT_SEM_PEDIDO, "perdcomps": []})
        achados.append(Achado(
            ref="CR-06", cnpj=cnpj, competencia=tri, tributo=trib,
            titulo=f"Crédito escriturado sem PER/DCOMP: {trib} {tri}",
            descricao=(f"Saldo credor escriturado de R$ {brl(v)} no período sem "
                       f"pedido de ressarcimento/compensação correspondente."),
            valores={"escriturado": v, "compensado": 0.0},
            diferenca=-v, risco="R7", base_legal="CTN, arts. 165–168 (prazo de 5 anos)",
            acao_proposta=("Avaliar PER/DCOMP do saldo antes da decadência "
                           "(verificar utilização em descontos posteriores)"),
            prioridade="MEDIA", decadencia=decadencia))

    _ord = {SIT_SEM_LASTRO: 0, SIT_SEM_EFD: 1, SIT_SEM_PEDIDO: 2, SIT_COM_LASTRO: 3}
    linhas.sort(key=lambda r: (_ord[r["situacao"]], r["cnpj"], r["competencia"]))
    return linhas, achados


def _pedidos_ativos(grupos: dict, status_map: dict) -> dict:
    from audit.parsers.perdcomp import _is_cancelled, _is_retified
    ativos = {}
    for chave, g in grupos.items():
        fatos = [f for f in g["fatos"]
                 if not _is_cancelled(f["detalhes"].get("numero_perdcomp", ""), status_map)
                 and not _is_retified(f["detalhes"].get("numero_perdcomp", ""), status_map)]
        if fatos:
            ativos[chave] = {"valor": sum(f["valor"] for f in fatos), "fatos": fatos}
    return ativos


def _tributo_do_pedido(tipo_credito: str) -> str:
    t = (tipo_credito or "").upper()
    if "COFINS" in t:
        return "COFINS"
    if "PIS" in t or "PASEP" in t:
        return "PIS"
    return ""


def _meses_do_periodo(comp: str) -> list[str]:
    """'2022.1T' → ['2022.01','2022.02','2022.03'] | '2022.05' → ['2022.05']
    | '2022' → 12 meses."""
    m = re.fullmatch(r"(\d{4})\.(\d)T", comp or "")
    if m:
        ano, tri = m.group(1), int(m.group(2))
        return [f"{ano}.{mes:02d}" for mes in range(3 * tri - 2, 3 * tri + 1)]
    if re.fullmatch(r"\d{4}\.\d{2}", comp or ""):
        return [comp]
    if re.fullmatch(r"\d{4}", comp or ""):
        return [f"{comp}.{mes:02d}" for mes in range(1, 13)]
    return []


def _trimestre(comp_mensal: str) -> str:
    ano, mes = comp_mensal[:4], int(comp_mensal[5:7])
    return f"{ano}.{(mes + 2) // 3}T"


def _decadencia(tri: str) -> str:
    """'2022.1T' → '2027-03' (último mês do período + 5 anos)."""
    m = re.fullmatch(r"(\d{4})\.(\d)T", tri or "")
    if not m:
        return ""
    return f"{int(m.group(1)) + _PRAZO_DECADENCIAL_ANOS}-{3 * int(m.group(2)):02d}"
