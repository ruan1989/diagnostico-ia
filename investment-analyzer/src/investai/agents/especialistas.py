"""Os agentes especialistas.

Cada um cobre um ângulo e devolve `N/A` quando o ângulo não existe para a
classe de ativo. Nenhum deles pode, sozinho, autorizar uma operação — é o
consolidador que junta, e o Risk Engine que veta.
"""
from __future__ import annotations

from typing import Any

from ..data.symbols import AssetClass
from ..models import Regime
from .base import AgenteBase, ContextoAnalise, ParecerAgente

TODAS = tuple(c for c in AssetClass if c is not AssetClass.DESCONHECIDO)


# ============================================================ 1) TÉCNICO
class AgenteTecnico(AgenteBase):
    """Estrutura de mercado, tendência, momentum, volume e volatilidade.

    Nunca usa um indicador isolado: combina alinhamento de médias, força de
    tendência (ADX/DI), momentum (MACD), posição no canal e volume. A leitura
    de RSI depende do regime, porque o mesmo valor significa coisas opostas em
    tendência e em faixa.
    """

    nome = "tecnico"
    peso = 0.20
    classes_suportadas = (AssetClass.CRIPTO, AssetClass.ACAO, AssetClass.ETF,
                          AssetClass.FII, AssetClass.INDICE,
                          AssetClass.CAMBIO, AssetClass.COMMODITY)

    def analisar(self, ctx: ContextoAnalise, direcao: int) -> ParecerAgente:
        if not ctx.features:
            return self._sem_dados(
                ["features_tecnicas"],
                "sem série de preços suficiente para calcular indicadores")

        principal = ctx.features.get(ctx.timeframe)
        if principal is None:
            principal = next(iter(ctx.features.values()))

        f = principal
        d = float(direcao)
        ev: list[str] = []
        contra: list[str] = []

        denom = getattr(f, "close", 0.0) or 1.0
        curto = (f.ema_fast - f.ema_slow) / denom * 100.0
        longo = (f.ema_slow - f.ema_trend) / denom * 100.0
        estrutura = d * (curto / 0.5 * 0.6 + longo / 1.5 * 0.4)
        ev.append(f"EMA9-21 {curto:+.2f}% e EMA21-200 {longo:+.2f}% do preço")

        intensidade = max(0.0, min(1.0, (f.adx - 15.0) / 25.0))
        direcional = d * (f.di_plus - f.di_minus) / 15.0
        forca = direcional * intensidade
        ev.append(f"ADX {f.adx:.1f} com +DI {f.di_plus:.1f} / -DI {f.di_minus:.1f}")
        if f.adx < 15:
            contra.append(f"ADX de {f.adx:.1f} indica ausência de tendência "
                          f"definida")

        momentum = d * (f.macd_hist / denom * 100.0) / 0.35
        ev.append(f"histograma MACD {f.macd_hist / denom * 100:+.3f}% do preço")

        canal = f.donchian_high - f.donchian_low
        if canal > 0:
            pos = ((f.close - f.donchian_low) / canal if d > 0
                   else (f.donchian_high - f.close) / canal)
            posicao = (pos - 0.5) * 2.0
            ev.append(f"preço a {pos * 100:.0f}% do canal na direção avaliada")
        else:
            posicao = 0.0

        volume = max(-1.0, min(1.0, (f.volume_ratio - 1.0) / 0.8))
        if f.volume_ratio < 0.7:
            contra.append(f"volume {f.volume_ratio:.2f}x a média: movimento "
                          f"sem confirmação")

        # RSI conforme o regime.
        em_tendencia = f.regime in (Regime.TENDENCIA_ALTA,
                                    Regime.TENDENCIA_BAIXA)
        if em_tendencia:
            alvo = 60.0 if d > 0 else 40.0
            rsi_v = max(-1.0, min(1.0, 1.0 - abs(f.rsi - alvo) / 25.0))
            if (d > 0 and f.rsi > 78) or (d < 0 and f.rsi < 22):
                rsi_v -= 0.6
                contra.append(f"RSI {f.rsi:.0f} em exaustão mesmo em tendência")
        else:
            rsi_v = max(-1.0, min(1.0, d * (50.0 - f.rsi) / 20.0))
        ev.append(f"RSI {f.rsi:.0f} lido em regime {f.regime.value}")

        # Concordância entre timeframes eleva a confiança.
        outros = [g for tf, g in ctx.features.items() if tf != ctx.timeframe]
        concordam = sum(
            1 for g in outros
            if (d > 0 and g.ema_fast > g.ema_slow)
            or (d < 0 and g.ema_fast < g.ema_slow))
        acordo = (concordam / len(outros)) if outros else 0.5
        if outros:
            ev.append(f"{concordam}/{len(outros)} timeframes concordam")
            if acordo < 0.5:
                contra.append("timeframes maiores não confirmam a direção")

        valor = (estrutura * 0.28 + forca * 0.22 + momentum * 0.18
                 + posicao * 0.16 + rsi_v * 0.10 + volume * 0.06)
        confianca = 0.45 + 0.35 * acordo + 0.20 * intensidade

        return self._parecer(valor, confianca, evidencias=ev,
                             contraindicacoes=contra,
                             metricas={"adx": round(f.adx, 2),
                                       "rsi": round(f.rsi, 2),
                                       "atr_pct": round(f.atr_pct, 3),
                                       "acordo_timeframes": round(acordo, 2)})


