#!/usr/bin/env python3
"""Publica títulos de commits e notas curtas no feed da página de acompanhamento."""

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

GIST_ID = ""
CONTA = "VoidSeeder"
ARQUIVO_GIST = "registros.json"
TEMPO_LIMITE = 30

ARQUIVO_BLOQUEIOS = "acompanhamento-bloqueios"
ARQUIVO_FILA = "acompanhamento-pendentes.json"
ARQUIVO_TRAVA = "acompanhamento.lock"
ARQUIVO_LOG = "acompanhamento.log"
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


def _registra_log(repo, mensagem):
    try:
        momento = datetime.now().astimezone().isoformat(timespec="seconds")
        with open(_dir_comum(repo) / ARQUIVO_LOG, "a", encoding="utf-8") as log:
            log.write(f"{momento} {mensagem}\n")
    except OSError:
        pass


def _instante(registro):
    return datetime.fromisoformat(registro["data"]).timestamp()


def _valida(registros):
    for registro in registros:
        try:
            _instante(registro)
            if not registro["id"] or not registro["texto"]:
                raise ValueError
        except (KeyError, TypeError, ValueError) as erro:
            raise ValueError(f"registro inválido no feed: {registro!r}") from erro


def _alcancavel(repo, sha):
    """Um SHA que nenhuma referência alcança já foi reescrito e não deve voltar ao feed."""
    r = _git(repo, "rev-list", "-n", "1", sha, "--not", "--all")
    return r.returncode == 0 and not r.stdout.strip()


def _commit_publicavel(repo, sha, termos):
    registro = ler_commit(repo, sha)
    if registro is None or not _alcancavel(repo, registro["id"]):
        return None
    termo = termo_bloqueado(registro["texto"], termos)
    if termo:
        _registra_log(repo, f"commit {registro['id']} não publicado: contém o termo {termo!r}")
        return None
    return registro


def aplicar(registros, operacoes, repo, termos):
    _valida(registros)
    por_id = {r["id"]: r for r in registros}
    for operacao in operacoes:
        tipo = operacao.get("op")
        if tipo == "commit":
            novo = _commit_publicavel(repo, operacao["sha"], termos)
        elif tipo == "reescrita":
            por_id.pop(operacao["de"], None)
            novo = _commit_publicavel(repo, operacao["para"], termos)
        elif tipo == "nota":
            novo = operacao["registro"]
            termo = termo_bloqueado(novo["texto"], termos)
            if termo:
                _registra_log(repo, f"nota {novo['id']} não publicada: contém o termo {termo!r}")
                novo = None
        else:
            _registra_log(repo, f"operação desconhecida ignorada: {tipo!r}")
            novo = None
        if novo:
            por_id[novo["id"]] = novo
    return sorted(por_id.values(), key=lambda r: (_instante(r), r["id"]))


@contextmanager
def _trava(repo):
    with open(_dir_comum(repo) / ARQUIVO_TRAVA, "a", encoding="utf-8") as arquivo:
        fcntl.flock(arquivo, fcntl.LOCK_EX)
        yield


def _le_fila(repo):
    caminho = _dir_comum(repo) / ARQUIVO_FILA
    try:
        fila = json.loads(caminho.read_text(encoding="utf-8"))
        if isinstance(fila, list):
            return fila
    except FileNotFoundError:
        return []
    except ValueError:
        pass
    os.replace(caminho, caminho.with_suffix(".corrompido"))
    _registra_log(repo, "fila ilegível guardada em acompanhamento-pendentes.corrompido")
    return []


def _grava_fila(repo, fila):
    caminho = _dir_comum(repo) / ARQUIVO_FILA
    temporario = caminho.with_name(f"{caminho.name}.{os.getpid()}.tmp")
    temporario.write_text(json.dumps(fila, ensure_ascii=False), encoding="utf-8")
    os.replace(temporario, caminho)


def enviar(repo, cliente, novas=()):
    with _trava(repo):
        fila = _le_fila(repo) + list(novas)
        if not fila:
            return 0
        _grava_fila(repo, fila)
        termos = carregar_bloqueios(repo)
        atuais = cliente.ler()
        resultado = aplicar(atuais, fila, repo, termos)
        if resultado != atuais:
            cliente.gravar(resultado)
        (_dir_comum(repo) / ARQUIVO_FILA).unlink(missing_ok=True)
        return len(fila)


