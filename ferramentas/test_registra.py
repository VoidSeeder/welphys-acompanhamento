import copy
import io
import json
import multiprocessing
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from registra import (
    BloqueiosAusentes,
    ClienteGist,
    aplicar,
    carregar_bloqueios,
    enviar,
    ler_commit,
    normaliza,
    principal,
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


class ClienteFalso:
    def __init__(self):
        self.dados = []
        self.falhar = False
        self.ler_invalido = False
        self.gravacoes = 0

    def ler(self):
        if self.falhar:
            raise OSError("sem rede")
        if self.ler_invalido:
            raise ValueError("não é lista")
        return copy.deepcopy(self.dados)

    def gravar(self, registros):
        if self.falhar:
            raise OSError("sem rede")
        self.gravacoes += 1
        self.dados = copy.deepcopy(registros)


class ClienteArquivo:
    """Faz o papel do Gist num arquivo, com uma pausa que expõe gravações concorrentes."""

    def __init__(self, caminho):
        self.caminho = Path(caminho)

    def ler(self):
        dados = json.loads(self.caminho.read_text(encoding="utf-8"))
        time.sleep(0.05)
        return dados

    def gravar(self, registros):
        self.caminho.write_text(json.dumps(registros), encoding="utf-8")


def _envia_um(repo, caminho, sha):
    enviar(Path(repo), ClienteArquivo(caminho), [{"op": "commit", "sha": sha}])


class BaseEnvio(RepoTemporario):
    def setUp(self):
        super().setUp()
        self.escreve_bloqueios("")
        self.cliente = ClienteFalso()

    def amend(self, mensagem):
        self.git("commit", "-q", "--amend", "--allow-empty", "-m", mensagem)
        return self.git("rev-parse", "HEAD")

    def apaga_bloqueios(self):
        (self.repo / ".git" / "acompanhamento-bloqueios").unlink()

    def le_fila(self):
        caminho = self.repo / ".git" / "acompanhamento-pendentes.json"
        if not caminho.exists():
            return []
        return json.loads(caminho.read_text(encoding="utf-8"))

    def envia_em_paralelo(self, quantos):
        caminho = self.repo.parent / "gist.json"
        caminho.write_text("[]", encoding="utf-8")
        shas = [self.commit(f"Commit {i}") for i in range(quantos)]
        ctx = multiprocessing.get_context("fork")
        processos = [
            ctx.Process(target=_envia_um, args=(str(self.repo), str(caminho), sha)) for sha in shas
        ]
        for p in processos:
            p.start()
        for p in processos:
            p.join(30)
            self.assertEqual(p.exitcode, 0)
        return json.loads(caminho.read_text(encoding="utf-8"))


class TestEnviar(BaseEnvio):
    def test_commit_publicado(self):
        sha = self.commit("Novo solver")
        enviar(self.repo, self.cliente, [{"op": "commit", "sha": sha}])
        self.assertEqual([r["texto"] for r in self.cliente.dados], ["Novo solver"])

    def test_reenvio_nao_duplica(self):
        op = {"op": "commit", "sha": self.commit("A")}
        enviar(self.repo, self.cliente, [op])
        enviar(self.repo, self.cliente, [op])
        self.assertEqual(len(self.cliente.dados), 1)

    def test_reenvio_sem_mudanca_nao_grava(self):
        op = {"op": "commit", "sha": self.commit("A")}
        enviar(self.repo, self.cliente, [op])
        enviar(self.repo, self.cliente, [op])
        self.assertEqual(self.cliente.gravacoes, 1)

    def test_amend_substitui_e_mantem_data(self):
        velho = self.commit("Antigo", data_autor="2026-11-01T10:00:00-03:00")
        enviar(self.repo, self.cliente, [{"op": "commit", "sha": velho}])
        novo = self.amend("Corrigido")
        enviar(self.repo, self.cliente, [{"op": "reescrita", "de": velho, "para": novo}])
        self.assertEqual(
            [(r["texto"], r["data"]) for r in self.cliente.dados],
            [("Corrigido", "2026-11-01T10:00:00-03:00")],
        )

    def test_commit_atrasado_depois_do_amend_nao_volta(self):
        velho = self.commit("Antigo")
        novo = self.amend("Corrigido")
        enviar(self.repo, self.cliente, [{"op": "commit", "sha": novo}])
        enviar(self.repo, self.cliente, [{"op": "reescrita", "de": velho, "para": novo}])
        enviar(self.repo, self.cliente, [{"op": "commit", "sha": velho}])
        self.assertEqual([r["id"] for r in self.cliente.dados], [novo])

    def test_reescritas_fora_de_ordem_nao_duplicam(self):
        a = self.commit("A")
        b = self.amend("B")
        c = self.amend("C")
        enviar(self.repo, self.cliente, [{"op": "commit", "sha": c}])
        enviar(self.repo, self.cliente, [{"op": "reescrita", "de": b, "para": c}])
        enviar(self.repo, self.cliente, [{"op": "reescrita", "de": a, "para": b}])
        self.assertEqual([r["id"] for r in self.cliente.dados], [c])

    def test_reescrita_nao_republica_item_removido_do_feed(self):
        velho = self.commit("Removido à mão")
        novo = self.amend("Removido à mão, reescrito")
        enviar(self.repo, self.cliente, [{"op": "reescrita", "de": velho, "para": novo}])
        self.assertEqual(self.cliente.dados, [])

    def test_reescrita_de_commit_ainda_na_fila_publica_o_novo(self):
        velho = self.commit("Feito sem rede")
        self.cliente.falhar = True
        with self.assertRaises(OSError):
            enviar(self.repo, self.cliente, [{"op": "commit", "sha": velho}])
        novo = self.amend("Feito sem rede, corrigido")
        with self.assertRaises(OSError):
            enviar(self.repo, self.cliente, [{"op": "reescrita", "de": velho, "para": novo}])
        self.cliente.falhar = False
        enviar(self.repo, self.cliente)
        self.assertEqual([r["id"] for r in self.cliente.dados], [novo])

    def test_falha_mantem_na_fila_e_reenvia(self):
        sha = self.commit("Offline")
        self.cliente.falhar = True
        with self.assertRaises(OSError):
            enviar(self.repo, self.cliente, [{"op": "commit", "sha": sha}])
        self.cliente.falhar = False
        enviar(self.repo, self.cliente)
        self.assertEqual([r["id"] for r in self.cliente.dados], [sha])
        self.assertEqual(self.le_fila(), [])

    def test_gist_invalido_nao_grava(self):
        self.cliente.ler_invalido = True
        with self.assertRaises(ValueError):
            enviar(self.repo, self.cliente, [{"op": "commit", "sha": self.commit("X")}])
        self.assertEqual(self.cliente.gravacoes, 0)
        self.assertEqual(len(self.le_fila()), 1)

    def test_item_malformado_no_gist_nao_grava(self):
        self.cliente.dados = [{"texto": "sem id"}]
        with self.assertRaises(ValueError):
            enviar(self.repo, self.cliente, [{"op": "commit", "sha": self.commit("X")}])
        self.assertEqual(self.cliente.gravacoes, 0)

    def test_sem_bloqueios_nao_publica_e_mantem_fila(self):
        self.apaga_bloqueios()
        with self.assertRaises(BloqueiosAusentes):
            enviar(self.repo, self.cliente, [{"op": "commit", "sha": self.commit("X")}])
        self.assertEqual(self.cliente.gravacoes, 0)
        self.assertEqual(len(self.le_fila()), 1)

    def test_titulo_bloqueado_nao_publica(self):
        self.escreve_bloqueios("segredo\n")
        enviar(self.repo, self.cliente, [{"op": "commit", "sha": self.commit("Com SEGREDO")}])
        self.assertEqual(self.cliente.dados, [])

    def test_ordena_pelo_instante_com_fusos_diferentes(self):
        a = self.commit("Depois", data_autor="2026-11-01T10:00:00-03:00")
        b = self.commit("Antes", data_autor="2026-11-01T12:00:00+00:00")
        r = aplicar([], [{"op": "commit", "sha": a}, {"op": "commit", "sha": b}], self.repo, [])
        self.assertEqual([i["texto"] for i in r], ["Antes", "Depois"])

    def test_envios_simultaneos_preservam_todos(self):
        self.assertEqual(len(self.envia_em_paralelo(8)), 8)


class TestClienteGist(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.pasta = Path(tmp.name)
        gh = self.pasta / "gh"
        gh.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = auth ]; then echo token-falso; exit 0; fi\n'
            'printf \'%s\\n\' "$GH_TOKEN $*" >> "$PASTA/chamadas"\n'
            'if [ "$2" = "-X" ]; then cat > "$PASTA/corpo"; exit 0; fi\n'
            'cat "$PASTA/resposta"\n',
            encoding="utf-8",
        )
        gh.chmod(0o755)
        ambiente = {"PATH": f"{self.pasta}:{os.environ['PATH']}", "PASTA": str(self.pasta)}
        patcher = mock.patch.dict(os.environ, ambiente)
        patcher.start()
        self.addCleanup(patcher.stop)

    def responde(self, conteudo, **extra):
        arquivo = {"content": conteudo, **extra}
        (self.pasta / "resposta").write_text(
            json.dumps({"files": {"registros.json": arquivo}}), encoding="utf-8"
        )

    def test_ler_devolve_a_lista(self):
        self.responde('[{"id": "a"}]')
        self.assertEqual(ClienteGist("abc").ler(), [{"id": "a"}])

    def test_ler_rejeita_o_que_nao_e_lista(self):
        self.responde('{"id": "a"}')
        with self.assertRaises(ValueError):
            ClienteGist("abc").ler()

    def test_ler_rejeita_conteudo_truncado(self):
        self.responde("[]", truncated=True)
        with self.assertRaises(ValueError):
            ClienteGist("abc").ler()

    def test_gravar_usa_o_token_e_preserva_acentos(self):
        ClienteGist("abc").gravar([{"texto": "Versão"}])
        self.assertTrue(
            (self.pasta / "chamadas").read_text("utf-8").startswith("token-falso api -X PATCH gists/abc")
        )
        corpo = json.loads((self.pasta / "corpo").read_text("utf-8"))
        self.assertIn("Versão", corpo["files"]["registros.json"]["content"])

    def test_sem_id_configurado_falha_sem_chamar_o_gh(self):
        with self.assertRaises(OSError):
            ClienteGist("").ler()
        self.assertFalse((self.pasta / "chamadas").exists())


