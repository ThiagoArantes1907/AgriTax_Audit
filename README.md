# AgriTax Audit — Plataforma de Auditoria Fiscal

Automação do Programa de Trabalho **PT-AF-003 v4.0** (auditoria fiscal com fontes
exclusivas e-CAC + ReceitaNetBX). Arquitetura em [docs/ARQUITETURA.md](docs/ARQUITETURA.md).

100% LOCAL · SEM API PAGA · A decisão tributária é sempre do contador.

## Uso

```
pip install -r requirements.txt

python pipeline.py novo    --cliente ACME --cnpj 00.000.000/0001-00
python pipeline.py status  --cliente ACME --cnpj 00000000000100
```

Fases do pipeline (seção 7 do programa): `coletar → estruturar → cruzar →
reperformar → pendencias → relatorio` — habilitadas marco a marco (§6 da arquitetura).

## Estrutura

```
audit/core          modelo canônico (FatoFiscal/Achado), domínio (CR/RP/SN/PE, riscos),
                    custódia (hash/manifesto — CB-03), ficha Anexo A, SQLite por engajamento
audit/coleta        CB — robôs e-CAC (Selenium) e ReceitaNetBX          [M4]
audit/parsers       fontes → fatos: PER/DCOMP, DARF/DAS, DCTF (OCR),
                    DCTFWeb, PGDAS-D, EFD-Contribuições, ECD (absorvidos
                    do AgriTax Audit v5) + central de estruturação        [OK]
audit/cruzamentos   CR-01..08                                           [M2]
audit/reperformance rp/ (Real/Presumido) · sn/ (Simples Nacional)       [M5+]
audit/pendencias    PE-01..05                                           [M3]
audit/entregaveis   matriz de achados, relatório, mapa de créditos      [M7]
audit/integracao    adaptadores (sistema PIS/COFINS grãos)
engajamentos/       dados por cliente/CNPJ (fora do git)
```

## Testes

```
python -m pytest tests/ -q
```

## Ferramenta legada nesta pasta

`perdcomp_extractor.py` — PERDCOMP Extractor v5.0 (GUI standalone). Será absorvido
por `audit/parsers` no M1; até lá continua utilizável: `python perdcomp_extractor.py`.
