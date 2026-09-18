"""FIIs: o score precisa punir armadilha de dividend yield, não premiá-la."""
import json

import pytest

from investai.models import FiiOpportunity
from investai.passive import (
    LIQUIDEZ_MINIMA, PESOS_FII, SnapshotFiiProvider, avaliar_fii,
    carteira_sugerida, classe_do_segmento, ranquear, tetos_aplicaveis,
)
from investai.passive.data import BrapiFiiProvider


def fii(**kw) -> FiiOpportunity:
    base = dict(ticker="TEST11", nome="Fundo Teste", segmento="logistica",
                preco=100.0, dy_12m=9.5, p_vp=0.92, vacancia_pct=1.5,
                liquidez_diaria=2_000_000.0, num_imoveis=20,
                patrimonio_liquido=2_000_000_000.0)
    base.update(kw)
    return FiiOpportunity(**base)


def test_pesos_somam_um():
    assert sum(PESOS_FII.values()) == pytest.approx(1.0)


def test_fundo_saudavel_pontua_alto():
    assert avaliar_fii(fii()).score > 80


# ------------------------------------------------------- armadilhas de yield
def test_dy_absurdo_e_penalizado_nao_premiado():
    """DY de 24% não é oportunidade — quase sempre é receita não recorrente."""
    saudavel = avaliar_fii(fii(dy_12m=9.5)).score
    armadilha = avaliar_fii(fii(dy_12m=24.0)).score
    assert armadilha < saudavel
    assert armadilha < 60


def test_dy_zero_e_praticamente_desqualificante():
    r = avaliar_fii(fii(dy_12m=0.0))
    assert r.score <= 35
    assert r.classificacao == "evitar"


def _fator(resultado, nome):
    return next(f for f in resultado.fatores if f.nome == nome)


def test_faixa_de_dy_depende_do_tipo_de_fundo():
    """12,5% a.a. é normal em fundo de papel e alto para tijolo.

    O teste compara o FATOR de DY isoladamente: comparar o score total de
    dois fundos diferentes não isola o efeito, porque ocupação, porte e
    diversificação também mudam entre tijolo e papel.
    """
    papel = avaliar_fii(fii(segmento="recebiveis", dy_12m=12.5,
                            vacancia_pct=None, num_imoveis=None))
    tijolo = avaliar_fii(fii(segmento="logistica", dy_12m=12.5))
    assert _fator(papel, "dy_sustentavel").valor > _fator(tijolo, "dy_sustentavel").valor
    assert "dentro da faixa" in _fator(papel, "dy_sustentavel").detalhe
    assert "acima de" in _fator(tijolo, "dy_sustentavel").detalhe


def test_mesmo_dy_em_tijolo_perde_pontos_conforme_sobe():
    """Dentro de um mesmo tipo de fundo, DY acima da faixa piora o fator."""
    na_faixa = avaliar_fii(fii(segmento="logistica", dy_12m=9.5))
    acima = avaliar_fii(fii(segmento="logistica", dy_12m=13.0))
    muito_acima = avaliar_fii(fii(segmento="logistica", dy_12m=17.0))
    v = [_fator(r, "dy_sustentavel").valor
         for r in (na_faixa, acima, muito_acima)]
    assert v[0] > v[1] > v[2]
    assert muito_acima.score < na_faixa.score


def test_classificacao_de_segmento():
    assert classe_do_segmento("recebiveis") == "papel"
    assert classe_do_segmento("logistica") == "tijolo"
    assert classe_do_segmento("fundo de fundos") == "fof"
    assert classe_do_segmento("coisa desconhecida") == "hibrido"


# ------------------------------------------------------------------- tetos
def test_vacancia_alta_limita_o_score_mesmo_com_bons_indicadores():
    """Uma soma ponderada diluiria esse defeito. O teto impede."""
    r = avaliar_fii(fii(vacancia_pct=26.0))
    assert r.score <= 32
    assert any("vacância" in a for a in r.alertas)


def test_iliquidez_limita_severamente():
    r = avaliar_fii(fii(liquidez_diaria=50_000.0))
    assert r.score <= 35
    assert any("liquidez" in a.lower() for a in r.alertas)


def test_liquidez_no_limite_ainda_penaliza():
    assert avaliar_fii(fii(liquidez_diaria=LIQUIDEZ_MINIMA - 1)).score <= 52


def test_monoativo_de_tijolo_e_limitado():
    r = avaliar_fii(fii(num_imoveis=1))
    assert r.score <= 58
    assert any("monoativo" in a for a in r.alertas)


