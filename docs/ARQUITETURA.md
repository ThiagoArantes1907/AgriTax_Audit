# Plataforma AgriTax Audit — Arquitetura Geral

**Documento:** ARQ-AA-001 · **Versão:** 0.1 (PROPOSTA — aguardando aprovação do Thiago)
**Base:** Programa de Trabalho PT-AF-003 v4.0 (`Programa_Auditoria_Fiscal_AgriTax.docx`), Anexo B (mapa de automação)
**Princípios herdados dos projetos anteriores:** 100% local, sem API paga, sem custo; o sistema
instrui e evidencia — a decisão tributária é sempre do contador.

---

## 1. O que a plataforma faz

Automatiza o programa de auditoria fiscal PT-AF-003: coleta as bases oficiais (e-CAC via
procuração + ReceitaNetBX), estrutura tudo num modelo canônico, executa os cruzamentos
(CR-01 a 08), a reperformance por regime (RP-01 a 04 ou SN-01 a 14) e o inventário de
pendências (PE-01 a 05), e gera os entregáveis da seção 9 — culminando no **diagnóstico
de prospecção** em prazo mínimo.

## 2. Decisões estruturais propostas

| # | Decisão | Justificativa |
|---|---------|---------------|
| D1 | **Monorepo novo em `D:\AgriTax_Audit04`**, pacote `audit/` | A plataforma é o produto; os sistemas existentes viram motores acoplados |
| D2 | **Sistemas existentes NÃO são copiados — são integrados como dependências** via camada `audit/integracao/` | O PIS/COFINS (`C:\Projetos\PIS COFINS COM GRAOS`) continua evoluindo no próprio repo; a plataforma o chama para RP-01 (Bloco M), cruzamento ICMS×Contribuições e retificadoras. Idem para o repo dos robôs/parsers e-CAC |
| D3 | **Modelo canônico `FatoFiscal`**: (CNPJ, tributo/código receita, competência, fonte) → valores escriturado / declarado / pago / compensado | Todos os cruzamentos CR viram comparações de vetores sobre a mesma chave — um motor genérico, N cruzamentos configurados |
| D4 | **Modelo `Achado`** espelha a matriz de entregáveis: ref (CR-04…), competência, valores divergentes, risco (R1–R9/S1–S8), base legal, ação proposta, campo de decisão do cliente | A planilha de decisão sai direto do modelo, como no sistema PIS/COFINS |
| D5 | **Armazenamento: arquivos brutos imutáveis + SQLite por engajamento** (`engajamento.db`) + manifesto de custódia (`manifest.json` com hash SHA-256, origem, data-base) | Atende CB-03 (cadeia de custódia); SQLite permite os cruzamentos por SQL sem servidor |
| D6 | **Engajamento como unidade de trabalho**: `engajamentos/<cliente>/<CNPJ>/` com `parametros.yaml` (ficha do Anexo A), `raw/`, `db/`, `achados/`, `entregaveis/` | Multi-cliente e multi-CNPJ desde o início (lição do pacote DANTAS: 1 pacote = N empresas) |
| D7 | **Regime define o plano de execução**: perfil Real / Presumido / Simples / SIMEI seleciona os módulos (P6 do programa); mudança de regime no período segmenta por exercício | Regra explícita do PT-AF-003 |
| D8 | **CLI `pipeline.py` com fases nomeadas como no programa**: `coletar → estruturar → cruzar → reperformar → pendencias → relatorio` | Mesmo padrão de orquestração validado no projeto PIS/COFINS |

## 3. Estrutura de pastas

```
D:\AgriTax_Audit04\
├── pipeline.py                  # CLI orquestrador (fases acima, por engajamento)
├── audit/
│   ├── core/                    # modelo (FatoFiscal, Achado, Engajamento), domínio
│   │   │                        #   fiscal (códigos de receita, anexos LC 123, tabelas),
│   │   │                        #   custódia (hash/manifesto), config
│   ├── coleta/                  # CB-01..06
│   │   ├── ecac/                # robôs Selenium (integrar existentes + novos)
│   │   ├── bx/                  # automação ReceitaNetBX (hoje manual)
│   │   └── integridade.py       # completude da série por competência (CB-02)
│   ├── parsers/                 # cada fonte → FatoFiscal/tabelas SQLite
│   │   ├── (integrar prontos: PERDCOMP, DARF, DCTF, DCTFWeb, PGDAS-D,
│   │   │    EFD-Contribuições, ECD, EFD-ICMS, DFe, EFD-Reinf)
│   │   └── (novos: ECF, Situação Fiscal, DEFIS, DTE)
│   ├── cruzamentos/             # CR-01..08 — motor genérico + configuração por cruzamento
│   ├── reperformance/
│   │   ├── rp/                  # RP-01..04 (Real/Presumido; RP-01 delega ao motor PIS/COFINS)
│   │   └── sn/                  # SN-01..14 (Simples: RBT12 rolling, anexos, fator r,
│   │                            #   segregação monofásico/ST por NCM)
│   ├── pendencias/              # PE-01..05 (classificação com prazos e prioridade)
│   ├── entregaveis/             # matriz de achados, relatório, mapa de créditos c/
│   │                            #   decadência, plano de regularização, papéis de trabalho
│   └── integracao/              # adaptadores para os repos externos (D2)
├── engajamentos/                # dados por cliente (fora do git)
├── docs/                        # este documento, decisões, uso
└── tests/                       # fixtures sintéticos por fonte + regressão com casos reais
```