# ======================================================= 2) DERIVATIVOS
class AgenteDerivativos(AgenteBase):
    """Funding, open interest, basis e risco de liquidação em cascata.

    Só se aplica a cripto com mercado de derivativos. Para ação, FII ou renda
    fixa devolve `N/A` — não tenta traduzir "funding" para algo que não existe.
    """

    nome = "derivativos"
    peso = 0.12
    classes_suportadas = (AssetClass.CRIPTO,)

    def analisar(self, ctx: ContextoAnalise, direcao: int) -> ParecerAgente:
        if ctx.asset_class is not AssetClass.CRIPTO:
            return self._na(f"{ctx.asset_class.value} não tem mercado de "
                            f"futuros perpétuos com funding")
        if ctx.snapshot is None and ctx.derivativos is None:
            return self._sem_dados(
                ["funding", "open_interest"],
                "sem dados de derivativos para este símbolo")

        d = float(direcao)
        ev: list[str] = []
        contra: list[str] = []
        faltando: list[str] = []

        snap = ctx.snapshot
        deriv = ctx.derivativos or {}

        funding = (getattr(snap, "funding_rate", None)
                   if snap is not None else None)
        if funding is None:
            funding = deriv.get("funding_rate")

        # Funding é lido de forma CONTRÁRIA: taxa muito positiva significa
        # multidão comprada pagando para continuar comprada. Entrar long ali
        # é entrar no fim da fila de liquidação.
        if funding is None:
            faltando.append("funding_rate")
            f_valor = 0.0
        else:
            f_valor = max(-1.0, min(1.0, -d * funding / 0.0012))
            ev.append(f"funding {funding * 100:+.4f}% por período "
                      f"(leitura contrária)")
            if abs(funding) > 0.0024:
                contra.append(
                    f"funding de {funding * 100:+.4f}% é extremo: "
                    f"posicionamento aglomerado e risco de liquidação em "
                    f"cascata")

        oi = (getattr(snap, "open_interest", None) if snap is not None
              else deriv.get("open_interest"))
        oi_var = deriv.get("open_interest_variacao_pct")
        if oi_var is None:
            faltando.append("open_interest_variacao_pct")
            oi_valor = 0.0
        else:
            # OI subindo com preço na direção da tese confirma fluxo novo.
            oi_valor = max(-1.0, min(1.0, d * oi_var / 12.0))
            ev.append(f"open interest {oi_var:+.1f}% na janela")
            if oi_var > 25.0:
                contra.append(
                    f"open interest subiu {oi_var:.0f}% muito rápido: "
                    f"alavancagem nova concentrada é combustível de cascata")

        basis = deriv.get("basis_pct")
        if basis is None:
            faltando.append("basis_pct")
            basis_valor = 0.0
        else:
            basis_valor = max(-1.0, min(1.0, -d * basis / 1.5))
            ev.append(f"basis {basis:+.2f}% entre perpétuo e spot")

        liq = deriv.get("liquidacoes_24h_usd")
        if liq is None:
            faltando.append("liquidacoes_24h_usd")

        valor = f_valor * 0.45 + oi_valor * 0.35 + basis_valor * 0.20
        # A confiança cai com a quantidade de campos ausentes.
        cobertura = 1.0 - len(faltando) / 4.0
        confianca = max(0.15, 0.85 * cobertura)

        parecer = self._parecer(
            valor, confianca, evidencias=ev, contraindicacoes=contra,
            metricas={"funding_rate": funding, "open_interest": oi,
                      "basis_pct": basis})
        parecer.dados_faltando = faltando
        return parecer


