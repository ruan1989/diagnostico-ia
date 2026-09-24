"""Detecção de drift: o mundo mudou, e o modelo não sabe.

Quatro coisas diferentes que se costumam chamar de "o modelo parou de
funcionar", e que exigem respostas diferentes:

* **drift de dado** — a distribuição das features mudou. O modelo está
  recebendo entradas que não parecem nada com as do treino. Pode até acertar,
  mas está extrapolando;
* **drift de conceito** — a relação entre features e desfecho mudou. As
  entradas continuam familiares, as respostas não. É o pior dos quatro,
  porque nada nas entradas denuncia;
* **drift de performance** — o resultado realizado piorou em relação ao que
  foi validado. É o único que o operador percebe sozinho, e o mais fácil de
  confundir com azar;
* **drift de regime** — o mercado mudou de estado. Não é defeito do modelo:
  é o modelo sendo aplicado fora do contexto em que foi medido.

O erro que este módulo evita
----------------------------
Chamar azar de drift. Uma sequência ruim de dez operações é comum em uma
estratégia perfeitamente boa — a distribuição de resultados em R tem cauda.
Por isso o drift de performance é julgado por intervalo de confiança da
DIFERENÇA entre as duas amostras, não por limiar em cima da média recente.
Sem isso, o sistema desligaria a estratégia a cada série azarada e religaria
a cada série sortuda, o que é ruído virando decisão.

O que ele faz
-------------
Mede e reporta, com ação recomendada. Não retreina sozinho e não despromove
sozinho: as duas coisas mudam o que vai a mercado, e essa decisão fica com
quem responde pelo dinheiro. `ModelRegistry.despromover` existe para o
momento em que essa decisão é tomada.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..validation.calibracao import avaliar_calibracao
from ..validation.stats import ic_media

# Faixas do PSI (Population Stability Index), na leitura usual de crédito,
# que é onde a medida nasceu:
PSI_ESTAVEL = 0.10
PSI_MODERADO = 0.25

# Observações mínimas por janela. Abaixo disso, comparar distribuições
# compara ruído.
MIN_JANELA = 50

# Operações mínimas para julgar performance. Menos que isso e o intervalo de
# confiança da diferença cobre tudo, então nenhuma conclusão é possível.
MIN_TRADES = 30

NIVEIS = ("nao_medido", "sem_amostra", "estavel", "atencao", "drift")


@dataclass(slots=True)
class Achado:
    """Um tipo de drift, medido."""

    tipo: str
    nivel: str                 # estavel | atencao | drift | sem_amostra
    metrica: str
    valor: float | None
    detalhe: str
    acao: str = ""

    @property
    def alarmante(self) -> bool:
        return self.nivel == "drift"

    def to_dict(self) -> dict[str, Any]:
        return {"tipo": self.tipo, "nivel": self.nivel,
                "metrica": self.metrica,
                "valor": (round(self.valor, 4)
                          if self.valor is not None else None),
                "detalhe": self.detalhe, "acao": self.acao}


@dataclass(slots=True)
class RelatorioDrift:
    achados: list[Achado] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def drift(self) -> list[Achado]:
        return [a for a in self.achados if a.nivel == "drift"]

    @property
    def atencao(self) -> list[Achado]:
        return [a for a in self.achados if a.nivel == "atencao"]

    @property
    def sem_amostra(self) -> list[Achado]:
        return [a for a in self.achados if a.nivel == "sem_amostra"]

    @property
    def nivel(self) -> str:
        # Sem nenhuma checagem executada o nível é `nao_medido`, nunca
        # `estavel`. Devolver "estável" para um relatório vazio pintaria a
        # tela de verde por não ter medido nada — que é a confusão que este
        # módulo inteiro existe para evitar.
        if not self.achados:
            return "nao_medido"
        if self.drift:
            return "drift"
        if self.atencao:
            return "atencao"
        if all(a.nivel == "sem_amostra" for a in self.achados):
            return "sem_amostra"
        return "estavel"

    @property
    def exige_acao(self) -> bool:
        return bool(self.drift)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nivel": self.nivel,
            "exige_acao": self.exige_acao,
            "achados": [a.to_dict() for a in self.achados],
            "drift": [a.tipo for a in self.drift],
            "atencao": [a.tipo for a in self.atencao],
            "sem_amostra": [a.tipo for a in self.sem_amostra],
            "avisos": self.avisos,
            "observacao": (
                "Este relatório mede e recomenda; não retreina nem despromove "
                "sozinho. As duas coisas mudam o que vai a mercado."),
        }


# ------------------------------------------------------------ drift de dado
def psi(referencia: Sequence[float], atual: Sequence[float], *,
        n_faixas: int = 10) -> float | None:
    """Population Stability Index entre duas distribuições.

    As faixas vêm dos QUANTIS DA REFERÊNCIA, não de largura fixa: o que
    interessa é quanto da massa saiu de onde estava, e faixas fixas em uma
    distribuição assimétrica deixariam quase tudo em um balde só.

    Devolve None quando alguma janela é pequena demais para comparar.
    """
    ref = sorted(float(v) for v in referencia)
    cur = [float(v) for v in atual]
    if len(ref) < MIN_JANELA or len(cur) < MIN_JANELA:
        return None

    # Cortes por quantil da referência, sem repetição (distribuição muito
    # concentrada pode gerar cortes iguais).
    cortes: list[float] = []
    for i in range(1, n_faixas):
        pos = i * len(ref) / n_faixas
        idx = min(len(ref) - 1, int(pos))
        v = ref[idx]
        if not cortes or v > cortes[-1]:
            cortes.append(v)
    if not cortes:
        return 0.0          # referência constante: nada a comparar

    def contar(valores: Sequence[float]) -> list[int]:
        baldes = [0] * (len(cortes) + 1)
        for v in valores:
            k = 0
            while k < len(cortes) and v > cortes[k]:
                k += 1
            baldes[k] += 1
        return baldes

    br, bc = contar(ref), contar(cur)
    total_r, total_c = len(ref), len(cur)
    # Piso para evitar log(0): balde vazio em uma das janelas viraria
    # infinito, e um balde vazio não é evidência infinita de mudança.
    piso = 1e-4
    soma = 0.0
    for r, c in zip(br, bc):
        pr = max(piso, r / total_r)
        pc = max(piso, c / total_c)
        soma += (pc - pr) * math.log(pc / pr)
    return soma


def drift_de_dado(referencia: dict[str, Sequence[float]],
                  atual: dict[str, Sequence[float]], *,
                  n_faixas: int = 10) -> Achado:
    """Compara a distribuição de cada feature entre referência e agora."""
    comuns = sorted(set(referencia) & set(atual))
    if not comuns:
        return Achado("dado", "sem_amostra", "psi", None,
                      "nenhuma feature em comum entre referência e janela "
                      "atual")

    valores: dict[str, float] = {}
    nao_medidas: list[str] = []
    for nome in comuns:
        v = psi(referencia[nome], atual[nome], n_faixas=n_faixas)
        if v is None:
            nao_medidas.append(nome)
        else:
            valores[nome] = v

    if not valores:
        return Achado(
            "dado", "sem_amostra", "psi", None,
            f"janelas menores que {MIN_JANELA} observações: comparar "
            f"distribuições aqui compararia ruído")

    pior_nome = max(valores, key=lambda k: valores[k])
    pior = valores[pior_nome]
    medio = sum(valores.values()) / len(valores)

    if pior >= PSI_MODERADO:
        nivel, acao = "drift", (
            "revalidar o modelo na janela recente antes de continuar "
            "usando a probabilidade dele; as entradas saíram da faixa em "
            "que ele foi medido")
    elif pior >= PSI_ESTAVEL:
        nivel, acao = "atencao", (
            "acompanhar; se o PSI continuar subindo, revalidar")
    else:
        nivel, acao = "estavel", ""

    detalhe = (f"PSI máximo {pior:.3f} em '{pior_nome}' "
               f"(médio {medio:.3f} sobre {len(valores)} features)")
    if nao_medidas:
        detalhe += f"; {len(nao_medidas)} feature(s) sem amostra suficiente"
    return Achado("dado", nivel, "psi", pior, detalhe, acao)


# --------------------------------------------------------- drift de conceito
def piso_ruido_ece(n: int, n_faixas: int = 10) -> float:
    """Quanto de ECE aparece por puro acaso em um modelo perfeitamente calibrado.

    O ECE tem chão de ruído que depende do tamanho da amostra: com `n`
    observações em `B` faixas, cada faixa tem n/B casos, e a frequência
    observada nela flutua com erro padrão de ordem `sqrt(p(1-p)·B/n)`.

    Isto não é detalhe: a primeira versão deste módulo usava um limiar
    absoluto de 0,02 na diferença de ECE, e duas amostras do MESMO processo
    calibrado, com 600 casos cada, disparavam "atenção". Medindo, o ECE médio
    de um modelo calibrado é 0,072 com n=200, 0,041 com n=600 e 0,023 com
    n=2000 — sempre acima do limiar fixo. O limiar precisa acompanhar o
    tamanho da amostra, senão o detector acusa drift por ter pouca amostra.

    A constante 0,8 vem de `E|Z| ≈ 0,8·σ` para erro aproximadamente normal,
    com `p(1-p)` no pior caso (0,25). O valor sai cerca de 25% acima do ECE
    medido, o que deixa a folga do lado seguro: prefere-se deixar passar um
    drift pequeno a acusar drift em amostra pequena.
    """
    if n <= 0:
        return 1.0
    return 0.8 * math.sqrt(0.25 * n_faixas / n)


def sigma_skill(n: int) -> float:
    """Desvio padrão da DIFERENÇA de skill entre duas amostras iguais.

    Medido empiricamente sobre pares de amostras de um modelo perfeitamente
    calibrado: 0,090 com n=200, 0,038 com n=600 e 0,024 com n=2000 — que
    acompanham `1,1/√n`.

    A constante importa porque a primeira versão usava um corte fixo de
    -0,05 no skill, que a n=600 fica dentro de um desvio padrão: duas
    amostras do mesmo processo disparavam "atenção" sozinhas.
    """
    if n <= 0:
        return 1.0
    return 1.1 / math.sqrt(n)


def drift_de_conceito(previstas_ref: Sequence[float],
                      ocorreu_ref: Sequence[bool | int],
                      previstas_atual: Sequence[float],
                      ocorreu_atual: Sequence[bool | int]) -> Achado:
    """A relação entre features e desfecho mudou?

    Medido pela degradação da calibração: as mesmas probabilidades que
    correspondiam à realidade na validação deixaram de corresponder. É o
    drift que nada nas entradas denuncia — por isso ele precisa ser medido
    contra o desfecho, não contra a distribuição.
    """
    if len(previstas_atual) < MIN_JANELA:
        return Achado(
            "conceito", "sem_amostra", "delta_ece", None,
            f"{len(previstas_atual)} observações na janela atual, abaixo do "
            f"mínimo de {MIN_JANELA}")
    if len(previstas_ref) < MIN_JANELA:
        return Achado(
            "conceito", "sem_amostra", "delta_ece", None,
            f"referência com {len(previstas_ref)} observações, abaixo do "
            f"mínimo de {MIN_JANELA}")

    ref = avaliar_calibracao(previstas_ref, ocorreu_ref)
    cur = avaliar_calibracao(previstas_atual, ocorreu_atual)
    delta_ece = cur.ece - ref.ece
    delta_skill = cur.brier.skill - ref.brier.skill if (
        cur.brier and ref.brier) else 0.0

    # Os limiares acompanham a amostra menor das duas: é ela que domina o
    # ruído. Ambos são expressos em unidades do próprio ruído, não em
    # números fixos — um corte fixo acusa drift por a amostra ser pequena.
    n_menor = min(len(previstas_ref), len(previstas_atual))
    piso = piso_ruido_ece(n_menor)
    sigma = sigma_skill(n_menor)

    detalhe = (f"ECE foi de {ref.ece:.1%} para {cur.ece:.1%} "
               f"({delta_ece:+.1%}, ruído esperado {piso:.1%}) e o skill de "
               f"{ref.brier.skill:.3f} para {cur.brier.skill:.3f} "
               f"({delta_skill:+.3f}, ruído {sigma:.3f})")

    # Perder poder discriminante é mais grave que perder calibração: a
    # calibração pode ser corrigida por um mapa novo, o poder preditivo não.
    if cur.brier is not None and not cur.brier.melhor_que_taxa_base:
        return Achado(
            "conceito", "drift", "skill", cur.brier.skill,
            detalhe + ". O modelo deixou de ser melhor que dizer a taxa-base",
            "parar de usar a probabilidade deste modelo e treinar outro; "
            "recalibrar não resolve perda de poder preditivo")
    # 3 desvios para drift, 2 para atenção. "Atenção" a 2σ dispara por acaso
    # em torno de 2% das checagens, por construção — é um sinal para olhar,
    # nunca uma decisão. "Drift" a 3σ é o que muda conduta.
    if delta_ece > 2 * piso or delta_skill < -3 * sigma:
        return Achado(
            "conceito", "drift", "delta_ece", delta_ece, detalhe,
            "recalibrar na janela recente e revalidar em uma terceira "
            "janela antes de voltar a usar")
    if delta_ece > piso or delta_skill < -2 * sigma:
        return Achado("conceito", "atencao", "delta_ece", delta_ece, detalhe,
                      "acompanhar a calibração a cada janela")
    return Achado("conceito", "estavel", "delta_ece", delta_ece, detalhe)


# ------------------------------------------------------ drift de performance
def drift_de_performance(baseline_r: Sequence[float],
                         recente_r: Sequence[float], *,
                         confianca: float = 0.95) -> Achado:
    """O resultado realizado piorou além do que o azar explica?

    Julgado pelo intervalo de confiança da DIFERENÇA entre as duas amostras,
    não por limiar na média recente. A distribuição de resultados em R tem
    cauda: dez operações ruins seguidas acontecem em estratégia boa, e um
    limiar simples desligaria a estratégia a cada série azarada e religaria a
    cada série sortuda — ruído virando decisão.
    """
    n_rec = len(recente_r)
    n_base = len(baseline_r)
    if n_rec < MIN_TRADES:
        return Achado(
            "performance", "sem_amostra", "delta_r", None,
            f"{n_rec} operações recentes, abaixo do mínimo de {MIN_TRADES}: "
            f"o intervalo de confiança da diferença cobriria tudo, então "
            f"nenhuma conclusão é possível")
    if n_base < MIN_TRADES:
        return Achado(
            "performance", "sem_amostra", "delta_r", None,
            f"baseline com {n_base} operações, abaixo do mínimo de "
            f"{MIN_TRADES}")

    m_base = sum(baseline_r) / n_base
    m_rec = sum(recente_r) / n_rec
    delta = m_rec - m_base

    var_base = sum((x - m_base) ** 2 for x in baseline_r) / (n_base - 1)
    var_rec = sum((x - m_rec) ** 2 for x in recente_r) / (n_rec - 1)
    # Erro padrão da diferença de médias, sem supor variâncias iguais
    # (Welch): a variância dos resultados costuma mudar junto com a média.
    se = math.sqrt(var_base / n_base + var_rec / n_rec)
    if se <= 0:
        return Achado("performance", "estavel", "delta_r", delta,
                      "variância zero nas duas amostras")

    z = delta / se
    ic_rec = ic_media(recente_r, confianca=confianca)
    detalhe = (f"expectativa recente {m_rec:+.3f}R contra baseline "
               f"{m_base:+.3f}R (diferença {delta:+.3f}R, z={z:.2f}); IC da "
               f"amostra recente [{ic_rec.inferior:+.3f}, "
               f"{ic_rec.superior:+.3f}]R em {n_rec} operações")

    # z abaixo de -1,96 é queda significativa a 95%.
    if z <= -1.96:
        return Achado(
            "performance", "drift", "z", z, detalhe,
            "suspender a estratégia e revalidar: a queda não é explicada "
            "por azar na amostra")
    if z <= -1.0:
        return Achado(
            "performance", "atencao", "z", z,
            detalhe + ". A queda ainda cabe no azar, mas está na direção "
                      "errada",
            "reduzir exposição e acompanhar; não é conclusiva ainda")
    return Achado("performance", "estavel", "z", z, detalhe)


# ---------------------------------------------------------- drift de regime
def drift_de_regime(referencia: Sequence[str],
                    atual: Sequence[str]) -> Achado:
    """A composição de regimes mudou?

    Não é defeito do modelo: é o modelo sendo aplicado fora do contexto em
    que foi medido. A ação correta é diferente — não retreinar, e sim
    reconhecer que a estatística validada em tendência não vale em
    lateralização.
    """
    if len(atual) < MIN_JANELA or len(referencia) < MIN_JANELA:
        return Achado(
            "regime", "sem_amostra", "distancia", None,
            f"janelas abaixo de {MIN_JANELA} leituras de regime")

    def dist(xs: Sequence[str]) -> dict[str, float]:
        n = len(xs)
        out: dict[str, float] = {}
        for x in xs:
            out[x] = out.get(x, 0.0) + 1.0 / n
        return out

    dr, da = dist(referencia), dist(atual)
    chaves = sorted(set(dr) | set(da))
    # Distância de variação total: metade da soma das diferenças absolutas.
    # Vai de 0 (distribuições iguais) a 1 (sem sobreposição), e é direta de
    # ler: "X% da massa mudou de regime".
    tvd = 0.5 * sum(abs(da.get(k, 0.0) - dr.get(k, 0.0)) for k in chaves)

    dominante_ref = max(dr, key=lambda k: dr[k])
    dominante_atual = max(da, key=lambda k: da[k])
    detalhe = (f"{tvd:.0%} da massa mudou de regime; dominante era "
               f"'{dominante_ref}' ({dr[dominante_ref]:.0%}) e agora é "
               f"'{dominante_atual}' ({da[dominante_atual]:.0%})")

    if dominante_atual != dominante_ref and tvd >= 0.30:
        return Achado(
            "regime", "drift", "tvd", tvd, detalhe,
            f"a estatística validada foi medida predominantemente em "
            f"'{dominante_ref}'; ela não se transfere para "
            f"'{dominante_atual}'. Medir separadamente por regime antes de "
            f"continuar")
    if tvd >= 0.30:
        return Achado("regime", "atencao", "tvd", tvd, detalhe,
                      "mudança de composição sem troca de dominante; "
                      "acompanhar")
    return Achado("regime", "estavel", "tvd", tvd, detalhe)


# ------------------------------------------------------------------ conjunto
def avaliar_drift(*,
                  features_ref: dict[str, Sequence[float]] | None = None,
                  features_atual: dict[str, Sequence[float]] | None = None,
                  previstas_ref: Sequence[float] | None = None,
                  ocorreu_ref: Sequence[bool | int] | None = None,
                  previstas_atual: Sequence[float] | None = None,
                  ocorreu_atual: Sequence[bool | int] | None = None,
                  baseline_r: Sequence[float] | None = None,
                  recente_r: Sequence[float] | None = None,
                  regimes_ref: Sequence[str] | None = None,
                  regimes_atual: Sequence[str] | None = None,
                  ) -> RelatorioDrift:
    """Roda as quatro checagens que houver dados para rodar.

    Cada bloco é opcional. O que não tiver dados NÃO entra como "estável":
    entra como `sem_amostra`, porque não ter medido é diferente de ter
    medido e não achado nada.
    """
    rel = RelatorioDrift()

    if features_ref is not None and features_atual is not None:
        rel.achados.append(drift_de_dado(features_ref, features_atual))
    if (previstas_ref is not None and ocorreu_ref is not None
            and previstas_atual is not None and ocorreu_atual is not None):
        rel.achados.append(drift_de_conceito(
            previstas_ref, ocorreu_ref, previstas_atual, ocorreu_atual))
    if baseline_r is not None and recente_r is not None:
        rel.achados.append(drift_de_performance(baseline_r, recente_r))
    if regimes_ref is not None and regimes_atual is not None:
        rel.achados.append(drift_de_regime(regimes_ref, regimes_atual))

    if not rel.achados:
        rel.avisos.append(
            "nenhum dado fornecido: nada foi medido. Isto não é o mesmo que "
            "'nenhum drift'")
        return rel

    faltando = [a.tipo for a in rel.sem_amostra]
    if faltando:
        rel.avisos.append(
            f"sem amostra suficiente para julgar: {', '.join(faltando)}. "
            f"Ausência de medição não é ausência de drift")
    for a in rel.drift:
        rel.avisos.append(f"DRIFT DE {a.tipo.upper()}: {a.detalhe}. {a.acao}")
    return rel


__all__ = [
    "Achado", "MIN_JANELA", "MIN_TRADES", "NIVEIS", "PSI_ESTAVEL",
    "PSI_MODERADO", "RelatorioDrift", "avaliar_drift", "drift_de_conceito",
    "drift_de_dado", "drift_de_performance", "drift_de_regime",
    "piso_ruido_ece", "psi", "sigma_skill",
]
