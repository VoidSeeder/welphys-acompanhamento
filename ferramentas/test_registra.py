import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from registra import (
    BloqueiosAusentes,
    carregar_bloqueios,
    ler_commit,
    normaliza,
    termo_bloqueado,
)


def ambiente_git(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="Teste",
        GIT_AUTHOR_EMAIL="teste@example.com",
        GIT_COMMITTER_NAME="Teste",
        GIT_COMMITTER_EMAIL="teste@example.com",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
    )
    env.update(extra)
    return env


class RepoTemporario(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")

    def git(self, *args, **env):
        r = subprocess.run(
            ["git", "-C", str(self.repo), *args],
            env=ambiente_git(**env),
            capture_output=True,
            text=True,
            check=True,
        )
        return r.stdout.strip()

    def commit(self, mensagem, data_autor=None):
        env = {"GIT_AUTHOR_DATE": data_autor} if data_autor else {}
        self.git("commit", "-q", "--allow-empty", "-m", mensagem, **env)
        return self.git("rev-parse", "HEAD")

    def commit_de_merge(self):
        self.commit("Base")
        self.git("checkout", "-q", "-b", "lado")
        self.commit("Lado")
        self.git("checkout", "-q", "main")
        self.commit("Principal")
        self.git("merge", "-q", "--no-ff", "-m", "Merge", "lado")
        return self.git("rev-parse", "HEAD")

    def escreve_bloqueios(self, conteudo):
        (self.repo / ".git" / "acompanhamento-bloqueios").write_text(conteudo, encoding="utf-8")


class TestFiltros(RepoTemporario):
    def test_normaliza_ignora_acento_e_caixa(self):
        self.assertEqual(normaliza("CORAÇÃO"), "coracao")

    def test_bloqueios_ausentes_levanta(self):
        with self.assertRaises(BloqueiosAusentes):
            carregar_bloqueios(self.repo)

    def test_bloqueios_ignora_comentario_e_vazio(self):
        self.escreve_bloqueios("# nota\n\nsegredo\n")
        self.assertEqual(carregar_bloqueios(self.repo), ["segredo"])

    def test_termo_bloqueado_sem_acento(self):
        self.assertEqual(
            termo_bloqueado("Ajusta a Configuração", ["configuracao"]), "configuracao"
        )

    def test_termo_bloqueado_sem_ocorrencia(self):
        self.assertIsNone(termo_bloqueado("Novo solver", ["segredo"]))


class TestLerCommit(RepoTemporario):
    def test_ler_commit_usa_titulo_e_data_do_autor(self):
        sha = self.commit("Título\n\nCorpo que não sai", data_autor="2026-11-01T10:00:00-03:00")
        r = ler_commit(self.repo, sha)
        self.assertEqual((r["id"], r["tipo"], r["texto"]), (sha, "commit", "Título"))
        self.assertEqual(r["data"], "2026-11-01T10:00:00-03:00")
        self.assertNotIn("Corpo", json.dumps(r, ensure_ascii=False))

    def test_ler_commit_respeita_marcador(self):
        for marcador in ("Acompanhamento: não", "acompanhamento: nao"):
            self.assertIsNone(ler_commit(self.repo, self.commit("T\n\n" + marcador)))

    def test_ler_commit_ignora_merge(self):
        self.assertIsNone(ler_commit(self.repo, self.commit_de_merge()))

    def test_ler_commit_inexistente(self):
        self.assertIsNone(ler_commit(self.repo, "0" * 40))


if __name__ == "__main__":
    unittest.main()
