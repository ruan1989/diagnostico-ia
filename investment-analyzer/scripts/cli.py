#!/usr/bin/env python3
"""CLI do InvestAI — usa o mesmo motor do painel, sem navegador.

Exemplos:
    python scripts/cli.py scan
    python scripts/cli.py scan --sintetico --symbols BTCUSDT,ETHUSDT
    python scripts/cli.py backtest BTCUSDT --tf 1H --lado long --barras 5000
    python scripts/cli.py fiis
    python scripts/cli.py carteira --capital 50000
    python scripts/cli.py validar          # roda backtest no universo inteiro
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from investai.analysis.screener import Screener               # noqa: E402
from investai.backtest.engine import rodar_backtest           # noqa: E402
from investai.config import Settings                          # noqa: E402
from investai.datahub import DataHub                          # noqa: E402
from investai.exchanges import (                              # noqa: E402
    BitgetClient, SyntheticProvider, credenciais_do_ambiente,
)
from investai.models import Side                              # noqa: E402
from investai.passive import (                                # noqa: E402
    carteira_sugerida, provider_padrao, ranquear,
)

AVISO = ("\n! Estimativas estatisticas, nao garantias. Mercado futuro com "
         "alavancagem pode zerar a conta.\n")


def montar(args: argparse.Namespace) -> tuple[Settings, DataHub]:
    settings = Settings.from_env()
    if getattr(args, "sintetico", False) or os.environ.get("INVESTAI_SYNTHETIC") == "1":
        provider = SyntheticProvider()
        print("# provider SINTETICO: dados simulados, nao sao cotacoes reais")
    else:
        provider = BitgetClient(credenciais_do_ambiente(),
                                product_type=settings.exec.product_type)
    return settings, DataHub(provider)


def cmd_scan(args: argparse.Namespace) -> int:
    settings, hub = montar(args)
    alvos = ([s.strip().upper() for s in args.symbols.split(",") if s.strip()]
             if args.symbols else None)
    screener = Screener(hub, settings)
    resultado = screener.scan(alvos, com_historico=not args.rapido)

    if args.json:
        print(json.dumps(resultado.to_dict(), ensure_ascii=False, indent=2))
        return 0

    print(f"\nVarredura de {len(resultado.analises)} pares em "
          f"{resultado.duracao_s:.1f}s\n")
    print(f"{'PAR':10}{'LADO':7}{'SCORE':>7}{'GRADE':>11}{'PROB':>7}"
          f"{'EXP R':>8}{'AMOSTRA':>9}{'REGIME':>22}")
    print("-" * 81)
    for a in resultado.analises:
        s = a.melhor
        if s is None:
            print(f"{a.symbol:10}{'—':7}{'':>7}{'erro':>11}  {a.erro[:40]}")
            continue
        n = s.hist.trades if s.hist else 0
        print(f"{s.symbol:10}{s.side.value:7}{s.score:7.1f}{s.grade.value:>11}"
              f"{s.prob_acerto_estimada:7.1%}{s.retorno_esperado_r:+8.2f}"
              f"{n:9}{s.regime.value:>22}")

    if resultado.operaveis:
        print(f"\n{len(resultado.operaveis)} sinal(is) operavel(is):\n")
        for s in resultado.operaveis:
            print(f"  {s.symbol} {s.side.value.upper()} (grade {s.grade.value}, "
                  f"score {s.score:.1f})")
            print(f"    entrada {s.entry:.6g} | stop {s.stop_loss:.6g} "
                  f"({s.stop_distance_pct:.2f}%)")
            print(f"    alvos   {' / '.join(f'{t:.6g}' for t in s.take_profits)}")
            print(f"    {s.invalidacao}")
            for nota in s.notas:
                print(f"    ! {nota}")
            print()
    else:
        print("\nNenhum sinal passou nos dois portoes (tecnico + estatistico).")
        print("Isso e um resultado valido: nao ha evidencia suficiente agora.")
    print(AVISO)
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    settings, hub = montar(args)
    tf = args.tf or settings.signal.timeframe_principal
    velas = hub.historico(args.symbol.upper(), tf, barras=args.barras)
    if len(velas) < 300:
        print(f"historico insuficiente: {len(velas)} candles", file=sys.stderr)
        return 1
    snap = hub.ticker(args.symbol.upper())
    lados = ([Side.LONG, Side.SHORT] if args.lado == "ambos"
             else [Side(args.lado)])
    print(f"\n{args.symbol.upper()} {tf} — {len(velas)} candles "
          f"({velas[0].dt:%Y-%m-%d} a {velas[-1].dt:%Y-%m-%d})")
    print("premissas: entrada na abertura da barra seguinte ao sinal; "
          "stop antes do alvo em empate;")
    print(f"           taxa {settings.exec.taxa_taker_pct}% + slippage "
          f"{settings.exec.slippage_pct}% por lado; funding a cada 8h\n")
    print(f"{'LADO':7}{'TRADES':>7}{'ACERTO':>8}{'PF':>7}{'EXP R':>8}"
          f"{'MAX DD':>8}{'PIOR SEQ':>9}{'PNL':>10}")
    print("-" * 64)
    for lado in lados:
        r = rodar_backtest(args.symbol.upper(), tf, velas, settings.signal,
                           settings.exec, side_filtro=lado, snapshot=snap,
                           funding_rate=snap.funding_rate if snap else 0.0001)
        st = r.stats
        alerta = "" if st.trades >= settings.signal.min_trades_historico else "  <- amostra pequena"
        print(f"{lado.value:7}{st.trades:7}{st.win_rate:8.1%}{st.profit_factor:7.2f}"
              f"{st.expectancy_r:+8.3f}{st.max_drawdown_pct:7.2f}%"
              f"{st.consecutive_losses:9}{st.net_pnl:+10.2f}{alerta}")
    print(AVISO)
    return 0


def cmd_validar(args: argparse.Namespace) -> int:
    """Mede a estatistica de todo o universo — a checagem mais importante."""
    settings, hub = montar(args)
    print(f"\nValidando {len(settings.universo)} pares em "
          f"{settings.signal.timeframe_principal} ({args.barras} barras)\n")
    print(f"{'PAR':10}{'LADO':7}{'TRADES':>7}{'ACERTO':>8}{'PF':>7}"
          f"{'EXP R':>8}{'MAX DD':>8}{'APROVA':>8}")
    print("-" * 63)
    aprovados = 0
    total = 0
    for sym in settings.universo:
        for lado in (Side.LONG, Side.SHORT):
            try:
                velas = hub.historico(sym, settings.signal.timeframe_principal,
                                      barras=args.barras)
                if len(velas) < 300:
                    continue
                r = rodar_backtest(sym, settings.signal.timeframe_principal,
                                   velas, settings.signal, settings.exec,
                                   side_filtro=lado)
            except Exception as exc:                    # noqa: BLE001
                print(f"{sym:10}{lado.value:7}  erro: {exc}")
                continue
            st = r.stats
            total += 1
            ok = (st.trades >= settings.signal.min_trades_historico
                  and st.win_rate >= settings.signal.min_win_rate_historico
                  and st.profit_factor >= settings.signal.min_profit_factor_historico
                  and st.expectancy_r >= settings.signal.min_expectancy_r)
            aprovados += int(ok)
            print(f"{sym:10}{lado.value:7}{st.trades:7}{st.win_rate:8.1%}"
                  f"{st.profit_factor:7.2f}{st.expectancy_r:+8.3f}"
                  f"{st.max_drawdown_pct:7.2f}%{'  sim' if ok else '  nao':>8}")
    print(f"\n{aprovados} de {total} combinacoes par/direcao passam nos minimos "
          f"de qualidade.")
    print("Somente essas podem gerar ordem automatica. As demais ficam em "
          "observacao.")
    print(AVISO)
    return 0


def cmd_fiis(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    provider = provider_padrao(settings.data_dir,
                               token_brapi=os.environ.get("BRAPI_TOKEN", ""),
                               usar_rede=not args.sintetico)
    fundos = provider.fundos()
    if not fundos:
        print(f"nenhum dado em {settings.data_dir}/fiis_snapshot.json",
              file=sys.stderr)
        return 1
    base = getattr(provider, "base_provider", provider)
    meta = getattr(base, "metadados", {})
    if meta.get("desatualizado"):
        print(f"\n! ATENCAO: fundamentais de {meta.get('atualizado_em')} "
              f"({meta.get('idade_dias')} dias). Atualize antes de aportar.")
    print(f"\n{'TICKER':9}{'SEGMENTO':20}{'SCORE':>7}{'DY':>7}{'P/VP':>6}"
          f"{'VAC':>7}{'R$/1k':>8}  CLASSIFICACAO")
    print("-" * 88)
    for f in ranquear(fundos):
        vac = "n/a" if f.vacancia_pct is None else f"{f.vacancia_pct:.1f}%"
        print(f"{f.ticker:9}{f.segmento:20}{f.score:7.1f}{f.dy_12m:7.2f}"
              f"{f.p_vp:6.2f}{vac:>7}{f.renda_mensal_por_1k:8.2f}  {f.classificacao}")
        for a in f.alertas:
            print(f"{'':9}! {a}")
    print("\n! Ranking tecnico, nao recomendacao. Rendimento de FII nao e fixo.\n")
    return 0


def cmd_carteira(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    provider = provider_padrao(settings.data_dir, usar_rede=not args.sintetico)
    fundos = provider.fundos()
    if not fundos:
        print("nenhum dado de FII disponivel", file=sys.stderr)
        return 1
    c = carteira_sugerida(fundos, args.capital, min_score=args.min_score,
                          max_fundos=args.max_fundos)
    if not c["itens"]:
        print(c["aviso"])
        return 0
    print(f"\nCarteira para R$ {args.capital:,.2f}\n")
    print(f"{'TICKER':9}{'PESO':>8}{'COTAS':>7}{'INVESTIDO':>13}{'RENDA/MES':>12}")
    print("-" * 49)
    for i in c["itens"]:
        print(f"{i['ticker']:9}{i['peso_pct']:7.2f}%{i['cotas']:7}"
              f"{i['valor_investido']:13,.2f}{i['renda_mensal_estimada']:12,.2f}")
    print("-" * 49)
    print(f"{'TOTAL':9}{'':>8}{'':>7}{c['investido']:13,.2f}"
          f"{c['renda_mensal_estimada']:12,.2f}")
    print(f"\nsobra em caixa: R$ {c['sobra_caixa']:,.2f}")
    print(f"DY medio ponderado: {c['dy_medio_ponderado']}%")
    print(f"renda anual estimada: R$ {c['renda_anual_estimada']:,.2f}")
    print(f"\n! {c['aviso']}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="investai", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sintetico", action="store_true",
                   help="usa gerador sintetico em vez da Bitget")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="varre o universo e lista oportunidades")
    s.add_argument("--symbols", default="", help="lista separada por virgula")
    s.add_argument("--rapido", action="store_true", help="sem backtest (mais rapido)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_scan)

    b = sub.add_parser("backtest", help="mede a estrategia em dados historicos")
    b.add_argument("symbol")
    b.add_argument("--tf", default="")
    b.add_argument("--lado", default="ambos", choices=["long", "short", "ambos"])
    b.add_argument("--barras", type=int, default=5000)
    b.set_defaults(func=cmd_backtest)

    v = sub.add_parser("validar", help="backtest de todo o universo")
    v.add_argument("--barras", type=int, default=5000)
    v.set_defaults(func=cmd_validar)

    f = sub.add_parser("fiis", help="ranking de FIIs para renda passiva")
    f.set_defaults(func=cmd_fiis)

    c = sub.add_parser("carteira", help="monta carteira de FII para um capital")
    c.add_argument("--capital", type=float, required=True)
    c.add_argument("--min-score", dest="min_score", type=float, default=62.0)
    c.add_argument("--max-fundos", dest="max_fundos", type=int, default=8)
    c.set_defaults(func=cmd_carteira)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrompido", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
