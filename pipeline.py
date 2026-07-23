#!/usr/bin/env python3
"""AgriTax Audit — orquestrador do programa de auditoria fiscal PT-AF-003.

Fases (seção 7 do programa):
    novo         cria um engajamento (ficha do Anexo A)
    coletar      CB-01..05 — downloads e-CAC/BX + registro de custódia   [M4]
    estruturar   CB-06 — parsers → SQLite                                 [M1]
    cruzar       CR-01..08 — cruzamentos estruturais                      [M2]
    reperformar  RP-01..04 ou SN-01..14 conforme o regime                 [M5+]
    pendencias   PE-01..05 — situação fiscal, DTE, parcelamentos          [M3]
    relatorio    entregáveis da seção 9                                   [M7]
    status       resumo do engajamento (custódia + banco + fases)

Uso:
    python pipeline.py novo --cliente ACME --cnpj 00.000.000/0001-00
    python pipeline.py status --cliente ACME --cnpj 00000000000100
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")   # console Windows (cp1252) × acentos
    sys.stderr.reconfigure(encoding="utf-8")

from audit.core import config, custodia, db
from audit.core.modelo import normaliza_cnpj

BASE_ENGAJAMENTOS = Path(__file__).parent / "engajamentos"

# fase → marco em que será implementada (docs/ARQUITETURA.md §6)
_FASES_PENDENTES = {
    "coletar": "M4 (robôs e-CAC integrados + automação BX)",
    "pendencias": "M3 (parser Situação Fiscal + DTE)",
    "relatorio": "M7 (entregáveis com marca AgriTax)",
}


def _engaj_dir(args) -> Path:
    engaj = BASE_ENGAJAMENTOS / args.cliente / normaliza_cnpj(args.cnpj)
    if not engaj.exists():
        sys.exit(f"Engajamento não encontrado: {engaj}\n"
                 f"Crie com: python pipeline.py novo --cliente {args.cliente!r} --cnpj {args.cnpj}")
    return engaj


def cmd_novo(args) -> None:
    engaj = config.criar_engajamento(BASE_ENGAJAMENTOS, args.cliente, args.cnpj)
    db.conectar(engaj).close()
    print(f"Engajamento criado: {engaj}")
    print(f"Preencha a ficha (Anexo A): {engaj / config.PARAMETROS}")


def cmd_status(args) -> None:
    engaj = _engaj_dir(args)
    params = config.carregar_parametros(engaj)
    manifest = custodia.carregar_manifest(engaj)
    problemas = custodia.verificar_integridade(engaj)
    con = db.conectar(engaj)
    res = db.resumo(con)
    con.close()

    print(f"Engajamento : {params.get('cliente')} / {params.get('cnpj')}")
    print(f"Período     : {params.get('periodo', {}).get('inicio') or '?'} a "
          f"{params.get('periodo', {}).get('fim') or '?'}")
    regimes = config.regimes_do_periodo(params)
    print(f"Regimes     : {', '.join(sorted(regimes)) or '(preencher regime_por_exercicio)'}")
    print(f"Custódia    : {len(manifest)} arquivo(s) registrados; "
          f"{'ÍNTEGRA' if not problemas else f'{len(problemas)} PROBLEMA(S): {problemas}'}")
    if res["fatos_por_fonte"]:
        print("Fatos       : " + ", ".join(f"{k}={v}" for k, v in res["fatos_por_fonte"].items()))
    else:
        print("Fatos       : (nenhum — rode 'estruturar' quando disponível)")
    if res["achados_por_ref"]:
        print("Achados     : " + ", ".join(f"{k}={v}" for k, v in res["achados_por_ref"].items()))


def cmd_estruturar(args) -> None:
    from audit.parsers.central import estruturar_engajamento
    engaj = _engaj_dir(args)
    res = estruturar_engajamento(engaj, data_base=args.data_base)
    for p in res["processados"]:
        print(f"  ✓ {p['arquivo']}  [{p['tipo']}]  {p['linhas']} linha(s) → {p['fatos']} fato(s)")
    for i in res["ignorados"]:
        print(f"  - {i['arquivo']}  ignorado: {i['motivo']}")
    for e in res["erros"]:
        print(f"  ✗ {e['arquivo']}  ERRO: {e['erro']}")
    print(f"Total: {len(res['processados'])} arquivo(s), {res['fatos_gravados']} fato(s) gravados; "
          f"{len(res['erros'])} erro(s).")
    if res["erros"]:
        sys.exit(1)


def cmd_cruzar(args) -> None:
    from audit.cruzamentos.executor import cruzar_engajamento
    engaj = _engaj_dir(args)
    resumo = cruzar_engajamento(engaj)
    total_achados = 0
    for ref in ("CR-04", "CR-05"):
        r = resumo.get(ref, {})
        sits = ", ".join(f"{k}: {v}" for k, v in r.get("por_situacao", {}).items())
        print(f"{ref}: {r.get('linhas', 0)} linha(s) → {r.get('achados', 0)} achado(s)"
              + (f"  [{sits}]" if sits else ""))
        total_achados += r.get("achados", 0)
    if resumo.get("status_perdcomp"):
        print(f"Planilha de status e-CAC: {resumo['status_perdcomp']} PER/DCOMP mapeados")
    print(f"Matriz completa em: {engaj / 'achados'}  |  {total_achados} achado(s) no banco")


def cmd_reperformar(args) -> None:
    from audit.reperformance.executor import reperformar_engajamento
    engaj = _engaj_dir(args)
    resumo = reperformar_engajamento(engaj)
    if "SN" in resumo:
        print(f"SN: {resumo['SN']}")
    else:
        print(f"Apurações PGDAS-D: {resumo.get('apuracoes', 0)}")
        for ref in ("SN-01", "SN-02", "SN-04", "SN-11"):
            print(f"  {ref}: {resumo.get(ref, 0)} achado(s)")
    print("RP (Real/Presumido): chega no M6.")


def cmd_pendente(fase: str):
    def _run(args) -> None:
        _engaj_dir(args)  # valida que o engajamento existe
        sys.exit(f"Fase '{fase}' ainda não implementada — chega no marco {_FASES_PENDENTES[fase]}.")
    return _run


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="pipeline.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def _com_engajamento(nome, func, ajuda):
        p = sub.add_parser(nome, help=ajuda)
        p.add_argument("--cliente", required=True)
        p.add_argument("--cnpj", required=True)
        p.set_defaults(func=func)
        return p

    _com_engajamento("novo", cmd_novo, "cria um engajamento (ficha do Anexo A)")
    _com_engajamento("status", cmd_status, "resumo do engajamento")
    p_est = _com_engajamento("estruturar", cmd_estruturar,
                             "CB-06: raw/** → custódia → parsers → SQLite")
    p_est.add_argument("--data-base", default="",
                       help="data-base das extrações e-CAC (AAAA-MM-DD)")
    _com_engajamento("cruzar", cmd_cruzar,
                     "CR-04/05: escriturado × declarado × pago/compensado")
    _com_engajamento("reperformar", cmd_reperformar,
                     "SN-01/02/04/11 (Simples); RP no M6")
    for fase, marco in _FASES_PENDENTES.items():
        _com_engajamento(fase, cmd_pendente(fase), f"[pendente — {marco}]")

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
