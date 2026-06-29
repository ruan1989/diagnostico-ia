import json
import os

_naturais_db = None
_farmaceuticos_db = None


def _carregar(nome_arquivo: str) -> list:
    caminho = os.path.join(os.path.dirname(__file__), "..", "dados", nome_arquivo)
    with open(caminho, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_naturais() -> list:
    global _naturais_db
    if _naturais_db is None:
        _naturais_db = _carregar("tratamentos_naturais.json")
    return _naturais_db


def _get_farmaceuticos() -> list:
    global _farmaceuticos_db
    if _farmaceuticos_db is None:
        _farmaceuticos_db = _carregar("tratamentos_farmaceuticos.json")
    return _farmaceuticos_db


def obter_tratamentos(doencas: list) -> tuple:
    """
    Retorna (naturais, farmacologicos) para as doenças fornecidas.
    Cada item é um dict {"doenca": str, "tratamentos": [str]}.
    """
    naturais = [e for e in _get_naturais() if e["doenca"] in doencas]
    farmacologicos = [e for e in _get_farmaceuticos() if e["doenca"] in doencas]
    return naturais, farmacologicos
