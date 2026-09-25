"""Métricas por segmento, com origens que nunca se misturam.

Duas regras que este módulo impõe
---------------------------------

**1. Origens diferentes nunca entram na mesma conta.**

Um backtest, um paper trading e uma operação real medem coisas diferentes. O
backtest não sofre slippage real nem fila de ordem; o paper sofre parte; o
real sofre tudo, mais o efeito de quem está do outro lado. Somar os três
produz uma expectativa que não descreve nenhum dos três — e que é sempre
melhor que a real, porque as fontes otimistas costumam ter mais operações.

`agregar` recusa lotes com origem misturada. Não avisa: recusa. Um aviso
seria ignorado uma vez e depois sempre.

**2. A média global responde uma pergunta sobre o passado.**

Um erro tentador é procurar, na quebra por segmento, o caso em que o total é
positivo e todos os segmentos são negativos. Ele não existe: a média global é
a média PONDERADA das médias dos segmentos, então ela não tem como ficar do
outro lado de todas elas. Esta versão do módulo chegou a ter uma checagem
assim, e a validação numérica mostrou que ela nunca podia disparar.

O que existe, e importa, são dois casos diferentes:

* **composição.** A expectativa total foi medida com a mistura de regimes do
  PASSADO. Se o mercado agora está predominantemente em um regime onde a
  estratégia é pior, a expectativa que vale é outra. `reponderar` recalcula
  o total sob uma composição informada, e a diferença costuma ser grande;
* **reversão entre grupos.** Aí sim o paradoxo de Simpson aparece de verdade:
  a estratégia A pode ganhar de B no agregado e perder para B em TODOS os
  segmentos, porque as duas foram medidas em misturas diferentes.
  `comparar_grupos` procura esse caso.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Sequence

from ..models import Trade
from ..validation.stats import IntervaloConfianca, ic_media, wilson

# Amostra mínima para um segmento sustentar conclusão. Abaixo disso ele
# aparece no relatório marcado como não conclusivo, em vez de ficar de fora:
# esconder segmento pequeno daria a impressão de que a estratégia só opera
# onde tem dados.
MIN_SEGMENTO = 30


class Origem(str, Enum):
    """De onde vieram os resultados. Nunca se somam."""

    BACKTEST = "backtest"
    OUT_OF_SAMPLE = "out_of_sample"
    PAPER = "paper"
    SHADOW = "shadow"
    DEMO = "demo"
    LIVE = "live"


# Quanto cada origem se aproxima da realidade de execução. Serve para
# ordenar relatórios e para dizer, quando duas discordam, qual delas é a que
# conta.
REALISMO: dict[Origem, int] = {
    Origem.BACKTEST: 1,
    Origem.OUT_OF_SAMPLE: 2,
    Origem.PAPER: 3,
    Origem.SHADOW: 4,
    Origem.DEMO: 5,
    Origem.LIVE: 6,
}

DESCRICAO_ORIGEM: dict[Origem, str] = {
    Origem.BACKTEST: "histórico completo; não sofre slippage real nem fila",
    Origem.OUT_OF_SAMPLE: "histórico que não participou do ajuste",
    Origem.PAPER: "tempo real com dinheiro simulado, com spread e latência",
    Origem.SHADOW: "decisões reais registradas antes do preço andar, sem ordem",
    Origem.DEMO: "ordens no ambiente de teste da corretora",
    Origem.LIVE: "dinheiro real; a única que conta de verdade",
}


class OrigemMisturada(ValueError):
    pass


@dataclass(slots=True)
class TradeAnotado:
    """Um trade com o contexto necessário para segmentar.

    O contexto precisa ser gravado no momento da operação. Deduzir o regime
    depois, olhando o gráfico, usaria informação que não existia na decisão
    — e produziria uma segmentação que descreve o passado, não a operação.
    """

    trade: Trade
    origem: Origem
    regime: str = ""
    timeframe: str = ""
    estrategia: str = ""

    @property
    def symbol(self) -> str:
        return self.trade.symbol

    @property
    def r(self) -> float:
        return self.trade.pnl_r


@dataclass(slots=True)
class Metrica:
    """O que uma amostra de trades diz — e com quanta certeza."""

    n: int = 0
    ganhos: int = 0
    perdas: int = 0
    expectativa_r: float = 0.0
    soma_r: float = 0.0
    melhor_r: float = 0.0
    pior_r: float = 0.0
    ic_expectativa: IntervaloConfianca | None = None
    ic_win_rate: IntervaloConfianca | None = None

    @property
    def win_rate(self) -> float:
        return self.ganhos / self.n if self.n else 0.0

    @property
    def conclusiva(self) -> bool:
        return self.n >= MIN_SEGMENTO

    @property
    def positiva_com_confianca(self) -> bool:
        """Expectativa positiva cujo piso do intervalo exclui zero.

        É o único caso em que "esta estratégia dá dinheiro neste segmento" é
        uma afirmação sustentada. Média positiva com piso negativo é média
        positiva de amostra pequena.
        """
        return (self.conclusiva and self.ic_expectativa is not None
                and self.ic_expectativa.inferior > 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n, "ganhos": self.ganhos, "perdas": self.perdas,
            "win_rate": round(self.win_rate, 4),
            "expectativa_r": round(self.expectativa_r, 4),
            "soma_r": round(self.soma_r, 3),
            "melhor_r": round(self.melhor_r, 3),
            "pior_r": round(self.pior_r, 3),
            "conclusiva": self.conclusiva,
            "positiva_com_confianca": self.positiva_com_confianca,
            "ic_expectativa": (self.ic_expectativa.to_dict()
                               if self.ic_expectativa else None),
            "ic_win_rate": (self.ic_win_rate.to_dict()
                            if self.ic_win_rate else None),
        }


def calcular(rs: Sequence[float], *, confianca: float = 0.95) -> Metrica:
    """Métrica de uma lista de resultados em R."""
    m = Metrica(n=len(rs))
    if not rs:
        return m
    m.ganhos = sum(1 for r in rs if r > 0)
    m.perdas = sum(1 for r in rs if r <= 0)
    m.soma_r = sum(rs)
    m.expectativa_r = m.soma_r / m.n
    m.melhor_r = max(rs)
    m.pior_r = min(rs)
    if m.n >= 2:
        m.ic_expectativa = ic_media(rs, confianca=confianca)
    m.ic_win_rate = wilson(m.ganhos, m.n, confianca)
    return m


@dataclass(slots=True)
class RelatorioSegmentado:
    origem: Origem
    chave: str                      # "regime", "symbol", "timeframe", ...
    total: Metrica = field(default_factory=Metrica)
    segmentos: dict[str, Metrica] = field(default_factory=dict)
    avisos: list[str] = field(default_factory=list)

    @property
    def conclusivos(self) -> dict[str, Metrica]:
        return {k: v for k, v in self.segmentos.items() if v.conclusiva}

    @property
    def composicao(self) -> dict[str, float]:
        """Fração da amostra em cada segmento.

        É o que torna a expectativa total uma medida do passado: ela foi
        calculada com ESTA mistura, e o mercado não é obrigado a repeti-la.
        """
        if self.total.n == 0:
            return {}
        return {k: m.n / self.total.n for k, m in self.segmentos.items()}

    @property
    def dispersao_entre_segmentos(self) -> float:
        """Distância entre o melhor e o pior segmento conclusivo, em R.

        Dispersão alta significa que a média esconde comportamentos muito
        diferentes, e que a composição futura importa mais que o número.
        """
        conc = self.conclusivos
        if len(conc) < 2:
            return 0.0
        valores = [m.expectativa_r for m in conc.values()]
        return max(valores) - min(valores)

    def reponderar(self, composicao: dict[str, float]) -> float | None:
        """Expectativa total sob outra mistura de segmentos.

        Responde "se o mercado passar a ser 70% lateral, quanto esta
        estratégia espera?". Usa só segmentos conclusivos; se a composição
        informada pesa um segmento sem amostra suficiente, devolve None em
        vez de inventar.
        """
        conc = self.conclusivos
        if not conc or not composicao:
            return None
        peso_total = 0.0
        soma = 0.0
        for nome, peso in composicao.items():
            if peso <= 0:
                continue
            m = conc.get(nome)
            if m is None:
                return None
            soma += peso * m.expectativa_r
            peso_total += peso
        if peso_total <= 0:
            return None
        return soma / peso_total

    def to_dict(self) -> dict[str, Any]:
        return {
            "origem": self.origem.value,
            "origem_descricao": DESCRICAO_ORIGEM[self.origem],
            "chave": self.chave,
            "total": self.total.to_dict(),
            "segmentos": {k: v.to_dict() for k, v in
                          sorted(self.segmentos.items())},
            "n_conclusivos": len(self.conclusivos),
            "composicao": {k: round(v, 4)
                           for k, v in self.composicao.items()},
            "dispersao_entre_segmentos": round(
                self.dispersao_entre_segmentos, 4),
            "avisos": self.avisos,
            "observacao": (
                f"Todos os números acima vêm de {self.origem.value} "
                f"({DESCRICAO_ORIGEM[self.origem]}). Resultados de origens "
                f"diferentes NÃO são somados neste sistema."),
        }


def _conferir_origem(anotados: Sequence[TradeAnotado]) -> Origem:
    origens = {a.origem for a in anotados}
    if not origens:
        raise OrigemMisturada("nenhum trade informado")
    if len(origens) > 1:
        nomes = ", ".join(sorted(o.value for o in origens))
        raise OrigemMisturada(
            f"o lote mistura origens ({nomes}). Backtest, paper e real medem "
            f"coisas diferentes, e a média dos três é sempre melhor que a "
            f"real — porque as fontes otimistas costumam ter mais operações. "
            f"Separe por origem antes de agregar")
    return origens.pop()


def agregar(anotados: Sequence[TradeAnotado],
            chave: str | Callable[[TradeAnotado], str], *,
            confianca: float = 0.95) -> RelatorioSegmentado:
    """Agrupa por um atributo e mede cada grupo.

    Recusa lotes com origem misturada. É recusa, e não aviso: um aviso seria
    ignorado uma vez e depois sempre.
    """
    origem = _conferir_origem(anotados)

    if callable(chave):
        extrair = chave
        nome_chave = getattr(chave, "__name__", "custom")
    else:
        nome_chave = chave
        def extrair(a: TradeAnotado) -> str:            # noqa: E306
            if chave == "symbol":
                return a.symbol
            return str(getattr(a, chave, "") or "(sem valor)")

    rel = RelatorioSegmentado(origem=origem, chave=nome_chave)
    rel.total = calcular([a.r for a in anotados], confianca=confianca)

    grupos: dict[str, list[float]] = {}
    for a in anotados:
        grupos.setdefault(extrair(a) or "(sem valor)", []).append(a.r)
    rel.segmentos = {k: calcular(v, confianca=confianca)
                     for k, v in grupos.items()}

    # ------------------------------------------------------------- avisos
    pequenos = [k for k, m in rel.segmentos.items() if not m.conclusiva]
    if pequenos:
        rel.avisos.append(
            f"{len(pequenos)} segmento(s) abaixo de {MIN_SEGMENTO} operações "
            f"({', '.join(sorted(pequenos)[:5])}): os números deles descrevem "
            f"a amostra, não o segmento")
    if not rel.conclusivos:
        rel.avisos.append(
            f"nenhum segmento atingiu {MIN_SEGMENTO} operações; a quebra "
            f"acima ainda não sustenta nenhuma conclusão por segmento")
    if rel.dispersao_entre_segmentos > 0.30:
        melhor = max(rel.conclusivos, key=lambda k: rel.conclusivos[k].expectativa_r)
        pior = min(rel.conclusivos, key=lambda k: rel.conclusivos[k].expectativa_r)
        rel.avisos.append(
            f"a expectativa vai de {rel.conclusivos[pior].expectativa_r:+.3f}R "
            f"em '{pior}' a {rel.conclusivos[melhor].expectativa_r:+.3f}R em "
            f"'{melhor}'. A média total só vale enquanto a mistura de "
            f"segmentos se repetir: hoje ela é "
            + ", ".join(f"{k} {v:.0%}" for k, v in
                        sorted(rel.composicao.items(),
                               key=lambda p: -p[1])[:3]))

    positivos = [k for k, m in rel.conclusivos.items()
                 if m.positiva_com_confianca]
    negativos = [k for k, m in rel.conclusivos.items()
                 if m.conclusiva and m.expectativa_r < 0]
    if positivos and negativos:
        rel.avisos.append(
            f"a estratégia é positiva com confiança em {', '.join(positivos)} "
            f"e negativa em {', '.join(negativos)}: operar só onde ela "
            f"funciona vale mais que melhorar a média")
    if origem is not Origem.LIVE:
        rel.avisos.append(
            f"origem {origem.value}: {DESCRICAO_ORIGEM[origem]}. Só "
            f"resultado em dinheiro real mede o que de fato acontece")
    return rel


def separar_por_origem(anotados: Iterable[TradeAnotado]
                       ) -> dict[Origem, list[TradeAnotado]]:
    """Divide um lote misturado. O passo obrigatório antes de agregar."""
    out: dict[Origem, list[TradeAnotado]] = {}
    for a in anotados:
        out.setdefault(a.origem, []).append(a)
    return out


def comparar_origens(anotados: Sequence[TradeAnotado]
                     ) -> dict[str, Any]:
    """Mede cada origem separadamente e mostra a degradação entre elas.

    A comparação é o ponto: uma estratégia com 0,4R no backtest e 0,05R no
    paper não tem 0,2R de expectativa — ela tem 0,05R e um backtest
    otimista. A tabela deixa isso visível em vez de deixar para a intuição.
    """
    por_origem = separar_por_origem(anotados)
    linhas = []
    for origem in sorted(por_origem, key=lambda o: REALISMO[o]):
        m = calcular([a.r for a in por_origem[origem]])
        linhas.append({
            "origem": origem.value,
            "realismo": REALISMO[origem],
            "descricao": DESCRICAO_ORIGEM[origem],
            **m.to_dict(),
        })

    degradacoes = []
    for anterior, atual in zip(linhas, linhas[1:]):
        base = anterior["expectativa_r"]
        if abs(base) > 1e-9:
            queda = (base - atual["expectativa_r"]) / abs(base)
            degradacoes.append({
                "de": anterior["origem"], "para": atual["origem"],
                "queda_pct": round(queda * 100, 1),
                "de_r": base, "para_r": atual["expectativa_r"],
            })

    mais_real = linhas[-1] if linhas else None
    return {
        "linhas": linhas,
        "degradacoes": degradacoes,
        "origem_mais_real": mais_real["origem"] if mais_real else None,
        "expectativa_que_conta": (mais_real["expectativa_r"]
                                  if mais_real else None),
        "observacao": (
            "Quando duas origens discordam, vale a mais próxima da execução "
            "real. Uma estratégia com 0,4R no backtest e 0,05R no paper não "
            "tem 0,2R: tem 0,05R e um backtest otimista."),
    }


def comparar_grupos(grupo_a: Sequence[TradeAnotado],
                    grupo_b: Sequence[TradeAnotado],
                    chave: str, *,
                    nome_a: str = "A", nome_b: str = "B",
                    confianca: float = 0.95) -> dict[str, Any]:
    """Compara dois grupos no agregado E em cada segmento.

    É aqui que o paradoxo de Simpson aparece de verdade: A pode ganhar de B
    no total e perder para B em TODOS os segmentos, quando os dois foram
    medidos em misturas diferentes de mercado. O total, nesse caso, está
    comparando a sorte da composição, não as estratégias.

    Os dois grupos precisam ter a mesma origem — comparar backtest de um com
    paper do outro mede a diferença entre as fontes, não entre as
    estratégias.
    """
    origem_a = _conferir_origem(grupo_a)
    origem_b = _conferir_origem(grupo_b)
    if origem_a is not origem_b:
        raise OrigemMisturada(
            f"grupo {nome_a} vem de {origem_a.value} e grupo {nome_b} de "
            f"{origem_b.value}: a diferença mediria as fontes, não as "
            f"estratégias")

    ra = agregar(grupo_a, chave, confianca=confianca)
    rb = agregar(grupo_b, chave, confianca=confianca)
    total_a = ra.total.expectativa_r
    total_b = rb.total.expectativa_r
    vence_no_total = nome_a if total_a > total_b else nome_b

    comuns = sorted(set(ra.conclusivos) & set(rb.conclusivos))
    por_segmento = []
    for seg in comuns:
        ea = ra.conclusivos[seg].expectativa_r
        eb = rb.conclusivos[seg].expectativa_r
        por_segmento.append({
            "segmento": seg,
            f"expectativa_{nome_a}": round(ea, 4),
            f"expectativa_{nome_b}": round(eb, 4),
            "vence": nome_a if ea > eb else nome_b,
            "n_a": ra.conclusivos[seg].n, "n_b": rb.conclusivos[seg].n,
        })

    vencedores = {linha["vence"] for linha in por_segmento}
    simpson = bool(
        len(comuns) >= 2 and len(vencedores) == 1
        and vencedores != {vence_no_total})

    saida = {
        "origem": origem_a.value,
        "chave": chave,
        f"total_{nome_a}": round(total_a, 4),
        f"total_{nome_b}": round(total_b, 4),
        "vence_no_total": vence_no_total,
        "segmentos_comparaveis": len(comuns),
        "por_segmento": por_segmento,
        "composicao_a": {k: round(v, 4) for k, v in ra.composicao.items()},
        "composicao_b": {k: round(v, 4) for k, v in rb.composicao.items()},
        "paradoxo_de_simpson": simpson,
        "avisos": [],
    }
    if simpson:
        perdedor = vencedores.pop()
        saida["avisos"].append(
            f"PARADOXO DE SIMPSON: {vence_no_total} ganha no agregado, mas "
            f"{perdedor} ganha em TODOS os {len(comuns)} segmentos "
            f"comparáveis. A diferença no total vem da mistura de segmentos "
            f"em que cada um foi medido, não de vantagem. Decidir pelo total "
            f"escolheria a pior das duas em qualquer cenário isolado")
    if not comuns:
        saida["avisos"].append(
            "nenhum segmento com amostra suficiente nos DOIS grupos: a "
            "comparação por segmento não pôde ser feita")
    return saida


__all__ = [
    "DESCRICAO_ORIGEM", "MIN_SEGMENTO", "Metrica", "Origem",
    "OrigemMisturada", "REALISMO", "RelatorioSegmentado", "TradeAnotado",
    "agregar", "calcular", "comparar_grupos", "comparar_origens",
    "separar_por_origem",
]
