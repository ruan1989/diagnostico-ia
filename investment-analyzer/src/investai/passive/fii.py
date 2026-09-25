"""Análise de FIIs para renda passiva.

O que este módulo tenta evitar
------------------------------
O erro mais comum em FII é comprar pelo dividend yield mais alto da lista.
DY alto quase sempre tem uma explicação: rendimento não recorrente (venda de
ativo), contrato vencendo, inquilino único em dificuldade, vacância subindo ou
fundo de papel carregado de CRI de risco. Por isso o score aqui:

* trata DY como curva, não como escala: acima de ~14% a.a. o fator PIORA,
  porque a probabilidade de ser insustentável cresce mais rápido que o ganho;
* exige liquidez mínima — não adianta yield bom se você não consegue vender;
* penaliza vacância e concentração em poucos imóveis/inquilinos;
* trata P/VP muito baixo como bandeira amarela, não só como desconto.

O resultado é um ranking com justificativa, não uma recomendação. Decisão de
aporte depende de objetivo, prazo e situação fiscal de cada pessoa.
"""
from __future__ import annotations

from ..models import FactorScore, FiiOpportunity

PESOS_FII: dict[str, float] = {
    "dy_sustentavel": 0.26,
    "preco_vs_patrimonio": 0.20,
    "ocupacao": 0.16,
    "liquidez": 0.14,
    "diversificacao": 0.12,
    "porte": 0.07,
    "segmento": 0.05,
}

# Faixas de DY anual consideradas saudáveis por tipo de fundo. Fundos de
# papel (CRI) operam com yield naturalmente mais alto que os de tijolo.
DY_IDEAL = {
    "papel": (11.0, 14.0),
    "tijolo": (8.0, 11.5),
    "hibrido": (9.0, 12.5),
    "fof": (8.5, 12.0),
}

CLASSE_SEGMENTO = {
    "logistica": "tijolo", "lajes corporativas": "tijolo", "shoppings": "tijolo",
    "renda urbana": "tijolo", "hoteis": "tijolo", "educacional": "tijolo",
    "hospitalar": "tijolo", "agencias bancarias": "tijolo",
    "recebiveis": "papel", "cri": "papel", "papel": "papel",
    "hibrido": "hibrido", "misto": "hibrido",
    "fof": "fof", "fundo de fundos": "fof",
}

# Qualidade estrutural média do segmento: liquidez, previsibilidade de
# contrato e sensibilidade a ciclo econômico.
NOTA_SEGMENTO = {
    "logistica": 0.85, "recebiveis": 0.70, "renda urbana": 0.70,
    "shoppings": 0.60, "lajes corporativas": 0.45, "hibrido": 0.55,
    "fof": 0.50, "educacional": 0.40, "hospitalar": 0.50,
    "hoteis": 0.25, "agencias bancarias": 0.20,
}

LIQUIDEZ_MINIMA = 300_000.0      # R$/dia — abaixo disso a saída é difícil


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def classe_do_segmento(segmento: str) -> str:
    return CLASSE_SEGMENTO.get(segmento.strip().lower(), "hibrido")


def _fator_dy(dy: float, segmento: str) -> FactorScore:
    """DY como curva de sino: existe faixa boa, e acima dela o risco cresce."""
    classe = classe_do_segmento(segmento)
    lo, hi = DY_IDEAL[classe]
    if dy <= 0:
        valor, detalhe = -1.0, "sem histórico de distribuição"
    elif dy < lo:
        # Abaixo da faixa: renda fraca, mas não é defeito estrutural.
        valor = _clamp((dy - lo * 0.55) / (lo - lo * 0.55) * 0.8 - 0.3)
        detalhe = f"DY {dy:.2f}% abaixo da faixa saudável ({lo:.1f}–{hi:.1f}%)"
    elif dy <= hi:
        valor = 1.0 - abs(dy - (lo + hi) / 2) / ((hi - lo) / 2) * 0.25
        detalhe = f"DY {dy:.2f}% dentro da faixa saudável ({lo:.1f}–{hi:.1f}%)"
    else:
        # Acima da faixa: cada ponto extra reduz o fator.
        excesso = dy - hi
        valor = _clamp(0.75 - excesso / 3.5)
        detalhe = (f"DY {dy:.2f}% acima de {hi:.1f}% — verificar se há receita "
                   f"não recorrente ou risco de corte")
    return FactorScore("dy_sustentavel", PESOS_FII["dy_sustentavel"], valor, detalhe)


