# Acompanhamento do WelPhys

Página estática que mostra os títulos dos commits e notas curtas do desenvolvimento do WelPhys. O código do WelPhys não passa por aqui: só saem o título e a data de cada commit.

A página lê um arquivo `registros.json` a cada 60 segundos. O script `ferramentas/registra.py` mantém esse arquivo.

## Testes

```bash
python3 -m unittest discover -s ferramentas
node --test ferramentas/lista.test.js
```

## Registrar uma nota

```bash
python3 ferramentas/registra.py --repo CAMINHO_DO_REPOSITORIO nota "Frase curta"
```

## Deixar um commit de fora

Inclua esta linha no corpo da mensagem do commit:

```text
Acompanhamento: não
```
