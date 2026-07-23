"""CR-04 — Escriturações × DCTF/DCTFWeb (confronto EFD, lógica do v5).

Compara, por (CNPJ, código-base, competência):
    EFD-Contribuições (débito apurado no Bloco M)
  × DCTF clássica (débito apurado) + DCTFWeb (saldo a pagar, líquido de deduções)

Situações (v5): Conforme | Divergente | Só na EFD | Só na DCTF/DCTFWeb.
Divergência com escriturado > confessado = risco R1 (cobrança automática +
multa isolada — é o que a malha da RFB detecta primeiro).
"""
from __future__ import annotations

import sqlite3

from audit.core.modelo import Achado

from .motor import TOL, brl, carregar_lado, soma_detalhe

SIT_OK = "Conforme"
SIT_DIVERG = "Divergente"
SIT_SO_EFD = "Só na EFD"
SIT_SO_DECL = "Só na DCTF/DCTFWeb"

BASE_LEGAL = "IN RFB 2.005/2021; Guia Prático EFD-Contribuições"


def run(con: sqlite3.Connection) -> tuple[list[dict], list[Achado]]:
    """Retorna (linhas do confronto — todas, inclusive conformes; achados).

    Lado escriturado = EFD-Contribuições (PIS/COFINS, Bloco M) + ECF
    (IRPJ/CSLL apurados) — "Escriturações × DCTF/DCTFWeb" do PT-AF-003.
    As chaves não colidem (códigos de receita distintos por tributo).
    """
    efd = carregar_lado(con, "EFD_CONTRIBUICOES", "ESCRITURADO",
                        apenas_arquivo_ativo=True)
    for chave, grupo in carregar_lado(con, "ECF", "ESCRITURADO",
                                      apenas_arquivo_ativo=True).items():
        efd.setdefault(chave, grupo)
    dctf = carregar_lado(con, "DCTF", "DECLARADO")
    dctfweb = carregar_lado(con, "DCTFWEB", "DECLARADO")

    linhas, achados = [], []
    for chave in sorted(set(efd) | set(dctf) | set(dctfweb)):
        cnpj, cod, comp = chave
        g_efd, g_dctf, g_web = efd.get(chave), dctf.get(chave), dctfweb.get(chave)

        # lados como no v5: EFD usa débito apurado; DCTFWeb usa saldo a pagar
        efd_debito = soma_detalhe(g_efd, "debito_apurado")
        if g_efd and efd_debito == 0.0:
            efd_debito = g_efd["valor"]  # fallback: contribuição a recolher
        dctf_debito = g_dctf["valor"] if g_dctf else 0.0
        web_debito = soma_detalhe(g_web, "saldo_pagar")
        if g_web and web_debito == 0.0:
            web_debito = g_web["valor"]
        total_decl = dctf_debito + web_debito

        diferenca = round(efd_debito - total_decl, 2)
        if efd_debito > 0 and total_decl == 0:
            situacao = SIT_SO_EFD
        elif efd_debito == 0 and total_decl > 0:
            situacao = SIT_SO_DECL
        elif abs(diferenca) <= TOL:
            situacao = SIT_OK
        else:
            situacao = SIT_DIVERG

        primeiro = ((g_efd or g_dctf or g_web)["fatos"][0])
        tributo = primeiro.get("tributo", "")

        linhas.append({
            "cnpj": cnpj, "codigo_receita": cod, "competencia": comp,
            "tributo": tributo, "efd_debito": efd_debito,
            "dctf_debito": dctf_debito, "dctfweb_debito": web_debito,
            "total_declarado": total_decl, "diferenca": diferenca,
            "situacao": situacao,
        })

        if situacao == SIT_OK:
            continue
        escriturado_maior = diferenca > 0
        achados.append(Achado(
            ref="CR-04", cnpj=cnpj, competencia=comp, tributo=tributo,
            titulo=f"{situacao}: {tributo or cod} {comp}",
            descricao=(f"EFD R$ {brl(efd_debito)} × declarado R$ {brl(total_decl)} "
                       f"(DCTF R$ {brl(dctf_debito)} + DCTFWeb R$ {brl(web_debito)}). "
                       f"Δ R$ {brl(abs(diferenca))}. Código {cod}."),
            valores={"escriturado": efd_debito, "declarado_dctf": dctf_debito,
                     "declarado_dctfweb": web_debito},
            diferenca=diferenca,
            risco="R1" if escriturado_maior else "",
            base_legal=BASE_LEGAL,
            acao_proposta=("Retificar DCTF/DCTFWeb para confessar o débito escriturado "
                           "(antes de cobrança automática)" if escriturado_maior else
                           "Verificar escrituração × declaração (possível retificação "
                           "pendente ou débito declarado sem lastro escriturado)"),
            prioridade="ALTA" if escriturado_maior else "MEDIA",
        ))

    _ord = {SIT_DIVERG: 0, SIT_SO_EFD: 1, SIT_SO_DECL: 2, SIT_OK: 3}
    linhas.sort(key=lambda r: (_ord[r["situacao"]], r["cnpj"], r["competencia"],
                               r["codigo_receita"]))
    return linhas, achados