# ====================================================== 3) FUNDAMENTOS
class AgenteFundamentos(AgenteBase):
    """Fundamentos específicos por classe.

    Para cripto devolve `N/A`: métricas de balanço não se aplicam a um token
    sem demonstrativo auditado, e inventar um "score fundamentalista" a partir
    de capitalização de mercado seria fabricar informação.
    """

    nome = "fundamentos"
    peso = 0.18
    classes_suportadas = (AssetClass.ACAO, AssetClass.FII, AssetClass.ETF,
                          AssetClass.RENDA_FIXA)

    def analisar(self, ctx: ContextoAnalise, direcao: int) -> ParecerAgente:
        if ctx.asset_class is AssetClass.CRIPTO:
            return self._na(
                "criptomoedas não têm demonstrativo financeiro auditado; "
                "derivar 'fundamento' de capitalização seria inventar métrica")
        if ctx.fundamentos is None:
            return self._sem_dados(
                ["fundamentos"],
                f"FONTE NÃO CONFIGURADA: sem dados fundamentalistas para "
                f"{ctx.symbol}")

        fund = ctx.fundamentos
        d = float(direcao)
        ev: list[str] = []
        contra: list[str] = []
        faltando: list[str] = []
        componentes: list[tuple[float, float]] = []   # (valor, peso)

        if ctx.asset_class is AssetClass.FII:
            return self._fii(ctx, d, fund)

        # ------------------------------------------------ ações e ETFs
        for chave, peso, bom_alto, faixa, rotulo in (
            ("roe", 0.18, True, 15.0, "ROE"),
            ("roic", 0.18, True, 12.0, "ROIC"),
            ("margem_liquida", 0.12, True, 10.0, "margem líquida"),
            ("crescimento_receita", 0.16, True, 8.0, "crescimento de receita"),
        ):
            v = fund.get(chave)
            if v is None:
                faltando.append(chave)
                continue
            norm = max(-1.0, min(1.0, (v - faixa) / faixa))
            componentes.append((norm if bom_alto else -norm, peso))
            ev.append(f"{rotulo} de {v:.1f}%")

        div_ebitda = fund.get("divida_liquida_ebitda")
        if div_ebitda is None:
            faltando.append("divida_liquida_ebitda")
        else:
            # Até 1x é confortável; acima de 3x é alavancagem relevante.
            norm = max(-1.0, min(1.0, (2.0 - div_ebitda) / 2.0))
            componentes.append((norm, 0.18))
            ev.append(f"dívida líquida/EBITDA de {div_ebitda:.2f}x")
            if div_ebitda > 3.5:
                contra.append(f"endividamento de {div_ebitda:.1f}x EBITDA "
                              f"limita a resiliência a juros altos")

        fcf = fund.get("fluxo_caixa_livre_positivo")
        if fcf is None:
            faltando.append("fluxo_caixa_livre_positivo")
        else:
            componentes.append((0.8 if fcf else -0.8, 0.10))
            ev.append("fluxo de caixa livre positivo" if fcf
                      else "fluxo de caixa livre negativo")
            if not fcf:
                contra.append("queima de caixa: lucro contábil sem geração "
                              "de caixa não paga dividendo nem reduz dívida")

        pl = fund.get("p_l")
        if pl is None:
            faltando.append("p_l")
        else:
            # Valuation entra invertido: múltiplo alto reduz a atratividade.
            norm = max(-1.0, min(1.0, (14.0 - pl) / 10.0))
            componentes.append((norm, 0.08))
            ev.append(f"P/L de {pl:.1f}")

        if not componentes:
            return self._sem_dados(
                faltando, "nenhum indicador fundamentalista disponível")

        soma_pesos = sum(p for _, p in componentes)
        bruto = sum(v * p for v, p in componentes) / soma_pesos
        # A direção importa: fundamentos bons favorecem compra e desfavorecem
        # venda a descoberto.
        valor = bruto * d
        cobertura = soma_pesos / 0.90
        confianca = max(0.20, min(0.90, cobertura))

        parecer = self._parecer(valor, confianca, evidencias=ev,
                                contraindicacoes=contra, metricas=dict(fund))
        parecer.dados_faltando = faltando
        return parecer

    def _fii(self, ctx: ContextoAnalise, d: float,
             fund: dict[str, Any]) -> ParecerAgente:
        """FII usa o avaliador dedicado, que já pune armadilha de DY."""
        from ..models import FiiOpportunity
        from ..passive.fii import avaliar_fii

        obrigatorios = ("preco", "dy_12m", "p_vp")
        faltando = [k for k in obrigatorios if fund.get(k) is None]
        if faltando:
            return self._sem_dados(faltando,
                                   f"faltam campos obrigatórios de FII: "
                                   f"{', '.join(faltando)}")

        fii = FiiOpportunity(
            ticker=ctx.symbol, nome=fund.get("nome", ctx.symbol),
            segmento=fund.get("segmento", "hibrido"),
            preco=float(fund["preco"]), dy_12m=float(fund["dy_12m"]),
            p_vp=float(fund["p_vp"]),
            vacancia_pct=fund.get("vacancia_pct"),
            liquidez_diaria=float(fund.get("liquidez_diaria", 0.0)),
            num_imoveis=fund.get("num_imoveis"),
            patrimonio_liquido=fund.get("patrimonio_liquido"))
        avaliado = avaliar_fii(fii)

        # Score 0..100 mapeado para -1..+1, com a direção aplicada.
        valor = ((avaliado.score - 50.0) / 50.0) * d
        ev = [f.detalhe for f in avaliado.fatores if f.detalhe][:6]
        return self._parecer(
            valor, 0.75, evidencias=ev, contraindicacoes=avaliado.alertas,
            metricas={"score_fii": round(avaliado.score, 1),
                      "classificacao": avaliado.classificacao,
                      "dy_12m": avaliado.dy_12m, "p_vp": avaliado.p_vp})