def _fator_p_vp(p_vp: float) -> FactorScore:
    """P/VP: desconto é bom até certo ponto; desconto extremo é sintoma."""
    if p_vp <= 0:
        return FactorScore("preco_vs_patrimonio", PESOS_FII["preco_vs_patrimonio"],
                           -1.0, "P/VP indisponível")
    if p_vp < 0.65:
        valor = _clamp(0.3 - (0.65 - p_vp) * 2.5)
        detalhe = f"P/VP {p_vp:.2f} — desconto muito alto, investigar a causa"
    elif p_vp <= 0.95:
        valor = 1.0 - abs(p_vp - 0.85) / 0.30 * 0.2
        detalhe = f"P/VP {p_vp:.2f} — negociado com desconto sobre o patrimônio"
    elif p_vp <= 1.05:
        valor = 0.55
        detalhe = f"P/VP {p_vp:.2f} — próximo do valor patrimonial"
    else:
        valor = _clamp(0.45 - (p_vp - 1.05) * 3.0)
        detalhe = f"P/VP {p_vp:.2f} — ágio sobre o patrimônio"
    return FactorScore("preco_vs_patrimonio", PESOS_FII["preco_vs_patrimonio"],
                       _clamp(valor), detalhe)


def _fator_ocupacao(vacancia: float | None, segmento: str) -> FactorScore:
    """Vacância é o indicador mais direto de risco de corte de rendimento."""
    classe = classe_do_segmento(segmento)
    if vacancia is None:
        # Fundo de papel não tem vacância física — não é omissão de dado.
        if classe == "papel":
            return FactorScore("ocupacao", PESOS_FII["ocupacao"], 0.45,
                               "fundo de papel: sem vacância física aplicável")
        return FactorScore("ocupacao", PESOS_FII["ocupacao"], -0.2,
                           "vacância não informada")
    if vacancia <= 2.0:
        valor, detalhe = 1.0, f"vacância {vacancia:.1f}% — praticamente cheia"
    elif vacancia <= 8.0:
        valor = 0.8 - (vacancia - 2.0) / 6.0 * 0.6
        detalhe = f"vacância {vacancia:.1f}% — normal para o setor"
    elif vacancia <= 18.0:
        valor = 0.2 - (vacancia - 8.0) / 10.0 * 0.9
        detalhe = f"vacância {vacancia:.1f}% — pressiona a distribuição"
    else:
        valor = _clamp(-0.7 - (vacancia - 18.0) / 20.0)
        detalhe = f"vacância {vacancia:.1f}% — risco alto de corte de rendimento"
    return FactorScore("ocupacao", PESOS_FII["ocupacao"], _clamp(valor), detalhe)


def _fator_liquidez(liquidez: float) -> FactorScore:
    """Liquidez define se você consegue sair sem destruir o preço."""
    if liquidez <= 0:
        valor, detalhe = -1.0, "liquidez não informada"
    elif liquidez < LIQUIDEZ_MINIMA:
        valor = _clamp(-1.0 + liquidez / LIQUIDEZ_MINIMA * 0.8)
        detalhe = (f"liquidez R$ {liquidez:,.0f}/dia abaixo do mínimo "
                   f"R$ {LIQUIDEZ_MINIMA:,.0f}")
    else:
        # Saturação logarítmica: de 300k para 1M importa muito; de 5M para
        # 10M, quase nada.
        import math
        valor = _clamp(math.log10(liquidez / LIQUIDEZ_MINIMA) / 1.3)
        detalhe = f"liquidez R$ {liquidez:,.0f}/dia"
    return FactorScore("liquidez", PESOS_FII["liquidez"], valor,
                       detalhe.replace(",", "."))


