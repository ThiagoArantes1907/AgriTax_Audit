"""Cadeia de custódia (CB-03): hash SHA-256, origem e data-base de cada arquivo.

Cada engajamento tem um `manifest.json` ao lado do `raw/`. Todo arquivo coletado
(e-CAC, ReceitaNetBX ou entrega manual) é registrado ANTES de ser processado;
os saldos do e-CAC mudam diariamente, então a data-base da consulta faz parte
da evidência (seção 5 do PT-AF-003).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

MANIFEST = "manifest.json"
CANAIS = ("ECAC", "RECEITANETBX", "MANUAL")


def sha256_arquivo(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for bloco in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


def _manifest_path(engaj_dir: str | Path) -> Path:
    return Path(engaj_dir) / MANIFEST


def carregar_manifest(engaj_dir: str | Path) -> list[dict]:
    p = _manifest_path(engaj_dir)
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def registrar_arquivo(engaj_dir: str | Path, arquivo: str | Path, canal: str,
                      data_base: str = "", descricao: str = "") -> dict:
    """Registra um arquivo no manifesto. Idempotente por (nome, sha256).

    data_base: data de referência da consulta no e-CAC (AAAA-MM-DD); para
    escriturações BX pode ficar vazia (o conteúdo é imutável após transmissão).
    """
    if canal not in CANAIS:
        raise ValueError(f"canal inválido: {canal!r} (use {CANAIS})")
    arquivo = Path(arquivo)
    if not arquivo.exists():
        raise FileNotFoundError(str(arquivo))

    entrada = {
        "arquivo": arquivo.name,
        "caminho_relativo": _relativo_seguro(arquivo, engaj_dir),
        "sha256": sha256_arquivo(arquivo),
        "tamanho_bytes": arquivo.stat().st_size,
        "canal": canal,
        "data_base": data_base,
        "descricao": descricao,
        "registrado_em": datetime.now().isoformat(timespec="seconds"),
    }

    manifest = carregar_manifest(engaj_dir)
    ja_existe = any(m["arquivo"] == entrada["arquivo"] and m["sha256"] == entrada["sha256"]
                    for m in manifest)
    if not ja_existe:
        manifest.append(entrada)
        _manifest_path(engaj_dir).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return entrada


def _relativo_seguro(arquivo: Path, engaj_dir: str | Path) -> str:
    try:
        return str(arquivo.resolve().relative_to(Path(engaj_dir).resolve()))
    except ValueError:
        return str(arquivo)


def verificar_integridade(engaj_dir: str | Path) -> list[dict]:
    """Reconfere o hash de cada arquivo do manifesto.

    Retorna a lista de problemas: [{"arquivo", "problema": "AUSENTE"|"HASH_DIVERGENTE"}].
    Lista vazia = íntegro.
    """
    problemas = []
    base = Path(engaj_dir)
    for m in carregar_manifest(engaj_dir):
        p = base / m["caminho_relativo"]
        if not p.exists():
            p = Path(m["caminho_relativo"])  # registrado fora do engajamento
        if not p.exists():
            problemas.append({"arquivo": m["arquivo"], "problema": "AUSENTE"})
        elif sha256_arquivo(p) != m["sha256"]:
            problemas.append({"arquivo": m["arquivo"], "problema": "HASH_DIVERGENTE"})
    return problemas
