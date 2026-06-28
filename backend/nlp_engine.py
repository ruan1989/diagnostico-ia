import json
import os
import re

_sintomas_db = None


def _carregar_sintomas() -> list:
    global _sintomas_db
    if _sintomas_db is None:
        caminho = os.path.join(os.path.dirname(__file__), "..", "dados", "sintomas.json")
        with open(caminho, "r", encoding="utf-8") as f:
            _sintomas_db = json.load(f)
    return _sintomas_db


def _normalizar(texto: str) -> str:
    texto = texto.lower()
    # remove pontuação
    texto = re.sub(r"[^\w\s]", " ", texto)
    return texto


def analisar_sintomas(relato: str) -> dict:
    """
    Analisa o relato livre de sintomas e retorna doenças detectadas e próximos passos.
    Retorna: {"doencas": [...], "proximos_passos": "..."}
    """
    relato_normalizado = _normalizar(relato)
    sintomas_db = _carregar_sintomas()

    doencas_detectadas = []
    pontuacao = {}

    for entrada in sintomas_db:
        doenca = entrada["doenca"]
        acertos = sum(
            1 for sintoma in entrada["sintomas"]
            if _normalizar(sintoma) in relato_normalizado
        )
        if acertos > 0:
            pontuacao[doenca] = acertos

    # ordena por número de sintomas coincidentes (maior primeiro)
    doencas_detectadas = sorted(pontuacao, key=pontuacao.get, reverse=True)

    if not doencas_detectadas:
        proximos_passos = (
            "Nenhuma doença identificada com base nos sintomas relatados. "
            "Consulte um médico para avaliação presencial."
        )
    else:
        proximos_passos = (
            f"Possível(is) condição(ões) detectada(s): {', '.join(doencas_detectadas)}. "
            "As informações fornecidas são apenas indicativas. "
            "Procure atendimento médico presencial para diagnóstico definitivo e tratamento adequado."
        )

    return {
        "doencas": doencas_detectadas,
        "proximos_passos": proximos_passos,
    }