def _fator_diversificacao(num_imoveis: int | None, segmento: str) -> FactorScore:
    """Inquilino ou imóvel único concentra todo o risco em um contrato."""
    classe = classe_do_segmento(segmento)
    if classe in ("papel", "fof"):
        return FactorScore("diversificacao", PESOS_FII["diversificacao"], 0.5,
                           "carteira de papéis/cotas — diversificação por emissor")
    if num_imoveis is None:
        return FactorScore("diversificacao", PESOS_FII["diversificacao"], -0.1,
                           "número de imóveis não informado")
    if num_imoveis <= 1:
        valor, detalhe = -0.8, "monoativo — risco concentrado em um único imóvel"
    elif num_imoveis <= 3:
        valor, detalhe = -0.1, f"{num_imoveis} imóveis — concentração relevante"
    elif num_imoveis <= 10:
        valor, detalhe = 0.55, f"{num_imoveis} imóveis — diversificação razoável"
    else:
        valor, detalhe = 1.0, f"{num_imoveis} imóveis — bem diversificado"
    return FactorScore("diversificacao", PESOS_FII["diversificacao"], valor, detalhe)


def _fator_porte(patrimonio: float | None) -> FactorScore:
    """Fundos muito pequenos têm custo fixo proporcionalmente alto e giro baixo."""
    if patrimonio is None or patrimonio <= 0:
        return FactorScore("porte", PESOS_FII["porte"], -0.2,
                           "patrimônio líquido não informado")
    bi = patrimonio / 1e9
    if bi < 0.15:
        valor, detalhe = -0.6, f"PL R$ {bi:.2f} bi — fundo pequeno"
    elif bi < 0.5:
        valor, detalhe = 0.2, f"PL R$ {bi:.2f} bi — porte médio"
    elif bi < 3.0:
        valor, detalhe = 1.0, f"PL R$ {bi:.2f} bi — porte confortável"
    else:
        valor, detalhe = 0.8, f"PL R$ {bi:.2f} bi — fundo grande"
    return FactorScore("porte", PESOS_FII["porte"], valor, detalhe)


def _fator_segmento(segmento: str) -> FactorScore:
    nota = NOTA_SEGMENTO.get(segmento.strip().lower(), 0.35)
    valor = nota * 2.0 - 1.0 if nota < 0.5 else nota
    return FactorScore("segmento", PESOS_FII["segmento"], _clamp(valor),
                       f"segmento '{segmento}'")


def _alertas(fii: FiiOpportunity) -> list[str]:
    """Bandeiras que merecem leitura do relatório gerencial antes de aportar."""
    out: list[str] = []
    classe = classe_do_segmento(fii.segmento)
    _, dy_hi = DY_IDEAL[classe]
    if fii.dy_12m > dy_hi + 2.0:
        out.append(f"DY de {fii.dy_12m:.1f}% muito acima do normal para "
                   f"{classe} — confira se houve rendimento extraordinário")
    if fii.p_vp < 0.7:
        out.append(f"P/VP de {fii.p_vp:.2f} sugere que o mercado precifica "
                   f"problema no patrimônio ou na inadimplência")
    if fii.p_vp > 1.15:
        out.append(f"P/VP de {fii.p_vp:.2f}: pagando ágio sobre o patrimônio")
    if fii.vacancia_pct is not None and fii.vacancia_pct > 12.0:
        out.append(f"vacância de {fii.vacancia_pct:.1f}% pressiona a distribuição")
    if fii.liquidez_diaria < LIQUIDEZ_MINIMA:
        out.append("liquidez baixa: saída pode exigir desconto no preço")
    if fii.num_imoveis is not None and fii.num_imoveis <= 1 and classe == "tijolo":
        out.append("fundo monoativo: vencimento ou saída do inquilino afeta "
                   "100% da receita")
    return out