def test_fundo_de_papel_sem_vacancia_nao_e_punido():
    """Fundo de CRI não tem vacância física — ausência do dado não é defeito."""
    papel = avaliar_fii(fii(segmento="recebiveis", vacancia_pct=None,
                            num_imoveis=None, dy_12m=12.0))
    tijolo_sem_dado = avaliar_fii(fii(segmento="logistica", vacancia_pct=None))
    assert papel.score > tijolo_sem_dado.score


def test_agio_alto_limita():
    assert avaliar_fii(fii(p_vp=1.40)).score <= 55


def test_desconto_extremo_e_bandeira_amarela_nao_pechincha():
    """P/VP de 0,50 significa que o mercado enxerga um problema."""
    desconto_saudavel = avaliar_fii(fii(p_vp=0.88)).score
    desconto_extremo = avaliar_fii(fii(p_vp=0.50)).score
    assert desconto_extremo < desconto_saudavel


def test_patrimonio_pequeno_limita():
    assert avaliar_fii(fii(patrimonio_liquido=50_000_000.0)).score <= 50


def test_tetos_listam_o_motivo():
    tetos = tetos_aplicaveis(fii(vacancia_pct=30.0, liquidez_diaria=10_000.0))
    assert len(tetos) >= 2
    assert all(isinstance(t, float) and isinstance(m, str) for t, m in tetos)


def test_renda_mensal_por_mil_reais():
    r = avaliar_fii(fii(dy_12m=12.0))
    assert r.renda_mensal_por_1k == pytest.approx(10.0)      # 12% / 12 meses


# --------------------------------------------------------------- ranking
def test_ranking_ordena_por_score():
    lista = ranquear([fii(ticker="BOM11"),
                      fii(ticker="RUIM11", vacancia_pct=30.0, dy_12m=22.0),
                      fii(ticker="MEDIO11", p_vp=1.12)])
    assert [f.ticker for f in lista][0] == "BOM11"
    assert [f.ticker for f in lista][-1] == "RUIM11"
    assert lista[0].score >= lista[1].score >= lista[2].score


# -------------------------------------------------------------- carteira
def test_carteira_respeita_teto_por_fundo():
    fundos = [fii(ticker=f"F{i}11", segmento=seg)
              for i, seg in enumerate(["logistica", "shoppings", "renda urbana",
                                       "recebiveis", "hibrido", "fof"])]
    c = carteira_sugerida(fundos, 100_000.0, max_por_fundo_pct=25.0)
    assert all(i["peso_pct"] <= 25.0 + 1e-6 for i in c["itens"])
    assert sum(i["peso_pct"] for i in c["itens"]) == pytest.approx(100.0, abs=0.1)


def test_carteira_limita_dois_fundos_por_segmento():
    """Cinco fundos de logística não são diversificação."""
    fundos = [fii(ticker=f"LOG{i}11", segmento="logistica") for i in range(5)]
    c = carteira_sugerida(fundos, 100_000.0)
    assert len(c["itens"]) <= 2


def test_carteira_vazia_quando_nada_atinge_o_score():
    c = carteira_sugerida([fii(vacancia_pct=30.0)], 10_000.0, min_score=70.0)
    assert c["itens"] == []
    assert "não forçar aporte" in c["aviso"]


def test_carteira_nao_investe_mais_que_o_capital():
    fundos = [fii(ticker=f"F{i}11", preco=97.0,
                  segmento=["logistica", "shoppings", "recebiveis"][i % 3])
              for i in range(6)]
    c = carteira_sugerida(fundos, 10_000.0)
    assert c["investido"] <= 10_000.0
    assert c["sobra_caixa"] >= 0


def test_carteira_capital_invalido():
    with pytest.raises(ValueError):
        carteira_sugerida([fii()], 0.0)


def test_carteira_traz_aviso_sobre_rendimento_variavel():
    c = carteira_sugerida([fii(), fii(ticker="B11", segmento="shoppings")], 20_000.0)
    assert "não é fixo" in c["aviso"] or "nem garantido" in c["aviso"]


# ---------------------------------------------------------------- provider
def _snapshot(tmp_path, atualizado_em="2025-01-31", fundos=None):
    caminho = tmp_path / "fiis_snapshot.json"
    caminho.write_text(json.dumps({
        "atualizado_em": atualizado_em,
        "fonte": "teste",
        "fundos": fundos if fundos is not None else [{
            "ticker": "abc11", "nome": "ABC", "segmento": "logistica",
            "preco": 100.0, "dy_12m": 9.0, "p_vp": 0.9,
            "vacancia_pct": 2.0, "liquidez_diaria": 1e6,
            "num_imoveis": 10, "patrimonio_liquido": 1e9,
        }],
    }), encoding="utf-8")
    return caminho