# ============================================================== 4) MACRO
class AgenteMacro(AgenteBase):
    """Juros, inflação, liquidez e condições financeiras."""

    nome = "macro"
    peso = 0.10
    classes_suportadas = TODAS

    def analisar(self, ctx: ContextoAnalise, direcao: int) -> ParecerAgente:
        if ctx.macro is None:
            return self._sem_dados(
                ["indicadores_macro"],
                "FONTE NÃO CONFIGURADA: sem dados macroeconômicos. O agente "
                "se abstém em vez de assumir cenário neutro.")

        m = ctx.macro
        d = float(direcao)
        ev: list[str] = []
        contra: list[str] = []
        faltando: list[str] = []
        componentes: list[tuple[float, float]] = []

        # Regime de liquidez: risk-on favorece ativo de risco.
        risk = m.get("regime_risco")
        if risk is None:
            faltando.append("regime_risco")
        else:
            mapa = {"risk_on": 0.7, "neutro": 0.0, "risk_off": -0.7}
            v = mapa.get(str(risk).lower(), 0.0)
            componentes.append((v, 0.35))
            ev.append(f"ambiente {risk}")
            if v < 0 and d > 0:
                contra.append("ambiente risk-off costuma penalizar compra "
                              "em ativo de risco")

        juros_dir = m.get("direcao_juros")
        if juros_dir is None:
            faltando.append("direcao_juros")
        else:
            mapa = {"queda": 0.6, "estavel": 0.0, "alta": -0.6}
            v = mapa.get(str(juros_dir).lower(), 0.0)
            # Juros em queda favorecem ativo de risco e FII; em alta, o oposto.
            if ctx.asset_class in (AssetClass.FII, AssetClass.ACAO,
                                   AssetClass.CRIPTO):
                componentes.append((v, 0.35))
                ev.append(f"juros em {juros_dir}")
            elif ctx.asset_class is AssetClass.RENDA_FIXA:
                # Em renda fixa prefixada, alta de juros é oportunidade de
                # carrego melhor na entrada — sinal invertido.
                componentes.append((-v, 0.35))
                ev.append(f"juros em {juros_dir} (leitura invertida para "
                          f"renda fixa)")

        inflacao = m.get("surpresa_inflacao_pct")
        if inflacao is None:
            faltando.append("surpresa_inflacao_pct")
        else:
            v = max(-1.0, min(1.0, -inflacao / 0.5))
            componentes.append((v, 0.30))
            ev.append(f"surpresa de inflação {inflacao:+.2f} p.p.")

        if not componentes:
            return self._sem_dados(faltando,
                                   "nenhum indicador macro disponível")

        soma = sum(p for _, p in componentes)
        valor = (sum(v * p for v, p in componentes) / soma) * d
        confianca = max(0.20, min(0.80, soma))
        parecer = self._parecer(valor, confianca, evidencias=ev,
                                contraindicacoes=contra, metricas=dict(m))
        parecer.dados_faltando = faltando
        return parecer