# ---------------------------------------------------------------------------
# Camada de teto (veto parcial).
#
# Uma soma ponderada dilui defeito grave: um fundo com 25% de vacância mas
# bons números nos outros fatores ainda somaria score alto, o que é
# perigosamente enganoso. Cada condição abaixo impõe um TETO ao score final,
# independentemente do resto. É o equivalente a "esse defeito sozinho já
# limita o quanto o fundo pode ser recomendado".
# ---------------------------------------------------------------------------
def tetos_aplicaveis(fii: FiiOpportunity) -> list[tuple[float, str]]:
    classe = classe_do_segmento(fii.segmento)
    _, dy_hi = DY_IDEAL[classe]
    tetos: list[tuple[float, str]] = []

    if fii.liquidez_diaria < LIQUIDEZ_MINIMA * 0.34:
        tetos.append((35.0, "liquidez inferior a R$ 100 mil/dia: saída travada"))
    elif fii.liquidez_diaria < LIQUIDEZ_MINIMA:
        tetos.append((52.0, "liquidez abaixo de R$ 300 mil/dia"))

    if fii.vacancia_pct is not None:
        if fii.vacancia_pct > 25.0:
            tetos.append((32.0, f"vacância de {fii.vacancia_pct:.1f}%"))
        elif fii.vacancia_pct > 18.0:
            tetos.append((45.0, f"vacância de {fii.vacancia_pct:.1f}%"))
        elif fii.vacancia_pct > 12.0:
            tetos.append((58.0, f"vacância de {fii.vacancia_pct:.1f}%"))

    if fii.dy_12m > dy_hi + 6.0:
        tetos.append((45.0, f"DY de {fii.dy_12m:.1f}% provavelmente não recorrente"))
    elif fii.dy_12m > dy_hi + 2.0:
        tetos.append((60.0, f"DY de {fii.dy_12m:.1f}% acima do sustentável"))
    elif fii.dy_12m <= 0:
        tetos.append((30.0, "fundo sem distribuição nos últimos 12 meses"))

    if fii.p_vp <= 0:
        tetos.append((40.0, "P/VP indisponível"))
    elif fii.p_vp < 0.60:
        tetos.append((48.0, f"P/VP de {fii.p_vp:.2f}: mercado precifica problema"))
    elif fii.p_vp > 1.25:
        tetos.append((55.0, f"P/VP de {fii.p_vp:.2f}: ágio elevado"))

    if classe == "tijolo" and fii.num_imoveis is not None and fii.num_imoveis <= 1:
        tetos.append((58.0, "fundo monoativo"))

    if fii.patrimonio_liquido is not None and 0 < fii.patrimonio_liquido < 100e6:
        tetos.append((50.0, "patrimônio líquido abaixo de R$ 100 milhões"))

    return tetos


def classificar_fii(score: float) -> str:
    if score >= 75:
        return "forte candidato"
    if score >= 62:
        return "bom, com ressalvas"
    if score >= 50:
        return "neutro — depende do objetivo"
    if score >= 38:
        return "fraco"
    return "evitar"


def avaliar_fii(fii: FiiOpportunity) -> FiiOpportunity:
    """Pontua o fundo e preenche score, fatores, alertas e renda estimada."""
    fatores = [
        _fator_dy(fii.dy_12m, fii.segmento),
        _fator_p_vp(fii.p_vp),
        _fator_ocupacao(fii.vacancia_pct, fii.segmento),
        _fator_liquidez(fii.liquidez_diaria),
        _fator_diversificacao(fii.num_imoveis, fii.segmento),
        _fator_porte(fii.patrimonio_liquido),
        _fator_segmento(fii.segmento),
    ]
    bruto = sum(f.contribuicao for f in fatores)
    score = _clamp(bruto) * 50.0 + 50.0

    # Aplica o teto mais restritivo entre os defeitos encontrados.
    tetos = tetos_aplicaveis(fii)
    limitadores: list[str] = []
    for teto, razao in tetos:
        if score > teto:
            limitadores.append(f"score limitado a {teto:.0f} por: {razao}")
        score = min(score, teto)

    fii.score = score
    fii.fatores = fatores
    fii.alertas = _alertas(fii) + limitadores
    fii.classificacao = classificar_fii(fii.score)
    # Renda mensal bruta estimada para cada R$ 1.000 aplicados, assumindo que
    # a distribuição dos últimos 12 meses se repita — premissa que o alerta
    # de DY insustentável existe justamente para questionar.
    fii.renda_mensal_por_1k = 1000.0 * (fii.dy_12m / 100.0) / 12.0
    return fii


