# AgriTax Audit — Plataforma de Auditoria Fiscal

Automação do Programa de Trabalho **PT-AF-003 v4.0** (auditoria fiscal com fontes
exclusivas e-CAC + ReceitaNetBX). Arquitetura em [docs/ARQUITETURA.md](docs/ARQUITETURA.md).

100% LOCAL · SEM API PAGA · A decisão tributária é sempre do contador.

## Uso

```
pip install -r requirements.txt

python pipeline.py novo     --cliente ACME --cnpj 00.000.000/0001-00
python pipeline.py coletar  --cliente ACME --cnpj ... --abrir-chrome   # login manual no e-CAC
python pipeline.py estruturar --cliente ACME --cnpj ... --data-base 2026-07-23
python pipeline.py cruzar --cliente ACME --cnpj ...
python pipeline.py reperformar --cliente ACME --cnpj ...
python pipeline.py relatorio --cliente ACME --cnpj ...
python pipeline.py status  --cliente ACME --cnpj ...
```

Fases do pipeline (seção 7 do programa): `coletar → estruturar → cruzar →
reperformar → pendencias → relatorio` — habilitadas marco a marco (§6 da arquitetura).

## Estrutura

```
audit/core          modelo canônico (FatoFiscal/Achado), domínio (CR/RP/SN/PE, riscos),
                    custódia (hash/manifesto — CB-03), ficha Anexo A, SQLite por engajamento
audit/coleta        CB-04: robôs Selenium do e-CAC (PER/DCOMP, DCTF,
                    DCTFWeb, DARF, Simples) via Chrome debug + login
                    manual; cópia p/ raw/ecac · BX ainda manual        [OK]
audit/parsers       fontes → fatos: PER/DCOMP, DARF/DAS, DCTF (OCR),
                    DCTFWeb, PGDAS-D, EFD-Contribuições, ECD (do v5,
                    com layouts corrigidos) + ECF (novo, IRPJ/CSLL por
                    período) + central de estruturação                    [OK]
audit/cruzamentos   CR-01 (ECD×ECF), CR-04 (EFD+ECF × DCTF/DCTFWeb),
                    CR-05 (confissão × quitação, 6 vias), CR-06
                    (pedido × lastro) e CR-08 (retificadoras)          [OK]
audit/reperformance sn/ Simples (SN-01/02/04/11) · rp/ RP-02: reexecução
                    da ECF (presunção mínima, 15%+adicional, CSLL 9%,
                    trava de 30%)                                       [OK]
audit/pendencias    PE-01..05                                           [M3]
audit/entregaveis   matriz decisória de achados (Excel, com campo de
                    decisão do contador); relatório PDF no M7 final     [parcial]
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
