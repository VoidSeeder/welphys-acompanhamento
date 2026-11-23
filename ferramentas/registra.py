#!/usr/bin/env python3
"""Publica títulos de commits e notas curtas no feed da página de acompanhamento."""

import os
import re
import subprocess
import unicodedata
from pathlib import Path

ARQUIVO_BLOQUEIOS = "acompanhamento-bloqueios"
MARCADOR = re.compile(r"^Acompanhamento:\s*n[ãa]o\s*$", re.IGNORECASE | re.MULTILINE)


class BloqueiosAusentes(Exception):
    """A lista local de termos bloqueados não existe; nada pode ser publicado."""


def _git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _dir_comum(repo):
    r = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if r.returncode != 0:
        raise OSError(f"não é um repositório Git: {repo}")
    return Path(r.stdout.strip())


def normaliza(texto):
    decomposto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c)).casefold()


def carregar_bloqueios(repo):
    caminho = _dir_comum(repo) / ARQUIVO_BLOQUEIOS
    try:
        linhas = caminho.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as erro:
        raise BloqueiosAusentes(str(caminho)) from erro
    termos = (linha.strip() for linha in linhas)
    return [t for t in termos if t and not t.startswith("#")]


def termo_bloqueado(texto, termos):
    alvo = normaliza(texto)
    for termo in termos:
        if normaliza(termo) in alvo:
            return termo
    return None


def ler_commit(repo, sha):
    r = _git(repo, "log", "-1", "--format=%P%x00%aI%x00%B", "--end-of-options", sha, "--")
    if r.returncode != 0 or r.stdout.count("\0") < 2:
        return None
    pais, data, mensagem = r.stdout.split("\0", 2)
    if len(pais.split()) > 1:
        return None
    mensagem = unicodedata.normalize("NFC", mensagem).strip()
    titulo, _, corpo = mensagem.partition("\n")
    titulo = titulo.strip()
    if not titulo or MARCADOR.search(corpo):
        return None
    completo = _git(repo, "rev-parse", "--verify", "--end-of-options", f"{sha}^{{commit}}")
    if completo.returncode != 0:
        return None
    return {"id": completo.stdout.strip(), "tipo": "commit", "data": data.strip(), "texto": titulo}