def ranquear(fundos: list[FiiOpportunity]) -> list[FiiOpportunity]:
    avaliados = [avaliar_fii(f) for f in fundos]
    avaliados.sort(key=lambda f: f.score, reverse=True)
    return avaliados


def carteira_sugerida(fundos: list[FiiOpportunity], capital: float,
                      max_por_fundo_pct: float = 20.0,
                      min_score: float = 62.0,
                      max_fundos: int = 8) -> dict:
    """Monta uma distribuição proporcional ao score, com teto por fundo.

    O teto por fundo e o limite por segmento existem para que a carteira não
    fique concentrada no fundo mais bem pontuado — pontuação alta não elimina
    risco específico.
    """
    if capital <= 0:
        raise ValueError("capital deve ser > 0")
    elegiveis = [f for f in ranquear(fundos) if f.score >= min_score]
    if not elegiveis:
        return {"capital": capital, "itens": [], "renda_mensal_estimada": 0.0,
                "dy_medio_ponderado": 0.0,
                "aviso": f"nenhum fundo com score >= {min_score:.0f} na lista "
                         f"analisada — não forçar aporte"}

    # No máximo 2 fundos por segmento, para diluir risco setorial.
    por_segmento: dict[str, int] = {}
    selecionados: list[FiiOpportunity] = []
    for f in elegiveis:
        seg = f.segmento.strip().lower()
        if por_segmento.get(seg, 0) >= 2:
            continue
        por_segmento[seg] = por_segmento.get(seg, 0) + 1
        selecionados.append(f)
        if len(selecionados) >= max_fundos:
            break

    total_score = sum(f.score for f in selecionados)
    itens = []
    for f in selecionados:
        peso = min(f.score / total_score, max_por_fundo_pct / 100.0)
        itens.append({"fii": f, "peso": peso})
    # Renormaliza depois de aplicar o teto, para somar 100%.
    soma = sum(i["peso"] for i in itens)
    for i in itens:
        i["peso"] = i["peso"] / soma

    detalhados = []
    renda = 0.0
    dy_pond = 0.0
    for i in itens:
        f: FiiOpportunity = i["fii"]
        valor = capital * i["peso"]
        cotas = int(valor // f.preco) if f.preco > 0 else 0
        investido = cotas * f.preco
        renda_item = investido * (f.dy_12m / 100.0) / 12.0
        renda += renda_item
        dy_pond += f.dy_12m * i["peso"]
        detalhados.append({
            "ticker": f.ticker, "segmento": f.segmento, "score": round(f.score, 1),
            "classificacao": f.classificacao,
            "peso_pct": round(i["peso"] * 100, 2),
            "preco": f.preco, "cotas": cotas,
            "valor_investido": round(investido, 2),
            "renda_mensal_estimada": round(renda_item, 2),
            "dy_12m": f.dy_12m, "p_vp": f.p_vp, "alertas": f.alertas,
        })
    investido_total = sum(d["valor_investido"] for d in detalhados)
    return {
        "capital": capital,
        "investido": round(investido_total, 2),
        "sobra_caixa": round(capital - investido_total, 2),
        "itens": detalhados,
        "renda_mensal_estimada": round(renda, 2),
        "renda_anual_estimada": round(renda * 12, 2),
        "dy_medio_ponderado": round(dy_pond, 2),
        "aviso": "Estimativa baseada na distribuição dos últimos 12 meses. "
                 "Rendimento de FII não é fixo nem garantido e pode ser "
                 "reduzido ou suspenso.",
    }
