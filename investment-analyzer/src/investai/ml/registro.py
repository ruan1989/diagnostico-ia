"""Registro de modelos: qual modelo produziu aquele número, e com que direito.

O problema
----------
Daqui a três meses, olhando uma operação que deu errado, a pergunta vai ser:
que modelo estimou aquela probabilidade? treinado em que período? com quais
features? a calibração dele estava válida naquele dia?

Sem registro, nenhuma dessas perguntas tem resposta, e a análise pós-trade
vira opinião. Este módulo guarda o suficiente para respondê-las.

O que é guardado
----------------
Versão, período de treino, lista de features na ordem, hiperparâmetros,
métricas fora da amostra e o relatório de calibração completo. A identidade
da versão é o hash do que define o modelo — mudar um hiperparâmetro e manter
o número da versão seria a mesma armadilha que o registro de estratégias
existe para evitar.

A regra de produção
-------------------
Só um modelo por (par, timeframe, lado) fica em produção, e só entra em
produção modelo calibrado. `promover` recusa o resto — permitir promover um
modelo descalibrado tornaria o registro um enfeite.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from .modelo import ModeloProbabilidade


def hash_modelo(m: ModeloProbabilidade) -> str:
    """Identidade do modelo: o que o define, não o que ele produziu."""
    canonico = json.dumps({
        "features": list(m.features),
        "hiper": m.hiper.to_dict(),
        "coeficientes": [round(c, 8) for c in m.coeficientes],
        "intercepto": round(m.intercepto, 8),
        "treino": [m.ts_treino_inicio, m.ts_treino_fim],
        "symbol": m.symbol, "timeframe": m.timeframe, "side": m.side,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonico.encode()).hexdigest()[:16]


@dataclass(slots=True)
class VersaoModelo:
    chave: str                 # symbol|timeframe|side
    versao: int
    modelo_hash: str
    modelo: ModeloProbabilidade
    criado_em_ms: int
    em_producao: bool = False
    notas: str = ""
    historico: list[dict[str, Any]] = field(default_factory=list)

    @property
    def id(self) -> str:
        return f"{self.chave}@v{self.versao}"

    def to_dict(self, *, completo: bool = False) -> dict[str, Any]:
        m = self.modelo
        cal = m.calibracao
        base: dict[str, Any] = {
            "id": self.id, "chave": self.chave, "versao": self.versao,
            "modelo_hash": self.modelo_hash,
            "criado_em_ms": self.criado_em_ms,
            "em_producao": self.em_producao,
            "calibrado": m.calibrado,
            "motivo_nao_calibrado": m.motivo_nao_calibrado,
            "n_treino": m.n_treino, "n_validacao": m.n_validacao,
            "periodo_treino": [m.ts_treino_inicio, m.ts_treino_fim],
            "periodo_validacao": [m.ts_validacao_inicio, m.ts_validacao_fim],
            "features": list(m.features),
            "hiperparametros": m.hiper.to_dict(),
            "taxa_base_treino": round(m.taxa_base_treino, 4),
            "veredicto_calibracao": cal.veredicto if cal else "SEM_CALIBRACAO",
            "ece": round(cal.ece, 4) if cal else None,
            "skill": (round(cal.brier.skill, 4)
                      if cal and cal.brier else None),
            "notas": self.notas,
        }
        if completo:
            base["modelo"] = m.to_dict()
            base["historico"] = self.historico
        return base


class RegistroError(RuntimeError):
    pass


class ModelRegistry:
    """Versiona modelos por (par, timeframe, lado)."""

    def __init__(self) -> None:
        self._versoes: dict[str, VersaoModelo] = {}
        self._producao: dict[str, str] = {}      # chave -> id da versão

    @staticmethod
    def chave_de(symbol: str, timeframe: str, side: str) -> str:
        return f"{symbol.upper()}|{timeframe}|{side.lower()}"

    # ---------------------------------------------------------- registro
    def registrar(self, modelo: ModeloProbabilidade, *, notas: str = "",
                  agora_ms: int | None = None) -> VersaoModelo:
        """Adiciona uma versão. Modelo idêntico devolve a versão existente."""
        if not modelo.treinado:
            raise RegistroError("modelo não treinado não pode ser registrado")
        if not (modelo.symbol and modelo.timeframe and modelo.side):
            raise RegistroError(
                "modelo sem par, timeframe ou lado: sem isso não há como "
                "saber onde ele se aplica, e um modelo aplicado ao ativo "
                "errado produz número plausível e falso")

        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
        chave = self.chave_de(modelo.symbol, modelo.timeframe, modelo.side)
        h = hash_modelo(modelo)

        existente = next(
            (v for v in self._versoes.values()
             if v.chave == chave and v.modelo_hash == h), None)
        if existente is not None:
            return existente

        versoes = [v.versao for v in self._versoes.values() if v.chave == chave]
        nova = VersaoModelo(
            chave=chave, versao=(max(versoes) + 1 if versoes else 1),
            modelo_hash=h, modelo=modelo, criado_em_ms=agora, notas=notas)
        nova.historico.append({
            "ts": agora, "evento": "registrado",
            "calibrado": modelo.calibrado,
            "n_treino": modelo.n_treino, "n_validacao": modelo.n_validacao,
        })
        self._versoes[nova.id] = nova
        return nova

    def obter(self, id_versao: str) -> VersaoModelo:
        v = self._versoes.get(id_versao)
        if v is None:
            raise RegistroError(f"versão de modelo desconhecida: {id_versao}")
        return v

    def listar(self, *, chave: str | None = None,
               so_calibrados: bool = False) -> list[VersaoModelo]:
        out = list(self._versoes.values())
        if chave:
            out = [v for v in out if v.chave == chave]
        if so_calibrados:
            out = [v for v in out if v.modelo.calibrado]
        return sorted(out, key=lambda v: (v.chave, v.versao))

    # --------------------------------------------------------- produção
    def promover(self, id_versao: str, *,
                 agora_ms: int | None = None) -> VersaoModelo:
        """Coloca a versão em produção. Recusa modelo sem calibração válida."""
        v = self.obter(id_versao)
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
        if not v.modelo.calibrado:
            raise RegistroError(
                f"{id_versao} não tem calibração válida "
                f"({v.modelo.motivo_nao_calibrado}). Promover assim tornaria "
                f"este registro um enfeite: a probabilidade dele entraria no "
                f"dimensionamento sem nada garantir que ela corresponde à "
                f"realidade")

        anterior = self._producao.get(v.chave)
        if anterior and anterior != id_versao:
            velha = self._versoes.get(anterior)
            if velha is not None:
                velha.em_producao = False
                velha.historico.append({
                    "ts": agora, "evento": "aposentado",
                    "substituido_por": id_versao})
        v.em_producao = True
        self._producao[v.chave] = id_versao
        v.historico.append({"ts": agora, "evento": "promovido",
                            "substituiu": anterior or ""})
        return v

    def despromover(self, chave: str, motivo: str = "", *,
                    agora_ms: int | None = None) -> VersaoModelo | None:
        """Tira de produção sem apagar. Usado quando drift derruba o modelo."""
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
        id_versao = self._producao.pop(chave, None)
        if id_versao is None:
            return None
        v = self._versoes.get(id_versao)
        if v is not None:
            v.em_producao = False
            v.historico.append({"ts": agora, "evento": "despromovido",
                                "motivo": motivo})
        return v

    def em_producao(self, symbol: str, timeframe: str,
                    side: str) -> ModeloProbabilidade | None:
        """O modelo que o agente deve consultar. None quando não há."""
        id_versao = self._producao.get(self.chave_de(symbol, timeframe, side))
        if id_versao is None:
            return None
        v = self._versoes.get(id_versao)
        if v is None or not v.modelo.calibrado:
            # Um modelo que perdeu a calibração (revalidação posterior) não
            # pode continuar servindo, mesmo marcado como produção.
            return None
        return v.modelo

    def buscador(self):
        """Devolve a função que o `AgenteML` consome."""
        return self.em_producao

    def tem_producao(self) -> bool:
        """Existe ao menos um modelo calibrado servindo?

        O consenso consulta isto antes de montar o agente de ML. A cobertura
        é medida sobre o peso nominal dos agentes que puderam opinar, então
        um agente que se absteria sempre não pode entrar na conta: ele
        derrubaria a cobertura e reprovaria análises que hoje passam.
        """
        return any(self.em_producao(*chave.split("|"))
                   is not None for chave in self._producao)

    # --------------------------------------------------------- inspeção
    def resumo(self) -> dict[str, Any]:
        versoes = self.listar()
        return {
            "total": len(versoes),
            "calibrados": sum(1 for v in versoes if v.modelo.calibrado),
            "em_producao": len(self._producao),
            "chaves": sorted({v.chave for v in versoes}),
            "versoes": [v.to_dict() for v in versoes],
            "observacao": (
                "Só entra em produção modelo com calibração medida fora da "
                "amostra e aprovada. Um modelo em produção que perder a "
                "calibração em uma revalidação deixa de ser servido, mesmo "
                "sem ninguém despromovê-lo."),
        }


__all__ = ["ModelRegistry", "RegistroError", "VersaoModelo", "hash_modelo"]