# ======================================================== 5) NOTÍCIAS
class AgenteNoticias(AgenteBase):
    """Notícias e sentimento, com distinção entre fato e rumor.

    Rumor NUNCA move o parecer sozinho. A classificação
    CONFIRMADA/DECLARACAO/RUMOR/OPINIAO pondera o impacto: um rumor de
    impacto crítico entra com peso reduzido e aparece como contraindicação,
    não como razão para entrar.
    """

    nome = "noticias"
    peso = 0.08
    classes_suportadas = TODAS

    PESO_CONFIABILIDADE = {
        "confirmada": 1.0,
        "declaracao": 0.6,
        "nao_verificada": 0.25,
        "rumor": 0.15,
        "opiniao": 0.05,
    }
    PESO_IMPACTO = {"critico": 1.0, "alto": 0.7, "medio": 0.4,
                    "baixo": 0.15, "ruido": 0.0}

    def analisar(self, ctx: ContextoAnalise, direcao: int) -> ParecerAgente:
        if ctx.noticias is None:
            return self._sem_dados(
                ["noticias"],
                "FONTE NÃO CONFIGURADA: sem agregador de notícias. O agente "
                "se abstém — 'sem notícia' e 'notícia neutra' são coisas "
                "diferentes.")
        if not ctx.noticias:
            return self._parecer(
                0.0, 0.30,
                evidencias=["nenhuma notícia relevante na janela analisada"])

        d = float(direcao)
        ev: list[str] = []
        contra: list[str] = []
        soma = 0.0
        peso_total = 0.0
        criticos_nao_confirmados = 0

        for n in ctx.noticias:
            classificacao = str(n.get("classificacao", "rumor")).lower()
            impacto = str(n.get("impacto", "baixo")).lower()
            direcao_noticia = float(n.get("direcao", 0.0))   # -1..+1
            titulo = str(n.get("titulo", ""))[:80]
            fonte = str(n.get("fonte", "desconhecida"))

            w_conf = self.PESO_CONFIABILIDADE.get(classificacao, 0.1)
            w_imp = self.PESO_IMPACTO.get(impacto, 0.1)
            peso = w_conf * w_imp
            if peso <= 0:
                continue

            soma += direcao_noticia * peso
            peso_total += peso
            linha = (f"[{impacto}/{classificacao}] {titulo} "
                     f"(fonte: {fonte})")
            if classificacao in ("rumor", "nao_verificada") and impacto in (
                    "critico", "alto"):
                criticos_nao_confirmados += 1
                contra.append(f"NÃO CONFIRMADA e de impacto {impacto}: "
                              f"{titulo}")
            else:
                ev.append(linha)

        if peso_total <= 0:
            return self._parecer(
                0.0, 0.20,
                evidencias=["apenas ruído e opinião na janela analisada"])

        bruto = soma / peso_total
        valor = bruto * d
        # Confiança limitada: notícia é o fator mais ruidoso do conjunto.
        confianca = max(0.15, min(0.65, peso_total / 2.0))
        if criticos_nao_confirmados:
            confianca *= 0.6
            contra.append(
                f"{criticos_nao_confirmados} notícia(s) de alto impacto sem "
                f"confirmação: não operar com base nelas")

        return self._parecer(
            valor, confianca, evidencias=ev[:6], contraindicacoes=contra,
            metricas={"n_noticias": len(ctx.noticias),
                      "peso_total": round(peso_total, 3),
                      "nao_confirmadas_relevantes": criticos_nao_confirmados})


