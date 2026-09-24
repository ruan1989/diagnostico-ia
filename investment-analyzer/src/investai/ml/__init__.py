"""Camada de aprendizado de máquina: amostras, modelo, registro.

A ordem de uso é sempre a mesma, e nenhuma etapa pode ser pulada:

    montar_amostras  → features no instante t, rótulo estritamente depois
    dividir_no_tempo → treino e validação separados por TEMPO, não sorteio
    treinar          → logístico com L2, recusa amostra pequena
    validar          → calibração medida fora da amostra
    ModelRegistry    → versiona e só promove o que está calibrado
"""
from .amostras import (
    Amostra, ConjuntoAmostras, FEATURES, dividir_no_tempo, montar_amostras,
    rotular, vetor_features,
)
from .modelo import (
    Hiperparametros, MIN_OBS_POR_PARAMETRO, MIN_TREINO, ModeloProbabilidade,
    Padronizador, TreinoError, sigmoide, treinar, validar,
)
from .registro import ModelRegistry, RegistroError, VersaoModelo, hash_modelo

__all__ = [
    "Amostra", "ConjuntoAmostras", "FEATURES", "Hiperparametros",
    "MIN_OBS_POR_PARAMETRO", "MIN_TREINO", "ModelRegistry",
    "ModeloProbabilidade", "Padronizador", "RegistroError", "TreinoError",
    "VersaoModelo", "dividir_no_tempo", "hash_modelo", "montar_amostras",
    "rotular", "sigmoide", "treinar", "validar", "vetor_features",
]
