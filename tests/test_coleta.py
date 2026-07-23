"""Testes do M4 (coletar): validações e cópia central → engajamento.

Os robôs Selenium em si exigem Chrome + sessão e-CAC logada (não testável
aqui); a lógica deles é a do v5, validada em produção. Testa-se o executor.
"""
import pytest

from audit.coleta import executor
from audit.coleta.ecac import _infra
from audit.core import config

CNPJ = "04.461.884/0001-32"


def test_modulo_invalido(tmp_path):
    engaj = config.criar_engajamento(tmp_path, "T", CNPJ)
    r = executor.coletar_engajamento(engaj, CNPJ, modulos=["nao_existe"])
    assert "desconhecido" in r["erro"]


def test_porta_fechada_da_instrucao(tmp_path, monkeypatch):
    engaj = config.criar_engajamento(tmp_path, "T", CNPJ)
    monkeypatch.setattr(executor, "SELENIUM_OK", True)
    monkeypatch.setattr(executor, "REQUESTS_OK", True)
    monkeypatch.setattr(executor, "_dl_is_debug_port_open", lambda porta: False)
    r = executor.coletar_engajamento(engaj, CNPJ, modulos=["darf"], porta=59999)
    assert "59999" in r["erro"] and "remote-debugging-port" in r["erro"]


def test_dependencias_ausentes(tmp_path, monkeypatch):
    engaj = config.criar_engajamento(tmp_path, "T", CNPJ)
    monkeypatch.setattr(executor, "SELENIUM_OK", False)
    r = executor.coletar_engajamento(engaj, CNPJ, modulos=["darf"])
    assert "pip install" in r["erro"]


def test_copiar_para_engajamento(tmp_path, monkeypatch):
    engaj = config.criar_engajamento(tmp_path, "T", CNPJ)
    # estrutura central fake: <root>/04461884/darf com 2 PDFs + 1 manifesto
    central = tmp_path / "central"
    darf_dir = central / "04461884" / "darf"
    darf_dir.mkdir(parents=True)
    (darf_dir / "darf_a.pdf").write_bytes(b"%PDF a")
    (darf_dir / "darf_b.pdf").write_bytes(b"%PDF b")
    (darf_dir / "_manifest.json").write_text("{}")   # extensão fora da lista? .json
    monkeypatch.setattr(_infra, "_dl_get_root_dir", lambda: central)
    monkeypatch.setattr(executor, "_dl_get_company_paths",
                        _infra._dl_get_company_paths)

    n = executor._copiar_para_engajamento(engaj, "04461884000132", ["darf"],
                                          log=lambda *a: None)
    assert n == 2
    destino = engaj / "raw" / "ecac" / "darf"
    assert sorted(p.name for p in destino.iterdir()) == ["darf_a.pdf", "darf_b.pdf"]

    # idempotente: segunda cópia não duplica
    n2 = executor._copiar_para_engajamento(engaj, "04461884000132", ["darf"],
                                           log=lambda *a: None)
    assert n2 == 0