class TestLinhaDeComando(BaseEnvio):
    def roda(self, *args, entrada=""):
        with mock.patch("registra.ClienteGist", return_value=self.cliente), mock.patch(
            "sys.stdin", io.StringIO(entrada)
        ), mock.patch("sys.stderr", io.StringIO()):
            return principal(["--repo", str(self.repo), *args])

    def test_commit_durante_rebase_nao_faz_nada(self):
        sha = self.commit("Em rebase")
        (self.repo / ".git" / "rebase-merge").mkdir()
        self.assertEqual(self.roda("commit", sha), 0)
        self.assertEqual((self.cliente.dados, self.le_fila()), ([], []))

    def test_commit_com_falha_sai_com_zero_e_registra_no_log(self):
        sha = self.commit("Offline")
        self.cliente.falhar = True
        self.assertEqual(self.roda("commit", sha), 0)
        self.assertEqual(len(self.le_fila()), 1)
        self.assertIn("sem rede", (self.repo / ".git" / "acompanhamento.log").read_text("utf-8"))

    def test_reescrita_le_pares_da_entrada(self):
        velho = self.commit("Antigo")
        enviar(self.repo, self.cliente, [{"op": "commit", "sha": velho}])
        novo = self.amend("Novo")
        self.assertEqual(self.roda("reescrita", entrada=f"{velho} {novo}\n"), 0)
        self.assertEqual([r["id"] for r in self.cliente.dados], [novo])

    def test_nota_publicada_so_com_os_quatro_campos(self):
        self.assertEqual(self.roda("nota", "Malha refinada"), 0)
        self.assertEqual(self.cliente.dados[0]["texto"], "Malha refinada")
        self.assertEqual(set(self.cliente.dados[0]), {"id", "tipo", "data", "texto"})

    def test_nota_bloqueada_sai_com_2_e_nao_publica(self):
        self.escreve_bloqueios("segredo\n")
        self.assertEqual(self.roda("nota", "Com Segredo"), 2)
        self.assertEqual((self.cliente.dados, self.le_fila()), ([], []))


if __name__ == "__main__":
    unittest.main()