## 4. Fluxo de dados

```
e-CAC (Selenium) ─┐
                  ├─► raw/ (imutável + manifest hash/data-base)
ReceitaNetBX ─────┘        │
                           ▼
                   parsers → SQLite (fatos, documentos, saldos)
                           │
        ┌──────────────────┼───────────────────┐
        ▼                  ▼                   ▼
  cruzamentos CR     reperformance RP/SN   pendências PE
        └──────────────────┼───────────────────┘
                           ▼
                     Achados (ref, risco, base legal, valores, decadência)
                           ▼
              entregáveis (matriz decisória → decisão do contador → plano)
```

## 5. Integração com os ativos existentes

| Ativo | Local | Papel na plataforma |
|---|---|---|
| Sistema PIS/COFINS grãos | `C:\Projetos\PIS COFINS COM GRAOS` | Motor de RP-01 (Bloco M), CR-03 (ICMS×Contribuições), retificadoras E10 |
| **AgriTax Audit v5 consolidado** (`agritax_audit_consolidado.py`, ~22.400 linhas, Tkinter) | `D:\AgriTax_Audit05_extraido\AgriTax_Audit05` (zip recebido 2026-07-22) | Fonte dos módulos "prontos" do Anexo B — ver inventário abaixo |
| PERDCOMP Extractor v5.0 | idem (e cópia em `D:\AgriTax_Audit04`) | Parser PER/DCOMP para CR-06/PE-04 |

### 5.1 Inventário do AgriTax Audit v5 consolidado (monólito)

**Parsers prontos** (funções `parse_*/extract_*`, independentes da GUI — extração viável):
PER/DCOMP (PDF), DARF/DAS, DCTF (PDF com fallback OCR), PGDAS-D (2 layouts do e-CAC),
DCTFWeb (XML e PDF), EFD-Contribuições (SPED txt), ECD (com plano de contas, balancete,
diário, razão, BP, DRE e validação).

**Motores de cruzamento prontos** (base dos CR-04/05/06):
- `run_confronto_efd_dctf` — EFD × DCTF + DCTFWeb (situações: conforme / divergente / só EFD / só declarado);
- `run_conciliacao` — DARF × DCOMP (duplo pagamento, divergência);
- `run_triplo_dctf_darf_dcomp` — confissão (DCTF + DCTFWeb + Simples) × quitação (DARF + DCOMP)
  — o "motor de conciliação de 6 vias" do Anexo B.

**Robôs Selenium e-CAC prontos**: `PerdcompDownloader`, `DctfDownloader`, `DctfWebDownloader`,
`DarfDownloader`, `SimplesNacionalDownloader`, com `_DownloadManifest` (rastreio de downloads).

**Limitações do monólito** (o que a plataforma resolve):
- `DataStore` é 100% em memória — sem persistência entre sessões (a plataforma introduz o
  SQLite por engajamento, D5);
- Tudo acoplado à GUI Tkinter num único arquivo — a extração para `audit/` separa parsing,
  cruzamento e download da interface;
- Sem cadeia de custódia com hash/data-base (CB-03) — o `_DownloadManifest` é um embrião.

**Estratégia de integração revisada:** diferente do sistema PIS/COFINS (que permanece como
dependência externa), o monólito v5 será **absorvido e modularizado** dentro de `audit/` —
os parsers viram `audit/parsers/*`, os motores viram `audit/cruzamentos/*`, os downloaders
viram `audit/coleta/ecac/*`, mantendo a GUI original funcional até a paridade da CLI.

## 6. Sequência de marcos proposta (um por vez, com aprovação)

| Marco | Entrega | Cobre |
|---|---|---|
| M0 | Scaffold + core: modelos FatoFiscal/Achado/Engajamento, ficha Anexo A em YAML, custódia (hash/manifesto), CLI esqueleto | CB-03, base de tudo |
| M1 | Inventário e integração dos ativos existentes (após receber os caminhos); ingestão para SQLite | CB-06 |
| M2 | CR-04/05 ponta a ponta (escriturado × DCTF/DCTFWeb × DARF) | Prioridade 2 do Anexo B |
| M3 | Parser Situação Fiscal + DTE → PE-01/02 | Prioridade 3 |
| M4 | Parser ECF + automação BX | Prioridade 1 (a mais pesada — depois de gerar valor com M2/M3) |
| M5 | Módulo Simples: SN-01, 08, 09, 11 | Prioridade 4 |
| M6 | Demais cruzamentos (CR-01/02/03/07/08) + RP-02/03/04 | Completude |
| M7 | Entregáveis com marca AgriTax (diagnóstico de prospecção) | Seção 9 |

**Nota sobre M4 × prioridade do documento:** o Anexo B sugere BX+ECF primeiro, mas ele depende
de automação de aplicativo desktop (ReceitaNetBX) e do parser mais complexo (ECF). Proponho
inverter: M2/M3 usam parsers já prontos e geram achados de prospecção imediatos com arquivos
baixados manualmente. Decisão do Thiago.

## 7. O que fica fora (por ora)

- Interface gráfica/web — CLI primeiro, como no PIS/COFINS; GUI depois que o motor estiver validado.
- Banco central multi-cliente — um SQLite por engajamento basta e simplifica sigilo/LGPD.
- Qualquer chamada a API paga.