# ====================================================== 6) QUANTITATIVO
class AgenteQuantitativo(AgenteBase):
    """Estatística histórica medida: expectativa, IC e amostra.

    É o único agente que fala sobre *evidência de vantagem*, e é
    deliberadamente o de maior peso. Sem estatística, ele se abstém em vez de
    assumir que a estratégia funciona.
    """

    nome = "quantitativo"
    peso = 0.22
    classes_suportadas = TODAS

    def analisar(self, ctx: ContextoAnalise, direcao: int) -> ParecerAgente:
        est = ctx.estatistica
        if est is None:
            return self._sem_dados(
                ["estatistica_historica"],
                "sem backtest walk-forward para este ativo e direção: "
                "CONFIANÇA ESTATÍSTICA INSUFICIENTE")

        ev: list[str] = []
        contra: list[str] = []

        n = getattr(est, "n", None) or getattr(est, "trades", 0)
        if n <= 0:
            return self._sem_dados(
                ["estatistica_historica"],
                "estatística presente mas sem nenhuma operação na amostra")

        # Aceita ExpectedValue ou BacktestStats.
        ev_liquido = getattr(est, "ev_liquido_r", None)
        if ev_liquido is None:
            ev_liquido = getattr(est, "expectancy_r", 0.0)
        ev_pess = getattr(est, "ev_pessimista_r", None)
        ic = getattr(est, "ic_expectativa", None)
        wr = getattr(est, "p_ganho", None)
        if wr is None:
            wr = getattr(est, "win_rate", 0.0)

        ev.append(f"{n} operações medidas com expectativa "
                  f"{ev_liquido:+.3f}R e acerto de {wr:.1%}")

        # O valor vem da expectativa, saturando em 0,5R.
        valor = max(-1.0, min(1.0, ev_liquido / 0.5))

        # A confiança vem do TAMANHO e da QUALIDADE da amostra.
        confianca = min(0.95, 0.15 + 0.80 * min(1.0, n / 120.0))

        if ic is not None:
            ev.append(f"IC 95% da expectativa: "
                      f"[{ic.inferior:+.3f}, {ic.superior:+.3f}]R")
            if ic.inferior <= 0 < ev_liquido:
                contra.append(
                    f"o piso do intervalo de confiança ({ic.inferior:+.3f}R) "
                    f"não exclui vantagem zero: a média positiva pode ser "
                    f"sorte da amostra")
                confianca *= 0.55
        # A amplitude só é julgável na TAXA DE ACERTO, que é proporção. Para
        # o IC da expectativa (em R) o que importa é o piso excluir zero, já
        # verificado acima.
        ic_wr = getattr(est, "ic_win_rate", None)
        if ic_wr is not None and not getattr(ic_wr, "informativo", True):
            contra.append(
                f"intervalo da taxa de acerto vai de {ic_wr.inferior:.1%} a "
                f"{ic_wr.superior:.1%}: amostra pequena demais para sustentar "
                f"decisão")
            confianca *= 0.7

        if ev_pess is not None and ev_pess <= 0 < ev_liquido:
            contra.append(f"EV recalculado no pior caso do IC é "
                          f"{ev_pess:+.3f}R")

        if n < 30:
            contra.append(f"amostra de {n} operações abaixo do mínimo "
                          f"estatístico de 30")
            confianca *= 0.5

        pf = getattr(est, "profit_factor", None)
        if pf is not None:
            ev.append(f"profit factor {pf:.2f}")
        seq = getattr(est, "consecutive_losses", None)
        if seq:
            ev.append(f"pior sequência observada: {seq} perdas seguidas")

        return self._parecer(
            valor, confianca, evidencias=ev, contraindicacoes=contra,
            metricas={"n": n, "expectativa_r": round(float(ev_liquido), 4),
                      "win_rate": round(float(wr), 4)})


