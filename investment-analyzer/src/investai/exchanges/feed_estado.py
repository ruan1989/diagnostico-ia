"""Máquina de estado de um feed em tempo real, sem nenhum I/O.

Por que separada da conexão
---------------------------
A parte difícil de um feed não é abrir o socket: é saber, a qualquer
instante, se o que está na memória ainda descreve o mercado. Essa lógica —
frescor, lacuna após reconexão, sequência fora de ordem — é onde os erros
moram, e ela não precisa de rede para ser testada.

Separar permite testar exaustivamente o que importa de forma síncrona e
determinística, e deixa para a camada de I/O apenas o que só um socket de
verdade prova.

O perigo que este módulo existe para evitar
-------------------------------------------
**Um feed que congela silenciosamente é pior que um feed que cai.**

Se a conexão cai, o sistema percebe e para. Se ela continua aberta mas para
de entregar mensagens, o último preço recebido fica na memória parecendo
atual, e o sistema segue decidindo em cima de um número velho — dimensionando
posição, conferindo stop, calculando distância de liquidação, tudo contra um
preço que não existe mais.

Por isso `fresco` é medido contra o RELÓGIO, não contra a chegada da última
mensagem. E por isso a idade entra em toda leitura, em vez de ficar num campo
que alguém pode esquecer de conferir.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Um canal é considerado velho quando passa deste tempo sem mensagem. O valor
# depende do canal: ticker de par líquido chega várias vezes por segundo,
# candle de 1H chega uma vez por hora.
TOLERANCIA_PADRAO_MS = 15_000

# Quanto tempo de silêncio dispara a reconexão preventiva. Menor que a
# tolerância de frescor: a ideia é reconectar ANTES de o dado apodrecer.
SILENCIO_PARA_RECONECTAR_MS = 8_000


@dataclass(slots=True)
class Mensagem:
    canal: str
    dados: Any
    recebida_em_ms: int
    sequencia: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"canal": self.canal, "recebida_em_ms": self.recebida_em_ms,
                "sequencia": self.sequencia}


@dataclass(slots=True)
class Lacuna:
    """Um intervalo em que o feed não estava entregando.

    Guardado porque a consequência prática é concreta: entre `inicio` e
    `fim` o sistema não viu o mercado, e qualquer série montada a partir do
    feed tem um buraco ali que precisa ser preenchido por REST.
    """

    canal: str
    inicio_ms: int
    fim_ms: int
    motivo: str

    @property
    def duracao_ms(self) -> int:
        return max(0, self.fim_ms - self.inicio_ms)

    def to_dict(self) -> dict[str, Any]:
        return {"canal": self.canal, "inicio_ms": self.inicio_ms,
                "fim_ms": self.fim_ms, "duracao_ms": self.duracao_ms,
                "motivo": self.motivo}


@dataclass(slots=True)
class EstadoCanal:
    canal: str
    tolerancia_ms: int = TOLERANCIA_PADRAO_MS
    ultima: Mensagem | None = None
    mensagens: int = 0
    fora_de_ordem: int = 0
    ultima_sequencia: int | None = None

    def idade_ms(self, agora_ms: int) -> int | None:
        if self.ultima is None:
            return None
        return max(0, agora_ms - self.ultima.recebida_em_ms)

    def fresco(self, agora_ms: int) -> bool:
        idade = self.idade_ms(agora_ms)
        return idade is not None and idade <= self.tolerancia_ms

    def to_dict(self, agora_ms: int) -> dict[str, Any]:
        return {
            "canal": self.canal, "mensagens": self.mensagens,
            "fora_de_ordem": self.fora_de_ordem,
            "idade_ms": self.idade_ms(agora_ms),
            "tolerancia_ms": self.tolerancia_ms,
            "fresco": self.fresco(agora_ms),
            "ultima_sequencia": self.ultima_sequencia,
        }


class EstadoFeed:
    """O que o feed sabe, e há quanto tempo sabe."""

    def __init__(self, canais: dict[str, int] | None = None, *,
                 tolerancia_padrao_ms: int = TOLERANCIA_PADRAO_MS):
        self.tolerancia_padrao_ms = tolerancia_padrao_ms
        self.canais: dict[str, EstadoCanal] = {}
        for canal, tol in (canais or {}).items():
            self.canais[canal] = EstadoCanal(canal, tol or tolerancia_padrao_ms)
        self.conectado = False
        self.conectado_em_ms = 0
        self.desconectado_em_ms = 0
        self.reconexoes = 0
        self.lacunas: list[Lacuna] = []
        self.erros: list[str] = []

    # ------------------------------------------------------------ canais
    def registrar(self, canal: str, *, tolerancia_ms: int | None = None
                  ) -> EstadoCanal:
        est = self.canais.get(canal)
        if est is None:
            est = EstadoCanal(canal, tolerancia_ms or self.tolerancia_padrao_ms)
            self.canais[canal] = est
        elif tolerancia_ms:
            est.tolerancia_ms = tolerancia_ms
        return est

    def mensagem(self, canal: str, dados: Any, agora_ms: int, *,
                 sequencia: int | None = None) -> Mensagem:
        est = self.registrar(canal)
        if (sequencia is not None and est.ultima_sequencia is not None
                and sequencia <= est.ultima_sequencia):
            # Mensagem repetida ou atrasada. Contar e DESCARTAR: aplicar uma
            # atualização antiga por cima de uma nova faria o preço andar
            # para trás sem que nada denunciasse.
            est.fora_de_ordem += 1
            return est.ultima if est.ultima else Mensagem(canal, dados,
                                                          agora_ms, sequencia)
        msg = Mensagem(canal=canal, dados=dados, recebida_em_ms=agora_ms,
                       sequencia=sequencia)
        est.ultima = msg
        est.mensagens += 1
        if sequencia is not None:
            est.ultima_sequencia = sequencia
        return msg

    def ultimo(self, canal: str, agora_ms: int) -> Mensagem | None:
        """Última mensagem do canal, ou None se ela estiver velha.

        Devolver None em vez de um dado velho é deliberado. Quem chama tem
        como tratar ausência; ninguém trata bem um número que parece atual e
        não é.
        """
        est = self.canais.get(canal)
        if est is None or est.ultima is None:
            return None
        return est.ultima if est.fresco(agora_ms) else None

    def ultimo_mesmo_velho(self, canal: str) -> tuple[Mensagem | None, int | None]:
        """A última mensagem e a idade dela, sem filtro.

        Existe para diagnóstico e para o caso em que quem chama decide
        conscientemente usar dado velho — e aí recebe a idade junto, para
        não poder alegar que não sabia.
        """
        est = self.canais.get(canal)
        if est is None or est.ultima is None:
            return None, None
        return est.ultima, est.ultima.recebida_em_ms

    # ------------------------------------------------------ conexão
    def conectou(self, agora_ms: int) -> None:
        # Reconectar depois de uma queda abre uma lacuna: entre a queda e
        # agora, o feed não viu o mercado. Registrar isso é o que permite
        # preencher o buraco por REST em vez de emendar séries como se nada
        # tivesse acontecido.
        if self.desconectado_em_ms and not self.conectado:
            self.reconexoes += 1
            for canal in self.canais:
                self.lacunas.append(Lacuna(
                    canal=canal, inicio_ms=self.desconectado_em_ms,
                    fim_ms=agora_ms, motivo="reconexão"))
        self.conectado = True
        self.conectado_em_ms = agora_ms
        self.desconectado_em_ms = 0

    def desconectou(self, agora_ms: int, motivo: str = "") -> None:
        self.conectado = False
        self.desconectado_em_ms = agora_ms
        if motivo:
            self.erros.append(motivo)
            del self.erros[:-20]

    def precisa_reconectar(self, agora_ms: int, *,
                           silencio_ms: int = SILENCIO_PARA_RECONECTAR_MS
                           ) -> bool:
        """Silêncio longo com conexão aberta é motivo para reconectar.

        Um socket pode continuar aberto e parar de entregar. Esperar o
        timeout do TCP significaria minutos operando com preço congelado.
        """
        if not self.conectado:
            return True
        if not self.canais:
            return False
        idades = [c.idade_ms(agora_ms) for c in self.canais.values()]
        if all(i is None for i in idades):
            # Conectado e nenhuma mensagem ainda: só reconecta se já faz
            # tempo desde a conexão.
            return agora_ms - self.conectado_em_ms > silencio_ms
        return all(i is not None and i > silencio_ms
                   for i in idades if i is not None)

    # ------------------------------------------------------ inspeção
    def canais_velhos(self, agora_ms: int) -> list[str]:
        return sorted(c.canal for c in self.canais.values()
                      if not c.fresco(agora_ms))

    def confiavel(self, agora_ms: int) -> bool:
        """Todos os canais frescos e nenhuma lacuna pendente."""
        return (self.conectado and bool(self.canais)
                and not self.canais_velhos(agora_ms)
                and not self.lacunas)

    def consumir_lacunas(self) -> list[Lacuna]:
        """Devolve e limpa as lacunas.

        Quem consome assume a responsabilidade de preenchê-las por REST. A
        limpeza é parte do contrato: uma lacuna que fica registrada para
        sempre faria `confiavel` nunca mais voltar a ser verdade.
        """
        fora = list(self.lacunas)
        self.lacunas.clear()
        return fora

    def estado(self, agora_ms: int) -> dict[str, Any]:
        return {
            "conectado": self.conectado,
            "confiavel": self.confiavel(agora_ms),
            "reconexoes": self.reconexoes,
            "canais": {c: e.to_dict(agora_ms) for c, e in
                       sorted(self.canais.items())},
            "canais_velhos": self.canais_velhos(agora_ms),
            "lacunas_pendentes": [g.to_dict() for g in self.lacunas],
            "ultimos_erros": self.erros[-5:],
            "observacao": (
                "Um canal velho devolve None em vez do último valor: um feed "
                "que congela em silêncio é pior que um feed que cai, porque "
                "o sistema segue decidindo em cima de um preço que já não "
                "existe."),
        }


__all__ = [
    "EstadoCanal", "EstadoFeed", "Lacuna", "Mensagem",
    "SILENCIO_PARA_RECONECTAR_MS", "TOLERANCIA_PADRAO_MS",
]
