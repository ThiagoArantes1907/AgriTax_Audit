"""Domínio fiscal do PT-AF-003: procedimentos, riscos e regimes.

Fonte única das referências usadas em Achado.ref e Achado.risco — espelha as
seções 4 (procedimentos) e 6 (riscos) do Programa de Trabalho v4.0.
"""

REGIMES = ("LUCRO_REAL", "LUCRO_PRESUMIDO", "SIMPLES_NACIONAL", "SIMEI")

# Seção 6.1 — riscos gerais (todos os regimes)
RISCOS_GERAIS = {
    "R1": "Escriturado > confessado (cobrança automática + multa isolada)",
    "R2": "Confessado > pago ou DARF mal alocado (saldo devedor + encargos)",
    "R3": "Omissão de competências (multa por omissão; agrava malha)",
    "R4": "Enquadramento indevido de receitas (tributo + multa de ofício de 75%)",
    "R5": "Créditos sem pertinência técnica (glosa em fiscalização)",
    "R6": "Compensações sem lastro/não homologadas (multa isolada de 50% + débito)",
    "R7": "Créditos não usados com decadência próxima (oportunidade — perda definitiva)",
    "R8": "Pendências ativas ignoradas (dívida ativa, perda de CND)",
    "R9": "Retificadoras mal sequenciadas (cobrança indevida ou supressão de crédito)",
}

# Seção 6.2 — riscos específicos do Simples Nacional
RISCOS_SIMPLES = {
    "S1": "Estouro de limite/sublimite sem comunicação (exclusão retroativa)",
    "S2": "Anexo errado ou fator r mal calculado (DAS a menor ou a maior)",
    "S3": "Exclusão de ofício por débitos (perda do regime; prazo do DTE)",
    "S4": "Falta de segregação monofásico/ST (pagamento em duplicidade → restituição)",
    "S5": "Segregação sem amparo (multa qualificada 75%–150%)",
    "S6": "DEFIS inconsistente com PGDAS-D (malha; distribuição de lucros)",
    "S7": "Lucros acima da presunção sem ECD (tributação na PF do sócio)",
    "S8": "Omissão × meios de pagamento — e-Financeira (monitorar malha via DTE)",
}

RISCOS = {**RISCOS_GERAIS, **RISCOS_SIMPLES}

# Seção 4 — procedimentos por bloco (ref → descrição curta)
PROCEDIMENTOS = {
    # 4.1 Coleta e integridade
    "CB-01": "Baixar escriturações via ReceitaNetBX (originais e retificadoras)",
    "CB-02": "Completude da série por competência obrigatória",
    "CB-03": "Hash, log de download e data-base (cadeia de custódia)",
    "CB-04": "Extrações e-CAC (situação fiscal, DTE, processos, DCTF, DARF, PER/DCOMP)",
    "CB-05": "Cadastro: CNPJ, CNAEs, sócios, histórico Simples/SIMEI",
    "CB-06": "Carga em ambiente estruturado, original × retificadora",
    # 4.2 Cruzamentos estruturais
    "CR-01": "ECD × ECF (resultado, referenciais, e-Lalur)",
    "CR-02": "EFD-Contribuições × ECD (receitas)",
    "CR-03": "EFD-Contribuições × EFD-ICMS/IPI (documento a documento)",
    "CR-04": "Escriturações × DCTF/DCTFWeb",
    "CR-05": "DCTF/DCTFWeb × DARFs (código, período, valor)",
    "CR-06": "PER/DCOMP × saldos de origem",
    "CR-07": "Situação Fiscal/DTE × achados próprios",
    "CR-08": "Retificadoras × versão anterior, por competência",
    # 4.3 Reperformance Real/Presumido
    "RP-01": "PIS/COFINS: recalcular apuração (blocos A/C/D/F e M)",
    "RP-02": "IRPJ/CSLL: reexecutar a ECF",
    "RP-03": "Saldos credores e negativos × PER/DCOMP × decadência",
    "RP-04": "EFD-ICMS/IPI: consistência interna",
    # 4.4 Simples Nacional
    "SN-01": "RBT12 rolling × limite R$ 4,8 mi",
    "SN-02": "Sublimites estaduais (R$ 3,6 mi)",
    "SN-03": "Anexo aplicado × CNAE",
    "SN-04": "Fator r (folha/receita ≥ 28%)",
    "SN-05": "Causas de exclusão de ofício",
    "SN-06": "PGDAS-D mensal × DEFIS anual",
    "SN-07": "Saídas EFD-ICMS/IPI × receita PGDAS-D",
    "SN-08": "Segregação de monofásicos PIS/COFINS",
    "SN-09": "Segregação de ICMS-ST",
    "SN-10": "Segregação sem amparo (inverso)",
    "SN-11": "DAS declarado × DAS pago",
    "SN-12": "Restituição/compensação do Simples",
    "SN-13": "Distribuição de lucros × ECD/presunção",
    "SN-14": "Retenções (DCTFWeb/EFD-Reinf)",
    # 4.5 Pendências e exigibilidade
    "PE-01": "Classificar pendências da Situação Fiscal",
    "PE-02": "DTE e e-Processo (intimações, prazos, Termo de Exclusão)",
    "PE-03": "Parcelamentos e transação (adimplência, rescisão)",
    "PE-04": "Despachos de PER/DCOMP (não homologadas, créditos deferidos)",
    "PE-05": "Dívida ativa (inscrições, CND, exclusão do Simples)",
}

FASES_PIPELINE = ("coletar", "estruturar", "cruzar", "reperformar", "pendencias", "relatorio")


def valida_ref(ref: str) -> str:
    if ref not in PROCEDIMENTOS:
        raise ValueError(f"referência de procedimento desconhecida: {ref!r}")
    return ref


def valida_risco(risco: str) -> str:
    if risco and risco not in RISCOS:
        raise ValueError(f"risco desconhecido: {risco!r}")
    return risco