class ClienteGist:
    def __init__(self, gist_id=GIST_ID):
        self.gist_id = gist_id
        self._token = None

    def _obtem_token(self):
        if self._token is None:
            r = subprocess.run(
                ["gh", "auth", "token", "--user", CONTA],
                capture_output=True,
                text=True,
                timeout=TEMPO_LIMITE,
                check=False,
            )
            if r.returncode != 0 or not r.stdout.strip():
                raise OSError(f"sem token da conta {CONTA}")
            self._token = r.stdout.strip()
        return self._token

    def _gh(self, *args, entrada=None):
        if not self.gist_id:
            raise OSError("GIST_ID não configurado")
        env = {**os.environ, "GH_TOKEN": self._obtem_token()}
        r = subprocess.run(
            ["gh", "api", *args],
            input=entrada,
            env=env,
            capture_output=True,
            encoding="utf-8",
            timeout=TEMPO_LIMITE,
            check=False,
        )
        if r.returncode != 0:
            raise OSError(f"gh api falhou: {r.stderr.strip()[:300]}")
        return r.stdout

    def ler(self):
        gist = json.loads(self._gh(f"gists/{self.gist_id}"))
        arquivo = gist.get("files", {}).get(ARQUIVO_GIST)
        if not arquivo or arquivo.get("truncated"):
            raise ValueError(f"{ARQUIVO_GIST} ausente ou truncado no Gist")
        registros = json.loads(arquivo["content"])
        if not isinstance(registros, list):
            raise ValueError(f"{ARQUIVO_GIST} não contém uma lista")
        return registros

    def gravar(self, registros):
        conteudo = json.dumps(registros, ensure_ascii=False, indent=2) + "\n"
        corpo = json.dumps({"files": {ARQUIVO_GIST: {"content": conteudo}}}, ensure_ascii=False)
        self._gh("-X", "PATCH", f"gists/{self.gist_id}", "--input", "-", entrada=corpo)


def _em_rebase(repo):
    r = _git(repo, "rev-parse", "--absolute-git-dir")
    if r.returncode != 0:
        return False
    pasta = Path(r.stdout.strip())
    return (pasta / "rebase-merge").exists() or (pasta / "rebase-apply").exists()


def _le_pares(entrada):
    operacoes = []
    for linha in entrada:
        partes = linha.split()
        if len(partes) >= 2:
            operacoes.append({"op": "reescrita", "de": partes[0], "para": partes[1]})
    return operacoes


def _publica_nota(repo, frase):
    texto = " ".join(frase.split())
    if not texto:
        print("Nota vazia, nada publicado.", file=sys.stderr)
        return 1
    try:
        termo = termo_bloqueado(texto, carregar_bloqueios(repo))
    except (BloqueiosAusentes, OSError) as erro:
        print(f"Nota não publicada, lista de bloqueios indisponível: {erro}", file=sys.stderr)
        return 1
    if termo:
        print(f"Nota não publicada: contém o termo {termo!r}.", file=sys.stderr)
        return 2
    registro = {
        "id": f"nota-{time.time_ns()}",
        "tipo": "nota",
        "data": datetime.now().astimezone().isoformat(timespec="seconds"),
        "texto": texto,
    }
    try:
        enviar(repo, ClienteGist(), [{"op": "nota", "registro": registro}])
    except Exception as erro:
        _registra_log(repo, f"nota: {erro}")
        print(f"Envio falhou, a nota ficou na fila: {erro}", file=sys.stderr)
        return 1
    return 0


def principal(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    comandos = parser.add_subparsers(dest="comando", required=True)
    comandos.add_parser("commit").add_argument("sha")
    comandos.add_parser("nota").add_argument("frase")
    comandos.add_parser("reescrita")
    comandos.add_parser("enviar")
    args = parser.parse_args(argv)

    if args.comando == "nota":
        return _publica_nota(args.repo, args.frase)
    try:
        if args.comando == "commit":
            if _em_rebase(args.repo):
                return 0
            novas = [{"op": "commit", "sha": args.sha}]
        elif args.comando == "reescrita":
            novas = _le_pares(sys.stdin)
        else:
            novas = []
        enviar(args.repo, ClienteGist(), novas)
    except Exception as erro:
        _registra_log(args.repo, f"{args.comando}: {erro}")
    return 0


if __name__ == "__main__":
    sys.exit(principal())
