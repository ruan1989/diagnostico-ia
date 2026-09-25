"""Auditoria automática de vazamento de informação futura.

Por que isto precisa ser executável
-----------------------------------
Lookahead é o defeito mais caro de um sistema de trading e o mais fácil de
introduzir sem perceber. Uma única linha que usa `velas[-1]` dentro de um
laço que deveria parar em `i`, ou um `sorted()` aplicado à série inteira
antes de fatiar, produz backtest excelente e conta vazia — e não parece
errado na leitura.

Revisão de código não pega isso de forma confiável. Uma PROVA pega.

A prova
-------
**Invariância de prefixo.** Se uma função calcula o estado no instante `i`
usando apenas informação disponível até `i`, então o resultado dela tem de
ser idêntico quando recebe a série inteira e quando recebe só `velas[:i+1]`.
Se mudar, ela olhou adiante. Não há terceira possibilidade.

A checagem complementar é a **injeção de sentinela**: altera-se uma barra
futura de forma grosseira e confere-se que nada no instante `i` se mexeu.
Ela pega o caso em que a série completa e o prefixo dão o mesmo resultado
por coincidência do dado, e não por construção.

Para rótulos, a pergunta é o espelho: o rótulo em `i` PODE depender de
barras posteriores — é isso que ele mede — mas não pode depender da barra
`i` nem de barras além do horizonte declarado.

Limites conhecidos, encontrados medindo
---------------------------------------
Os dois casos abaixo apareceram ao apontar o auditor para funções
deliberadamente defeituosas, e valem como aviso de uso:

* **a invariância de prefixo pode passar por coincidência do dado.** Uma
  função que normaliza pelo máximo da série inteira não é pega quando o
  máximo global ocorre ANTES de todos os índices testados — ali o máximo do
  prefixo é igual ao total. Por isso a sentinela existe, e por isso as duas
  checagens devem ser usadas juntas;
* **a checagem de horizonte só vê a violação quando há desfecho além do
  horizonte.** Um rotulador que ignora o limite de barras passa em uma série
  onde todo desfecho ocorre em 14 barras contra um horizonte de 48. Auditar
  rotulador exige dado em que o horizonte realmente morda.

O que este módulo não faz
-------------------------
Não prova ausência de vazamento em geral: prova ausência nos pontos
testados, com o dado testado. É uma rede, não um teorema. Uma rede executada
a cada commit, porém, pega a classe de erro que de fato acontece.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from ..models import Candle

# Fator da sentinela: multiplica o preço de uma barra futura para torná-la
# impossível de ignorar. Grande o bastante para que qualquer contaminação
# apareça acima de ruído de ponto flutuante.
FATOR_SENTINELA = 7.0


@dataclass(slots=True)
class Violacao:
    tipo: str
    onde: str
    detalhe: str

    def to_dict(self) -> dict[str, Any]:
        return {"tipo": self.tipo, "onde": self.onde, "detalhe": self.detalhe}


@dataclass(slots=True)
class RelatorioLeakage:
    alvo: str = ""
    verificacoes: int = 0
    violacoes: list[Violacao] = field(default_factory=list)
    nao_testado: list[str] = field(default_factory=list)

    @property
    def limpo(self) -> bool:
        return not self.violacoes and self.verificacoes > 0

    @property
    def veredicto(self) -> str:
        if self.violacoes:
            return "VAZAMENTO"
        if self.verificacoes == 0:
            return "NAO_TESTADO"
        return "LIMPO"

    def to_dict(self) -> dict[str, Any]:
        return {
            "alvo": self.alvo, "veredicto": self.veredicto,
            "limpo": self.limpo, "verificacoes": self.verificacoes,
            "violacoes": [v.to_dict() for v in self.violacoes],
            "nao_testado": self.nao_testado,
            "observacao": (
                "LIMPO significa 'nenhum vazamento nos pontos testados com "
                "este dado'. É uma rede, não um teorema."),
        }

    def texto(self) -> str:
        linhas = [f"AUDITORIA DE VAZAMENTO — {self.alvo}",
                  f"{self.verificacoes} verificação(ões)"]
        for v in self.violacoes:
            linhas.append(f"  VAZAMENTO [{v.tipo}] em {v.onde}: {v.detalhe}")
        for n in self.nao_testado:
            linhas.append(f"  não testado: {n}")
        linhas.append(self.veredicto)
        return "\n".join(linhas)


def _comparavel(valor: Any) -> Any:
    """Reduz um objeto de features a algo comparável campo a campo."""
    if hasattr(valor, "to_dict"):
        try:
            return valor.to_dict()
        except Exception:                               # noqa: BLE001
            pass
    if hasattr(valor, "__dict__"):
        return dict(valor.__dict__)
    if hasattr(valor, "__slots__"):
        return {s: getattr(valor, s, None) for s in valor.__slots__}
    return valor


def _diferencas(a: Any, b: Any) -> list[str]:
    """Campos em que dois resultados diferem, ignorando ruído de ponto flutuante."""
    da, db = _comparavel(a), _comparavel(b)
    if not isinstance(da, dict) or not isinstance(db, dict):
        return [] if da == db else [f"valor: {da!r} != {db!r}"]
    fora: list[str] = []
    for chave in sorted(set(da) | set(db)):
        va, vb = da.get(chave), db.get(chave)
        if isinstance(va, float) and isinstance(vb, float):
            escala = max(1.0, abs(va), abs(vb))
            if abs(va - vb) > 1e-9 * escala:
                fora.append(f"{chave}: {va!r} != {vb!r}")
        elif va != vb:
            fora.append(f"{chave}: {va!r} != {vb!r}")
    return fora


# ------------------------------------------------------- invariância de prefixo
def auditar_invariancia_de_prefixo(
        fn: Callable[[Sequence[Candle], int], Any],
        velas: Sequence[Candle], *,
        indices: Sequence[int] | None = None,
        alvo: str = "função") -> RelatorioLeakage:
    """O resultado em `i` muda quando a série inclui barras depois de `i`?

    Se mudar, a função olhou adiante. É uma prova, não um indício: uma
    função que só usa `velas[:i+1]` não tem como produzir resultado
    diferente ao receber mais barras no fim.
    """
    rel = RelatorioLeakage(alvo=alvo)
    n = len(velas)
    if n < 10:
        rel.nao_testado.append(f"série com {n} velas: curta demais")
        return rel

    # Índices espalhados, incluindo o começo da série: concentrar na segunda
    # metade deixa um máximo global antigo esconder normalização vazada.
    alvos = list(indices) if indices is not None else [
        int(n * f) for f in (0.25, 0.4, 0.55, 0.7, 0.85, 0.95)]
    for i in alvos:
        if not 0 <= i < n - 1:
            continue
        try:
            completo = fn(velas, i)
        except Exception as exc:                        # noqa: BLE001
            rel.nao_testado.append(f"índice {i}: {type(exc).__name__}: {exc}")
            continue
        try:
            prefixo = fn(velas[:i + 1], i)
        except Exception as exc:                        # noqa: BLE001
            rel.nao_testado.append(
                f"índice {i} com prefixo: {type(exc).__name__}: {exc}")
            continue
        rel.verificacoes += 1
        difs = _diferencas(completo, prefixo)
        if difs:
            rel.violacoes.append(Violacao(
                "prefixo", f"índice {i}",
                f"resultado muda quando a série inclui barras posteriores: "
                f"{'; '.join(difs[:4])}"))
    return rel


def auditar_sentinela(fn: Callable[[Sequence[Candle], int], Any],
                      velas: Sequence[Candle], *,
                      indices: Sequence[int] | None = None,
                      alvo: str = "função") -> RelatorioLeakage:
    """Altera barras FUTURAS de forma grosseira. Nada em `i` pode mudar.

    Complementa a invariância de prefixo: aquela pode passar por coincidência
    do dado; esta força a diferença a aparecer.
    """
    rel = RelatorioLeakage(alvo=f"{alvo} (sentinela)")
    n = len(velas)
    if n < 10:
        rel.nao_testado.append(f"série com {n} velas: curta demais")
        return rel

    alvos = list(indices) if indices is not None else [
        int(n * f) for f in (0.3, 0.55, 0.8)]
    for i in alvos:
        if not 0 <= i < n - 2:
            continue
        try:
            antes = fn(velas, i)
        except Exception as exc:                        # noqa: BLE001
            rel.nao_testado.append(f"índice {i}: {type(exc).__name__}: {exc}")
            continue

        adulteradas = list(velas)
        for j in range(i + 1, n):
            v = adulteradas[j]
            adulteradas[j] = Candle(
                ts=v.ts, open=v.open * FATOR_SENTINELA,
                high=v.high * FATOR_SENTINELA, low=v.low * FATOR_SENTINELA,
                close=v.close * FATOR_SENTINELA,
                volume=v.volume * FATOR_SENTINELA)
        try:
            depois = fn(adulteradas, i)
        except Exception as exc:                        # noqa: BLE001
            rel.nao_testado.append(
                f"índice {i} com sentinela: {type(exc).__name__}: {exc}")
            continue

        rel.verificacoes += 1
        difs = _diferencas(antes, depois)
        if difs:
            rel.violacoes.append(Violacao(
                "sentinela", f"índice {i}",
                f"multiplicar as barras posteriores por {FATOR_SENTINELA} "
                f"mudou o resultado em {i}: {'; '.join(difs[:4])}"))
    return rel


# ------------------------------------------------------------- rotulagem
def auditar_rotulagem(
        rotular_fn: Callable[[Sequence[Candle], int], Any],
        velas: Sequence[Candle], *,
        horizonte: int,
        indices: Sequence[int] | None = None,
        alvo: str = "rotulador") -> RelatorioLeakage:
    """O rótulo em `i` depende só das barras em (i, i+horizonte]?

    O espelho da checagem de features: o rótulo PODE olhar o futuro — é isso
    que ele mede. O que ele não pode é:

      * usar a própria barra `i`, cujo máximo já é informação do futuro no
        instante da decisão;
      * usar barras além do horizonte declarado, o que tornaria o rótulo
        incomparável com a regra que a execução real vai seguir.
    """
    rel = RelatorioLeakage(alvo=alvo)
    n = len(velas)
    if n < horizonte + 10:
        rel.nao_testado.append(
            f"série com {n} velas para horizonte {horizonte}: curta demais")
        return rel

    alvos = list(indices) if indices is not None else [
        int(n * f) for f in (0.3, 0.5)]
    for i in alvos:
        if not 0 <= i < n - horizonte - 2:
            continue
        try:
            base = rotular_fn(velas, i)
        except Exception as exc:                        # noqa: BLE001
            rel.nao_testado.append(f"índice {i}: {type(exc).__name__}: {exc}")
            continue

        # 1) A barra da decisão não pode contar.
        com_barra_extrema = list(velas)
        v = com_barra_extrema[i]
        com_barra_extrema[i] = Candle(
            ts=v.ts, open=v.open, high=v.high * FATOR_SENTINELA,
            low=v.low / FATOR_SENTINELA, close=v.close, volume=v.volume)
        try:
            alterado = rotular_fn(com_barra_extrema, i)
        except Exception as exc:                        # noqa: BLE001
            rel.nao_testado.append(f"índice {i} barra i: {exc}")
        else:
            rel.verificacoes += 1
            if _diferencas(base, alterado):
                rel.violacoes.append(Violacao(
                    "barra_da_decisao", f"índice {i}",
                    "esticar máximo e mínimo da PRÓPRIA barra da decisão "
                    "mudou o rótulo: o rotulador olha dentro da vela que "
                    "originou o sinal"))

        # 2) Barras além do horizonte não podem contar.
        alem = list(velas)
        for j in range(i + horizonte + 1, n):
            w = alem[j]
            alem[j] = Candle(
                ts=w.ts, open=w.open * FATOR_SENTINELA,
                high=w.high * FATOR_SENTINELA, low=w.low / FATOR_SENTINELA,
                close=w.close * FATOR_SENTINELA, volume=w.volume)
        try:
            alterado2 = rotular_fn(alem, i)
        except Exception as exc:                        # noqa: BLE001
            rel.nao_testado.append(f"índice {i} além do horizonte: {exc}")
        else:
            rel.verificacoes += 1
            if _diferencas(base, alterado2):
                rel.violacoes.append(Violacao(
                    "horizonte", f"índice {i}",
                    f"alterar barras além de i+{horizonte} mudou o rótulo: "
                    f"o rotulador usa mais futuro do que declara"))
    return rel


# --------------------------------------------------------- divisão temporal
def auditar_divisao_temporal(
        treino_ts: Sequence[int], teste_ts: Sequence[int], *,
        alvo: str = "divisão treino/teste") -> RelatorioLeakage:
    """Treino e teste se sobrepõem no tempo?

    Sobreposição faz a métrica out-of-sample medir dados que o ajuste viu.
    O número resultante é otimista e indistinguível de um número válido, que
    é o que torna esse erro tão perigoso.
    """
    rel = RelatorioLeakage(alvo=alvo)
    if not treino_ts or not teste_ts:
        rel.nao_testado.append("uma das janelas está vazia")
        return rel

    rel.verificacoes += 1
    fim_treino, inicio_teste = max(treino_ts), min(teste_ts)
    if inicio_teste <= fim_treino:
        rel.violacoes.append(Violacao(
            "sobreposicao", "janelas",
            f"o teste começa em {inicio_teste}, dentro do treino que vai até "
            f"{fim_treino}"))

    rel.verificacoes += 1
    comuns = set(treino_ts) & set(teste_ts)
    if comuns:
        rel.violacoes.append(Violacao(
            "instantes_repetidos", "janelas",
            f"{len(comuns)} instante(s) aparecem nas duas janelas; o "
            f"primeiro é {min(comuns)}"))
    return rel


def auditar_ordem_temporal(ts: Sequence[int], *,
                           alvo: str = "série") -> RelatorioLeakage:
    """A série está em ordem cronológica e sem instantes repetidos?

    Série fora de ordem quebra toda garantia de prefixo: `velas[:i+1]`
    deixa de significar "tudo até o instante i".
    """
    rel = RelatorioLeakage(alvo=alvo)
    if len(ts) < 2:
        rel.nao_testado.append("série com menos de duas observações")
        return rel

    rel.verificacoes += 1
    fora = [k for k in range(len(ts) - 1) if ts[k] >= ts[k + 1]]
    if fora:
        rel.violacoes.append(Violacao(
            "ordem", f"posições {fora[:5]}",
            f"{len(fora)} par(es) fora de ordem cronológica; sem ordem, "
            f"fatiar por índice não significa fatiar por tempo"))
    return rel


# ------------------------------------------------------------ padronização
def auditar_padronizacao(medias_treino: Sequence[float],
                         xs_treino: Sequence[Sequence[float]], *,
                         alvo: str = "padronizador") -> RelatorioLeakage:
    """As estatísticas de padronização vieram só do treino?

    Padronizar com a média do conjunto completo é vazamento silencioso: a
    média do teste entra no treino, as métricas melhoram, e nada no código
    parece errado.
    """
    rel = RelatorioLeakage(alvo=alvo)
    if not xs_treino or not medias_treino:
        rel.nao_testado.append("sem dados de treino para conferir")
        return rel

    n = len(xs_treino)
    d = min(len(medias_treino), len(xs_treino[0]))
    for j in range(d):
        esperado = sum(x[j] for x in xs_treino) / n
        rel.verificacoes += 1
        escala = max(1.0, abs(esperado))
        if abs(medias_treino[j] - esperado) > 1e-9 * escala:
            rel.violacoes.append(Violacao(
                "padronizacao", f"feature {j}",
                f"média guardada {medias_treino[j]:.6f} difere da média do "
                f"treino {esperado:.6f}: as estatísticas vieram de outro "
                f"conjunto"))
    return rel


# ------------------------------------------------------------------ conjunto
def juntar(*relatorios: RelatorioLeakage) -> RelatorioLeakage:
    """Consolida vários relatórios em um só."""
    total = RelatorioLeakage(alvo="auditoria completa")
    for r in relatorios:
        total.verificacoes += r.verificacoes
        for v in r.violacoes:
            total.violacoes.append(Violacao(v.tipo, f"{r.alvo}/{v.onde}",
                                            v.detalhe))
        total.nao_testado.extend(f"{r.alvo}: {n}" for n in r.nao_testado)
    return total


__all__ = [
    "FATOR_SENTINELA", "RelatorioLeakage", "Violacao",
    "auditar_divisao_temporal", "auditar_invariancia_de_prefixo",
    "auditar_ordem_temporal", "auditar_padronizacao", "auditar_rotulagem",
    "auditar_sentinela", "juntar",
]