def test_snapshot_normaliza_ticker_maiusculo(tmp_path):
    fundos = SnapshotFiiProvider(_snapshot(tmp_path)).fundos()
    assert [f.ticker for f in fundos] == ["ABC11"]


def test_snapshot_marca_desatualizado(tmp_path):
    pv = SnapshotFiiProvider(_snapshot(tmp_path, "2020-01-01"))
    fundos = pv.fundos()
    assert pv.metadados["desatualizado"] is True
    assert "desatualizado" in fundos[0].fonte


def test_snapshot_recente_nao_e_marcado(tmp_path):
    from datetime import date
    pv = SnapshotFiiProvider(_snapshot(tmp_path, date.today().isoformat()))
    pv.fundos()
    assert pv.metadados["desatualizado"] is False


def test_snapshot_ignora_fundo_com_campo_faltando(tmp_path):
    fundos = SnapshotFiiProvider(_snapshot(tmp_path, fundos=[
        {"ticker": "OK11", "nome": "N", "segmento": "logistica",
         "preco": 100.0, "dy_12m": 9.0, "p_vp": 0.9},
        {"ticker": "RUIM11", "nome": "faltando preco"},
    ])).fundos()
    assert [f.ticker for f in fundos] == ["OK11"]


def test_snapshot_filtra_por_ticker(tmp_path):
    caminho = _snapshot(tmp_path, fundos=[
        {"ticker": "A11", "nome": "A", "segmento": "logistica", "preco": 10.0,
         "dy_12m": 9.0, "p_vp": 0.9},
        {"ticker": "B11", "nome": "B", "segmento": "shoppings", "preco": 20.0,
         "dy_12m": 9.0, "p_vp": 0.9},
    ])
    assert [f.ticker for f in SnapshotFiiProvider(caminho).fundos(["b11"])] == ["B11"]


def test_snapshot_arquivo_ausente_nao_explode(tmp_path):
    pv = SnapshotFiiProvider(tmp_path / "nao_existe.json")
    assert pv.fundos() == []
    assert "erro" in pv.metadados


def test_snapshot_json_invalido_nao_explode(tmp_path):
    ruim = tmp_path / "ruim.json"
    ruim.write_text("{isso nao e json", encoding="utf-8")
    pv = SnapshotFiiProvider(ruim)
    assert pv.fundos() == []
    assert "erro" in pv.metadados


class _ClienteFalso:
    """Simula a brapi sem rede."""

    def __init__(self, payload=None, status=200):
        self.payload = payload or {}
        self.status = status

    def get(self, url, params=None):
        class _R:
            status_code = self.status
            def json(_self):  # noqa: N805
                return self.payload
        return _R()


def test_brapi_atualiza_preco_e_reescala_multiplos(tmp_path):
    """Preço novo com múltiplo velho seria incoerente: DY e P/VP são
    derivados do preço e têm que acompanhar."""
    base = SnapshotFiiProvider(_snapshot(tmp_path))
    cliente = _ClienteFalso({"results": [
        {"symbol": "ABC11", "regularMarketPrice": 50.0,
         "regularMarketVolume": 100_000}]})
    fundos = BrapiFiiProvider(base, client=cliente).fundos()
    f = fundos[0]
    assert f.preco == pytest.approx(50.0)
    assert f.dy_12m == pytest.approx(18.0)      # 9% * (100/50)
    assert f.p_vp == pytest.approx(0.45)        # 0,9 / (100/50)
    assert "brapi" in f.fonte


def test_brapi_indisponivel_mantem_snapshot(tmp_path):
    base = SnapshotFiiProvider(_snapshot(tmp_path))
    fundos = BrapiFiiProvider(base, client=_ClienteFalso(status=500)).fundos()
    assert fundos[0].preco == pytest.approx(100.0)
    assert "brapi" not in fundos[0].fonte


def test_brapi_ignora_preco_invalido(tmp_path):
    base = SnapshotFiiProvider(_snapshot(tmp_path))
    cliente = _ClienteFalso({"results": [
        {"symbol": "ABC11", "regularMarketPrice": 0}]})
    assert BrapiFiiProvider(base, client=cliente).fundos()[0].preco == pytest.approx(100.0)


def test_snapshot_do_projeto_e_valido_e_marcado_como_modelo():
    """O arquivo que acompanha o projeto tem que estar sempre marcado como
    desatualizado, para ninguém decidir aporte com valores de exemplo."""
    from pathlib import Path
    caminho = Path(__file__).resolve().parent.parent / "data" / "fiis_snapshot.json"
    pv = SnapshotFiiProvider(caminho)
    fundos = pv.fundos()
    assert len(fundos) >= 10
    assert pv.metadados["desatualizado"] is True
    assert "ilustrativos" in pv.metadados["observacao"].lower()
