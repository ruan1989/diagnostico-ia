"""Registro de estratégias versionadas.

Regra que este módulo impõe
---------------------------
**Alterar parâmetros cria uma nova versão; nunca muda a versão existente.**

O motivo é concreto: se a estratégia v3 acumulou 200 operações de paper
trading e alguém ajusta um parâmetro "só um pouquinho", aquelas 200 operações
deixam de ser evidência sobre o que está rodando agora. Sem versionamento, o
histórico vira uma média entre coisas diferentes, e o sistema passa a achar
que validou algo que nunca existiu.

A versão é identificada pelo hash dos parâmetros: mudança silenciosa é
impossível por construção.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Fase(str, Enum):
    """Fases do ciclo de vida, na ordem obrigatória de progressão."""

    RASCUNHO = "rascunho"
    BACKTEST = "backtest"
    OUT_OF_SAMPLE = "out_of_sample"
    PAPER_TRADING = "paper_trading"
    SHADOW = "shadow"
    ASSISTIDO = "assistido"
    REAL_LIMITADO = "real_limitado"
    APOSENTADA = "aposentada"
    REPROVADA = "reprovada"


# Ordem de progressão. Pular fase é impossível: `proxima_fase` só avança um
# passo, e o gate da fase atual precisa passar.
ORDEM: tuple[Fase, ...] = (
    Fase.RASCUNHO,
    Fase.BACKTEST,
    Fase.OUT_OF_SAMPLE,
    Fase.PAPER_TRADING,
    Fase.SHADOW,
    Fase.ASSISTIDO,
    Fase.REAL_LIMITADO,
)

DESCRICAO_FASE: dict[Fase, str] = {
    Fase.RASCUNHO: "Estratégia definida, ainda sem nenhuma medição.",
    Fase.BACKTEST: "Mede o comportamento no histórico completo. Aprovar aqui "
                   "não significa quase nada — é só o filtro mais básico.",
    Fase.OUT_OF_SAMPLE: "Mede em dados que não participaram do ajuste, via "
                        "walk-forward. É aqui que a maioria das estratégias "
                        "morre, e é o objetivo.",
    Fase.PAPER_TRADING: "Executa em tempo real com dinheiro simulado, "
                        "sofrendo spread, slippage e latência de verdade.",
    Fase.SHADOW: "Produz decisões em tempo real sem enviar ordens, e compara "
                 "decisão teórica com execução simulada e movimento real.",
    Fase.ASSISTIDO: "Propõe operações reais, mas um humano confirma cada uma.",
    Fase.REAL_LIMITADO: "Opera capital real com limite reduzido e sob "
                        "monitoramento. Nunca é 'liberado', é 'limitado'.",
    Fase.APOSENTADA: "Retirada de operação.",
    Fase.REPROVADA: "Reprovada em um gate; não avança sem reformulação.",
}


def hash_parametros(parametros: dict[str, Any]) -> str:
    """Hash estável dos parâmetros — a identidade real da versão."""
    canonico = json.dumps(parametros, sort_keys=True, separators=(",", ":"),
                          default=str)
    return hashlib.sha256(canonico.encode()).hexdigest()[:16]


@dataclass(slots=True)
class VersaoEstrategia:
    strategy_id: str
    version: int
    parametros: dict[str, Any]
    params_hash: str
    fase: Fase = Fase.RASCUNHO
    criada_em_ms: int = 0
    atualizada_em_ms: int = 0
    mercado: str = "cripto_futuros"
    timeframe: str = "1H"
    descricao: str = ""
    # Histórico de transições: cada avanço ou reprovação fica registrado.
    historico: list[dict[str, Any]] = field(default_factory=list)
    # Resultado do último gate avaliado.
    ultimo_gate: dict[str, Any] | None = None
    motivo_reprovacao: str = ""

    @property
    def chave(self) -> str:
        return f"{self.strategy_id}@v{self.version}"

    @property
    def operavel_real(self) -> bool:
        return self.fase is Fase.REAL_LIMITADO

    @property
    def ativa(self) -> bool:
        return self.fase not in (Fase.APOSENTADA, Fase.REPROVADA,
                                 Fase.RASCUNHO)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id, "version": self.version,
            "chave": self.chave, "parametros": self.parametros,
            "params_hash": self.params_hash, "fase": self.fase.value,
            "fase_descricao": DESCRICAO_FASE[self.fase],
            "criada_em_ms": self.criada_em_ms,
            "atualizada_em_ms": self.atualizada_em_ms,
            "mercado": self.mercado, "timeframe": self.timeframe,
            "descricao": self.descricao, "operavel_real": self.operavel_real,
            "ativa": self.ativa, "historico": self.historico,
            "ultimo_gate": self.ultimo_gate,
            "motivo_reprovacao": self.motivo_reprovacao,
        }


class EstrategiaError(RuntimeError):
    pass


class StrategyRegistry:
    def __init__(self) -> None:
        self._versoes: dict[str, VersaoEstrategia] = {}

    # ---------------------------------------------------------- criação
    def criar(self, strategy_id: str, parametros: dict[str, Any], *,
              mercado: str = "cripto_futuros", timeframe: str = "1H",
              descricao: str = "",
              agora_ms: int | None = None) -> VersaoEstrategia:
        """Cria a próxima versão de uma estratégia.

        Se os parâmetros forem idênticos a uma versão existente, devolve
        aquela versão em vez de duplicar — mesma configuração é a mesma
        estratégia, e criar uma "nova" só zeraria o histórico sem motivo.
        """
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
        h = hash_parametros(parametros)

        existente = next(
            (v for v in self._versoes.values()
             if v.strategy_id == strategy_id and v.params_hash == h), None)
        if existente is not None:
            return existente

        versoes = [v.version for v in self._versoes.values()
                   if v.strategy_id == strategy_id]
        nova = VersaoEstrategia(
            strategy_id=strategy_id,
            version=(max(versoes) + 1 if versoes else 1),
            parametros=dict(parametros), params_hash=h,
            criada_em_ms=agora, atualizada_em_ms=agora,
            mercado=mercado, timeframe=timeframe, descricao=descricao,
        )
        nova.historico.append({
            "ts": agora, "evento": "criada", "fase": Fase.RASCUNHO.value,
            "params_hash": h,
        })
        self._versoes[nova.chave] = nova
        return nova

    def derivar(self, chave_origem: str, mudancas: dict[str, Any], *,
                descricao: str = "",
                agora_ms: int | None = None) -> VersaoEstrategia:
        """Cria nova versão a partir de outra, aplicando mudanças.

        É o único caminho para "ajustar" uma estratégia. A versão anterior
        continua existindo com seu histórico intacto — que é justamente o
        ponto: dá para comparar as duas.
        """
        origem = self.obter(chave_origem)
        params = {**origem.parametros, **mudancas}
        if hash_parametros(params) == origem.params_hash:
            raise EstrategiaError(
                f"as mudanças não alteram os parâmetros de {chave_origem}")
        nova = self.criar(
            origem.strategy_id, params, mercado=origem.mercado,
            timeframe=origem.timeframe,
            descricao=descricao or f"derivada de {chave_origem}",
            agora_ms=agora_ms)
        nova.historico.append({
            "ts": nova.criada_em_ms, "evento": "derivada",
            "origem": chave_origem, "mudancas": mudancas,
        })
        return nova

    # ---------------------------------------------------------- consulta
    def obter(self, chave: str) -> VersaoEstrategia:
        v = self._versoes.get(chave)
        if v is None:
            raise EstrategiaError(f"versão desconhecida: {chave}")
        return v

    def listar(self, *, strategy_id: str | None = None,
               fase: Fase | None = None) -> list[VersaoEstrategia]:
        out = list(self._versoes.values())
        if strategy_id:
            out = [v for v in out if v.strategy_id == strategy_id]
        if fase:
            out = [v for v in out if v.fase is fase]
        return sorted(out, key=lambda v: (v.strategy_id, v.version))

    def operaveis_em_real(self) -> list[VersaoEstrategia]:
        return self.listar(fase=Fase.REAL_LIMITADO)

    def por_fase(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for v in self._versoes.values():
            out.setdefault(v.fase.value, []).append(v.chave)
        return out

    # --------------------------------------------------------- transições
    def registrar_gate(self, chave: str, resultado: dict[str, Any], *,
                       agora_ms: int | None = None) -> None:
        v = self.obter(chave)
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
        v.ultimo_gate = resultado
        v.atualizada_em_ms = agora
        v.historico.append({
            "ts": agora, "evento": "gate_avaliado",
            "fase": v.fase.value,
            "aprovado": bool(resultado.get("aprovado")),
            "n_reprovacoes": len(resultado.get("reprovacoes", [])),
        })

    def promover(self, chave: str, *,
                 agora_ms: int | None = None) -> VersaoEstrategia:
        """Avança UMA fase. Pular fase é impossível por construção."""
        v = self.obter(chave)
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)

        if v.fase in (Fase.APOSENTADA, Fase.REPROVADA):
            raise EstrategiaError(
                f"{chave} está em {v.fase.value} e não pode ser promovida")
        if v.fase is Fase.REAL_LIMITADO:
            raise EstrategiaError(
                f"{chave} já está na última fase ({v.fase.value}); não existe "
                f"fase 'liberada' neste sistema")

        atual = ORDEM.index(v.fase)
        anterior = v.fase
        v.fase = ORDEM[atual + 1]
        v.atualizada_em_ms = agora
        v.historico.append({
            "ts": agora, "evento": "promovida",
            "de": anterior.value, "para": v.fase.value,
        })
        return v

    def reprovar(self, chave: str, motivo: str, *,
                 agora_ms: int | None = None) -> VersaoEstrategia:
        v = self.obter(chave)
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
        anterior = v.fase
        v.fase = Fase.REPROVADA
        v.motivo_reprovacao = motivo
        v.atualizada_em_ms = agora
        v.historico.append({
            "ts": agora, "evento": "reprovada", "de": anterior.value,
            "motivo": motivo,
        })
        return v

    def aposentar(self, chave: str, motivo: str = "", *,
                  agora_ms: int | None = None) -> VersaoEstrategia:
        v = self.obter(chave)
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
        anterior = v.fase
        v.fase = Fase.APOSENTADA
        v.atualizada_em_ms = agora
        v.historico.append({
            "ts": agora, "evento": "aposentada", "de": anterior.value,
            "motivo": motivo,
        })
        return v

    def rebaixar(self, chave: str, motivo: str, *,
                 agora_ms: int | None = None) -> VersaoEstrategia:
        """Volta uma fase. Usado quando o desempenho ao vivo se deteriora.

        Rebaixar é a operação que falta na maioria dos sistemas: eles sabem
        promover e não sabem reconhecer que a vantagem acabou.
        """
        v = self.obter(chave)
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
        if v.fase not in ORDEM or ORDEM.index(v.fase) == 0:
            raise EstrategiaError(f"{chave} não pode ser rebaixada de "
                                  f"{v.fase.value}")
        anterior = v.fase
        v.fase = ORDEM[ORDEM.index(v.fase) - 1]
        v.atualizada_em_ms = agora
        v.historico.append({
            "ts": agora, "evento": "rebaixada", "de": anterior.value,
            "para": v.fase.value, "motivo": motivo,
        })
        return v

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": len(self._versoes),
            "por_fase": self.por_fase(),
            "operaveis_em_real": [v.chave for v in self.operaveis_em_real()],
            "ordem_das_fases": [f.value for f in ORDEM],
            "descricao_das_fases": {f.value: DESCRICAO_FASE[f] for f in Fase},
            "versoes": [v.to_dict() for v in self.listar()],
        }