# ======================================================== 7) LIQUIDEZ
class AgenteLiquidez(AgenteBase):
    """Liquidez e custo de transação — o fator que invalida tese boa.

    Vantagem de 0,3R por operação desaparece se o spread custa 0,25R para
    entrar e sair.
    """

    nome = "liquidez"
    peso = 0.10
    classes_suportadas = TODAS

    MINIMOS_24H_USD = {
        AssetClass.CRIPTO: 20_000_000.0,
        AssetClass.ACAO: 5_000_000.0,
        AssetClass.ETF: 2_000_000.0,
        AssetClass.FII: 300_000.0,
    }

    def analisar(self, ctx: ContextoAnalise, direcao: int) -> ParecerAgente:
        snap = ctx.snapshot
        vol = getattr(snap, "volume_24h_usd", None) if snap else None
        spread = (ctx.derivativos or {}).get("spread_pct")

        if vol is None and spread is None:
            return self._sem_dados(
                ["volume_24h_usd", "spread_pct"],
                "sem dados de liquidez para avaliar custo de entrada e saída")

        ev: list[str] = []
        contra: list[str] = []
        componentes: list[tuple[float, float]] = []

        if vol is not None:
            minimo = self.MINIMOS_24H_USD.get(ctx.asset_class, 1_000_000.0)
            import math
            razao = vol / minimo if minimo else 1.0
            v = max(-1.0, min(1.0, math.log10(max(razao, 1e-6)) / 1.3))
            componentes.append((v, 0.6))
            ev.append(f"volume 24h de US$ {vol:,.0f} "
                      f"({razao:.1f}x o mínimo da classe)")
            if razao < 1.0:
                contra.append(
                    f"liquidez abaixo do mínimo para {ctx.asset_class.value}: "
                    f"saída pode exigir desconto relevante")

        if spread is not None:
            v = max(-1.0, min(1.0, (0.10 - spread) / 0.10))
            componentes.append((v, 0.4))
            ev.append(f"spread de {spread:.3f}%")
            if spread > 0.20:
                contra.append(f"spread de {spread:.2f}% consome parte "
                              f"relevante da vantagem esperada")

        soma = sum(p for _, p in componentes)
        # Liquidez não tem direção: ela habilita ou impede, para os dois lados.
        valor = sum(v * p for v, p in componentes) / soma
        return self._parecer(valor, 0.80, evidencias=ev,
                             contraindicacoes=contra,
                             metricas={"volume_24h_usd": vol,
                                       "spread_pct": spread})


def agentes_padrao(registro_modelos=None) -> list[AgenteBase]:
    """Conjunto completo. O agente de risco NÃO está aqui: ele veta, não pontua.

    O agente de ML só entra quando o registro tem modelo em produção, e não
    por estar disponível. A cobertura do consenso é medida como fração do
    PESO NOMINAL disponível: um agente de peso 0,18 que se absteria sempre —
    porque não há modelo treinado — derrubaria a cobertura em 18 pontos e
    faria o sistema reprovar análises que hoje passam. Incluí-lo "para ficar
    completo" tornaria o sistema pior, não mais capaz.
    """
    agentes: list[AgenteBase] = [
        AgenteTecnico(), AgenteQuantitativo(), AgenteFundamentos(),
        AgenteDerivativos(), AgenteMacro(), AgenteNoticias(),
        AgenteLiquidez(),
    ]
    if registro_modelos is not None and registro_modelos.tem_producao():
        from .ml import AgenteML
        agentes.append(AgenteML(registro_modelos.buscador()))
    return agentes
