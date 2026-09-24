"""API HTTP e painel de análise.

Segurança do serviço
--------------------
O serviço escuta em 127.0.0.1 por padrão. Todo endpoint que MUDA algo (conectar
chave, armar modo real, ligar o motor, fechar posição) exige o cabeçalho
`X-API-Token`, comparado em tempo constante. Se `INVESTAI_API_TOKEN` não for
definido, um token é gerado no boot e impresso no log — nunca fica aberto.

A senha da conta Bitget não aparece em nenhum lugar deste arquivo. O que o
painel envia é chave de API + secret + passphrase, que são cifrados no
keystore local com a senha mestra. As respostas devolvem apenas o prefixo da
chave, para conferência visual.
"""
from __future__ import annotations

import hmac
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agents import ChiefInvestmentEngine
from .analysis.screener import Screener
from .assets import (
    CandidatoComparacao, CenarioMacro, Indexador, PerfilInvestidor,
    TipoRendaFixa, Titulo, analisar_acao, analisar_etf, comparar,
    comparar_titulos,
)
from .backtest.engine import rodar_backtest
from .data import AssetClass, DataKind, registry_padrao
from .ops import monitor_padrao
from .ops.comandos import (
    TravaOperacao, diagnostico, liberar_trava, parada_emergencia,
    status as status_operacional, status_texto as status_texto_fn,
)
from .ops.shadow_live import ShadowLive
from .orquestrador import Orquestrador
from .portfolio import (
    CENARIOS_PADRAO, analisar_diversificacao, matriz_correlacao,
    rodar_stress_test,
)
from .reporting import BlocoDesempenho, CentralDeAlertas, Journal
from .risk import RiskEngine
from .strategies import (
    CriteriosPromocao, Fase, StrategyRegistry, )
from .strategies.pipeline import rodar_pipeline_completo
from .validation import comparar_modos, monte_carlo
from .config import Settings
from .datahub import DataHub
from .exchanges import (
    ApiCredentials, BitgetClient, CredentialError, ExchangeError, Keystore,
    SyntheticProvider, credenciais_do_ambiente,
)
from .models import Side
from .passive import carteira_sugerida, provider_padrao, ranquear
from .risk import RiskManager
from .store import Store
from .trading import (
    CONFIRMACAO_LIVE, ControleIdempotencia, Executor, GuardaFase,
    TradingEngine,
)

log = logging.getLogger("investai.api")

# Frase exigida para religar o sistema depois de uma parada de emergência.
# Mesma lógica de armar o modo real: religar às pressas o que foi parado às
# pressas não deve ser um clique distraído.
CONFIRMACAO_LIBERAR = "LIBERAR OPERACAO"

AVISO_PADRAO = (
    "Este sistema calcula probabilidades a partir de dados históricos. "
    "Não existe entrada com acerto garantido em mercado futuro: qualquer "
    "operação pode dar prejuízo, e alavancagem pode zerar a conta. Os números "
    "de 'probabilidade' e 'retorno esperado' são estimativas estatísticas, "
    "não promessas."
)


# --------------------------------------------------------------------- modelos
class ConectarBitget(BaseModel):
    api_key: str = Field(min_length=8)
    api_secret: str = Field(min_length=8)
    passphrase: str = Field(min_length=1)
    senha_mestra: str = Field(min_length=8)
    salvar: bool = True


class Destravar(BaseModel):
    senha_mestra: str = Field(min_length=8)


class ArmarLive(BaseModel):
    confirmacao: str


class VincularEstrategia(BaseModel):
    chave: str


class ParadaEmergencia(BaseModel):
    motivo: str = "comando de emergência pelo painel"
    fechar_posicoes: bool = True


class LiberarTrava(BaseModel):
    # A liberação exige a frase, pelo mesmo motivo que armar o modo real
    # exige: religar um sistema que foi parado às pressas não deve ser um
    # clique distraído.
    confirmacao: str


class ConfirmarProposta(BaseModel):
    client_oid: str
    motivo: str = ""


class CarteiraFii(BaseModel):
    capital: float = Field(gt=0)
    max_por_fundo_pct: float = Field(default=20.0, gt=0, le=100)
    min_score: float = Field(default=62.0, ge=0, le=100)
    max_fundos: int = Field(default=8, ge=1, le=30)


class FecharPosicao(BaseModel):
    symbol: str
    fracao: float = Field(default=1.0, gt=0, le=1.0)


class RetomarOperacao(BaseModel):
    confirmacao: str


class AnalisarAcao(BaseModel):
    ticker: str
    fundamentos: dict[str, Any] | None = None


class AnalisarEtf(BaseModel):
    ticker: str
    dados: dict[str, Any] | None = None


class TituloRF(BaseModel):
    nome: str
    tipo: str
    indexador: str
    taxa: float
    prazo_dias: int = Field(gt=0)
    emissor: str = ""
    risco_credito: int = Field(default=3, ge=1, le=5)
    liquidez_diaria: bool = False
    marcacao_a_mercado: bool = True


class CompararRendaFixa(BaseModel):
    titulos: list[TituloRF]
    cdi_aa: float | None = None
    selic_aa: float | None = None
    ipca_aa: float | None = None
    valor_aplicado: float = Field(default=10_000.0, gt=0)


class CandidatoRequest(BaseModel):
    identificador: str
    classe: str
    retorno_nominal_aa: float | None = None
    incerteza_retorno: float | None = None
    volatilidade_aa: float | None = None
    drawdown_plausivel_pct: float | None = None
    # FRAÇÃO, não percentual: 0.15 para 15%. Aceitar 15.0 aqui produziria
    # retorno líquido negativo silenciosamente (`retorno × (1 − 15)`), que é
    # pior do que recusar a requisição.
    aliquota_ir: float = Field(default=0.0, ge=0.0, le=1.0,
                               description="fração (0.15 = 15%), não %")
    isento_ir: bool = False
    liquidez_dias: int | None = None
    horizonte_minimo_meses: int | None = None
    risco_credito: int | None = None
    garantia: str = ""


class CompararClasses(BaseModel):
    candidatos: list[CandidatoRequest]
    inflacao_aa: float | None = None
    horizonte_meses: int = Field(default=36, gt=0)
    tolerancia_drawdown_pct: float = Field(default=15.0, gt=0)
    necessidade_liquidez_dias: int = Field(default=30, ge=0)
    objetivo: str = "crescimento"


class RegistrarEstatistica(BaseModel):
    symbol: str
    side: str = Field(pattern="^(long|short)$")
    retornos_r: list[float] = Field(min_length=1)


class ValidarEstrategia(BaseModel):
    strategy_id: str = "confluencia_tendencia"
    symbol: str = "BTCUSDT"
    timeframe: str = "1H"
    lado: str = Field(default="long", pattern="^(long|short)$")
    barras: int = Field(default=6000, ge=1000, le=20000)
    n_ciclos: int = Field(default=4, ge=2, le=10)
    score_minimo: float = 66.0


class MonteCarloRequest(BaseModel):
    retornos_r: list[float] = Field(min_length=1)
    n_simulacoes: int = Field(default=3000, ge=100, le=20000)
    risco_por_trade_frac: float = Field(default=0.005, gt=0, lt=1)
    modo: str = Field(default="blocos", pattern="^(iid|blocos)$")


class StressRequest(BaseModel):
    capital: float = Field(gt=0)


# ----------------------------------------------------------------- aplicação
class AppState:
    """Estado compartilhado do processo — montado uma vez no boot."""

    def __init__(self, settings: Settings | None = None,
                 usar_sintetico: bool | None = None):
        self.settings = settings or Settings.from_env()
        Path(self.settings.data_dir).mkdir(parents=True, exist_ok=True)

        self.token = os.environ.get("INVESTAI_API_TOKEN", "").strip()
        self.token_gerado = False
        if not self.token:
            self.token = secrets.token_urlsafe(24)
            self.token_gerado = True

        self.store = Store(self.settings.db_path)
        self.keystore = Keystore(Path(self.settings.data_dir) / "bitget_keystore.json")
        self.credenciais: ApiCredentials | None = credenciais_do_ambiente()
        self.bitget: BitgetClient | None = None
        self.erro_conexao = ""

        # Fallback sintético permite abrir o painel e entender o sistema mesmo
        # sem chave de API e sem acesso à Bitget.
        if usar_sintetico is None:
            usar_sintetico = os.environ.get(
                "INVESTAI_SYNTHETIC", "").strip().lower() in {"1", "true", "sim"}
        self.usar_sintetico = usar_sintetico
        self.provider = self._montar_provider()
        self.hub = DataHub(self.provider)
        self.screener = Screener(self.hub, self.settings)

        # ------------------------------------------------- camadas novas
        self.registry = registry_padrao(
            sintetico=usar_sintetico,
            bitget_ok=self._checar_bitget,
            brapi_ok=lambda: bool(os.environ.get("BRAPI_TOKEN", "").strip()))
        self.registry.revalidar()

        self.risk = RiskManager(self.settings.risk,
                                self.settings.exec.capital_inicial_usd)
        self.risk_engine = RiskEngine(
            self.settings.risk, self.settings.exec.capital_inicial_usd,
            manager=self.risk)
        self.chief = ChiefInvestmentEngine()
        self.alertas = CentralDeAlertas()
        self.journal = Journal()
        self.strategies = StrategyRegistry()
        self.monitor = monitor_padrao(
            checar_dados=self._checar_dados,
            checar_exchange=self._checar_exchange,
            checar_banco=self._checar_banco,
            checar_risco=lambda: not self.risk_engine.halted,
            checar_noticias=lambda: False)
        # A guarda de fase compartilha o registro de estratégias com o
        # pipeline de validação: promover uma estratégia lá muda o que a
        # guarda autoriza aqui, sem nenhuma sincronização manual.
        # Parada de emergência: gravada no banco, lida pela guarda em cada
        # ordem. Sobrevive a reinício de propósito — uma parada que se perde
        # no restart é uma pausa, não uma parada.
        self.trava = TravaOperacao(self.store)
        self.guarda = GuardaFase(
            self.strategies,
            capital_usd=self.settings.exec.capital_inicial_usd,
            trava=self._ler_trava)
        # Controle de idempotência ligado ao banco: sobrevive a reinício do
        # processo, que é exatamente o caso que ele existe para resolver.
        self.idempotencia = ControleIdempotencia(self.store, self.bitget)
        self.executor = Executor(self.settings.exec, backend=self.bitget,
                                 modo="paper", guarda=self.guarda,
                                 idempotencia=self.idempotencia)
        self.engine = TradingEngine(self.settings, self.screener, self.executor,
                                    self.risk, self.store)
        self.orquestrador = Orquestrador(
            self.settings, self.hub, self.risk_engine,
            registry=self.registry, chief=self.chief, monitor=self.monitor,
            alertas=self.alertas, journal=self.journal,
            relogio=self._relogio_dados)
        self.ultimo_ciclo = None

        # Reconciliação de subida: intenções de ordem que ficaram com destino
        # desconhecido em uma execução anterior. Roda aqui, antes de o motor
        # existir, porque o resultado pode ser "há posição real aberta que
        # este processo não conhece" — e isso precisa estar na tela antes de
        # qualquer ordem nova.
        self.reconciliacao_subida = self.idempotencia.reconciliar_pendentes()
        if self.reconciliacao_subida.exige_atencao:
            self.store.registrar_evento(
                "ALERTA", "idempotencia",
                "ordens pendentes encontradas na subida",
                self.reconciliacao_subida.to_dict())

        # Shadow mode: registra em disco a decisão que o sistema tomaria e,
        # nos ciclos seguintes, confere o que o mercado fez com ela. Não
        # envia ordem — é a medida honesta de "estas decisões dão dinheiro?"
        # antes de arriscar capital. Ligado por padrão; desligue com
        # INVESTAI_SHADOW=0.
        self.shadow = ShadowLive(
            self.store, self.hub,
            timeframe=self.settings.signal.timeframe_principal)
        self.shadow_ligado = os.environ.get(
            "INVESTAI_SHADOW", "1").strip().lower() not in {"0", "false", "nao", "não"}

        self.fii_provider = provider_padrao(
            self.settings.data_dir,
            token_brapi=os.environ.get("BRAPI_TOKEN", ""),
            usar_rede=not usar_sintetico)

        if self.credenciais is not None:
            self.conectar(self.credenciais)

    # ------------------------------------------------- checagens de saúde
    def _relogio_dados(self) -> int:
        """Instante de referência para medir a IDADE dos dados.

        Com dados reais é o relógio da máquina. Em modo sintético é o instante
        do próprio gerador da série: medir uma série simulada contra o relógio
        real reprovaria por "dados desatualizados" um histórico íntegro, e o
        painel inteiro cairia em `dados_insuficientes` sem que nada estivesse
        de fato errado.
        """
        if self.usar_sintetico:
            agora = getattr(self.hub.provider, "agora_ms", 0)
            if agora:
                return int(agora)
        return int(time.time() * 1000)

    def _checar_dados(self) -> bool:
        try:
            velas = self.hub.candles(self.settings.universo[0], "1H", limit=5)
            return bool(velas)
        except Exception:                               # noqa: BLE001
            return False

    def _checar_bitget(self) -> bool:
        if self.usar_sintetico or self.bitget is None:
            return False
        try:
            return self.bitget.server_time_ms() > 0
        except Exception:                               # noqa: BLE001
            return False

    def _checar_exchange(self) -> bool:
        # Em modo simulado a exchange não é crítica: o simulador substitui.
        if self.usar_sintetico:
            return True
        return self._checar_bitget()

    def _checar_banco(self) -> bool:
        try:
            self.store.get_estado("__healthcheck__", None)
            return True
        except Exception:                               # noqa: BLE001
            return False

    def _montar_provider(self) -> Any:
        if self.usar_sintetico:
            log.warning("usando provider SINTÉTICO — dados simulados, não de mercado")
            return SyntheticProvider()
        cliente = BitgetClient(self.credenciais,
                               product_type=self.settings.exec.product_type,
                               margin_coin=self.settings.exec.margin_coin)
        self.bitget = cliente
        return cliente

    def _ler_trava(self) -> tuple[bool, str]:
        """Estado da parada de emergência, no formato que a guarda espera."""
        t = self.trava.ler()
        return t.ativa, t.motivo

    def conectar(self, cred: ApiCredentials) -> dict[str, Any]:
        """Anexa credenciais ao cliente e valida contra a exchange."""
        self.credenciais = cred
        if self.usar_sintetico:
            # `ok` diz que a requisição foi aceita; `validada` diz que a chave
            # foi conferida CONTRA A EXCHANGE. São coisas diferentes, e em
            # modo sintético só a primeira é verdadeira. Colapsar as duas faria
            # o painel anunciar "chave validada" para uma chave digitada
            # errado, e o erro só apareceria na primeira ordem real.
            self.erro_conexao = ""
            return {"ok": True, "validada": False, "api_key": cred.mascara(),
                    "aviso": "modo sintético ativo: credencial armazenada mas "
                             "NÃO verificada contra a Bitget, e nenhuma ordem "
                             "real será enviada"}
        if self.bitget is None:
            self.bitget = BitgetClient(
                cred, product_type=self.settings.exec.product_type,
                margin_coin=self.settings.exec.margin_coin)
        else:
            self.bitget.cred = cred
        self.executor.backend = self.bitget
        # A consulta de ordem por clientOid precisa da mesma conexão
        # autenticada; sem isto, a idempotência ficaria cega justamente
        # depois de o usuário conectar a chave.
        self.idempotencia.backend = self.bitget
        diag = self.bitget.verificar_credenciais()
        # Aqui a chave foi de fato consultada na exchange, então `validada`
        # acompanha `ok`.
        diag["validada"] = bool(diag.get("ok"))
        self.erro_conexao = "" if diag.get("ok") else str(diag.get("erro", ""))
        if diag.get("ok"):
            self.risk.sincronizar_capital(float(diag.get("saldo_usdt", 0.0)))
        return diag

    def desconectar(self) -> None:
        self.credenciais = None
        if self.bitget is not None:
            self.bitget.cred = None
        self.engine.desarmar_live()

    def status_conexao(self) -> dict[str, Any]:
        return {
            "conectado": self.credenciais is not None,
            "api_key": self.credenciais.mascara() if self.credenciais else None,
            "keystore_existe": self.keystore.existe,
            "erro": self.erro_conexao,
            "modo_dados": "sintetico" if self.usar_sintetico else "bitget",
            "permissao_saque_necessaria": False,
            "instrucao": (
                "Crie a chave em Bitget > API Management com permissões de "
                "leitura e de trade em futuros. NÃO habilite saque nem "
                "transferência. A senha da sua conta nunca é usada aqui."
            ),
        }


def criar_app(state: AppState | None = None) -> FastAPI:
    st = state or AppState()
    app = FastAPI(
        title="InvestAI — Análise de Oportunidades",
        description=AVISO_PADRAO,
        version="1.0.0",
    )
    app.state.investai = st

    if st.token_gerado:
        log.warning("INVESTAI_API_TOKEN não definido; token desta sessão: %s",
                    st.token)

    def exigir_token(x_api_token: str = Header(default="")) -> None:
        """Compara em tempo constante para não vazar o token por timing."""
        if not hmac.compare_digest(x_api_token or "", st.token):
            raise HTTPException(401, "token inválido; envie o cabeçalho X-API-Token")

    protegido = [Depends(exigir_token)]

    # ------------------------------------------------------------- informação
    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        return {
            "config": st.settings.to_dict(),
            "conexao": st.status_conexao(),
            "aviso": AVISO_PADRAO,
            "confirmacao_live": CONFIRMACAO_LIVE,
            "token_gerado_automaticamente": st.token_gerado,
        }

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "ts": int(time.time() * 1000),
                "modo_dados": "sintetico" if st.usar_sintetico else "bitget",
                "motor_rodando": st.engine.estado.rodando}

    # ------------------------------------------------------------------ cripto
    @app.get("/api/scan")
    def scan(symbols: str = Query(default="", description="lista separada por vírgula"),
             com_historico: bool = Query(default=True)) -> dict[str, Any]:
        alvos = [s.strip().upper() for s in symbols.split(",") if s.strip()] or None
        resultado = st.screener.scan(alvos, com_historico=com_historico)
        for sinal in resultado.operaveis:
            st.store.salvar_sinal(sinal)
        payload = resultado.to_dict()
        payload["aviso"] = AVISO_PADRAO
        return payload

    @app.get("/api/analise/{symbol}")
    def analise(symbol: str) -> dict[str, Any]:
        a = st.screener.analisar(symbol.upper())
        if a.erro:
            raise HTTPException(404, a.erro)
        if not a.features:
            raise HTTPException(404, f"sem dados para {symbol.upper()}")
        return {"analise": a.to_dict(), "aviso": AVISO_PADRAO}

    @app.get("/api/backtest/{symbol}")
    def backtest(symbol: str,
                 timeframe: str = Query(default=""),
                 lado: str = Query(default="ambos", pattern="^(long|short|ambos)$"),
                 barras: int = Query(default=3000, ge=400, le=10000)) -> dict[str, Any]:
        tf = timeframe or st.settings.signal.timeframe_principal
        try:
            velas = st.hub.historico(symbol.upper(), tf, barras=barras)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except ExchangeError as exc:
            raise HTTPException(404, str(exc)) from exc
        if len(velas) < 300:
            raise HTTPException(
                422, f"histórico insuficiente para {symbol} {tf}: "
                     f"{len(velas)} candles")
        snap = st.hub.ticker(symbol.upper())
        filtro = None if lado == "ambos" else Side(lado)
        try:
            resultado = rodar_backtest(
                symbol.upper(), tf, velas, st.settings.signal, st.settings.exec,
                side_filtro=filtro, snapshot=snap,
                funding_rate=snap.funding_rate if snap else 0.0001)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        payload = resultado.to_dict()
        payload["premissas"] = {
            "execucao": "sinal no fechamento da barra, entrada na abertura seguinte",
            "empate_stop_alvo": "assume stop primeiro (pessimista)",
            "taxa_taker_pct": st.settings.exec.taxa_taker_pct,
            "slippage_pct": st.settings.exec.slippage_pct,
            "funding": "debitado a cada 8h de posição aberta",
            "risco_por_trade": "1% do capital simulado",
        }
        payload["aviso"] = (
            "Resultado passado em dados históricos. Não indica resultado "
            "futuro. Amostra pequena (< 30 operações) tem pouco valor "
            "estatístico."
        )
        return payload

    # -------------------------------------------------------------------- FIIs
    @app.get("/api/fiis")
    def fiis(tickers: str = Query(default="")) -> dict[str, Any]:
        alvos = [t.strip().upper() for t in tickers.split(",") if t.strip()] or None
        fundos = st.fii_provider.fundos(alvos)
        if not fundos:
            raise HTTPException(
                503, "nenhum dado de FII disponível; verifique "
                     f"{st.settings.data_dir}/fiis_snapshot.json")
        ranking = ranquear(fundos)
        base = getattr(st.fii_provider, "base_provider", st.fii_provider)
        meta = getattr(base, "metadados", {})
        return {
            "fonte": meta,
            "total": len(ranking),
            "fundos": [f.to_dict() for f in ranking],
            "aviso": (
                "Ranking técnico com base nos indicadores informados. Não é "
                "recomendação de investimento. Rendimento de FII não é fixo e "
                "pode ser reduzido ou suspenso. Confira os fundamentais no "
                "relatório gerencial do fundo antes de aportar."
            ),
        }

    @app.post("/api/fiis/carteira")
    def carteira(req: CarteiraFii) -> dict[str, Any]:
        fundos = st.fii_provider.fundos()
        if not fundos:
            raise HTTPException(503, "nenhum dado de FII disponível")
        return carteira_sugerida(
            fundos, req.capital, max_por_fundo_pct=req.max_por_fundo_pct,
            min_score=req.min_score, max_fundos=req.max_fundos)

    # ------------------------------------------------------------- histórico
    @app.get("/api/sinais")
    def sinais(limite: int = Query(default=50, ge=1, le=500),
               symbol: str = Query(default=""),
               apenas_operaveis: bool = Query(default=False)) -> dict[str, Any]:
        return {"sinais": st.store.sinais_recentes(
            limite, symbol.upper() or None, apenas_operaveis)}

    @app.get("/api/trades")
    def trades(limite: int = Query(default=100, ge=1, le=1000),
               modo: str = Query(default="")) -> dict[str, Any]:
        return {
            "trades": st.store.trades(limite, modo or None),
            "resumo": st.store.resumo_trades(modo or None),
            "curva_capital": st.store.curva_capital(
                st.settings.exec.capital_inicial_usd, modo or None),
        }

    @app.get("/api/eventos")
    def eventos(limite: int = Query(default=100, ge=1, le=500),
                nivel: str = Query(default="")) -> dict[str, Any]:
        return {"eventos": st.store.eventos(limite, nivel or None)}

    # ---------------------------------------------------------------- conexão
    @app.get("/api/bitget/status")
    def bitget_status() -> dict[str, Any]:
        base = st.status_conexao()
        if st.credenciais and st.bitget and not st.usar_sintetico:
            base["diagnostico"] = st.bitget.verificar_credenciais()
        return base

    @app.post("/api/bitget/conectar", dependencies=protegido)
    def bitget_conectar(req: ConectarBitget) -> dict[str, Any]:
        cred = ApiCredentials(req.api_key.strip(), req.api_secret.strip(),
                              req.passphrase.strip())
        diag = st.conectar(cred)
        salvo = False
        if req.salvar:
            try:
                st.keystore.salvar(cred, req.senha_mestra)
                salvo = True
            except CredentialError as exc:
                raise HTTPException(400, str(exc)) from exc
        st.store.registrar_evento(
            "INFO", "api", "credencial Bitget conectada",
            {"api_key": cred.mascara(), "salvo_em_keystore": salvo,
             "validacao_ok": bool(diag.get("ok"))})
        return {"conexao": diag, "salvo_em_keystore": salvo,
                "keystore": str(st.keystore.caminho),
                "aviso": "Chave cifrada em disco com a senha mestra. Ela não é "
                         "recuperável sem essa senha, e o segredo nunca é "
                         "devolvido por esta API."}

    @app.post("/api/bitget/destravar", dependencies=protegido)
    def bitget_destravar(req: Destravar) -> dict[str, Any]:
        try:
            cred = st.keystore.carregar(req.senha_mestra)
        except CredentialError as exc:
            raise HTTPException(401, str(exc)) from exc
        diag = st.conectar(cred)
        return {"conexao": diag, "api_key": cred.mascara()}

    @app.delete("/api/bitget/credenciais", dependencies=protegido)
    def bitget_apagar() -> dict[str, Any]:
        apagado = st.keystore.apagar()
        st.desconectar()
        st.store.registrar_evento("ALERTA", "api", "credenciais removidas")
        return {"keystore_apagado": apagado, "conexao": st.status_conexao()}

    # ------------------------------------------------------------------ motor
    @app.get("/api/status")
    def status() -> dict[str, Any]:
        s = st.engine.status()
        s["conexao"] = st.status_conexao()
        s["aviso"] = AVISO_PADRAO
        return s

    @app.post("/api/motor/iniciar", dependencies=protegido)
    def motor_iniciar() -> dict[str, Any]:
        return {"mensagem": st.engine.iniciar(), "status": st.engine.status()}

    @app.post("/api/motor/parar", dependencies=protegido)
    def motor_parar() -> dict[str, Any]:
        return {"mensagem": st.engine.parar(), "status": st.engine.status()}

    @app.post("/api/motor/ciclo", dependencies=protegido)
    def motor_ciclo() -> dict[str, Any]:
        """Executa um único ciclo — útil para testar sem deixar o loop ligado."""
        antes = st.engine.estado.ultimo_scan_ms
        resultado = st.engine.ciclo()
        return {"resultado": resultado, "status": st.engine.status(),
                "mensagem": _resumo_do_ciclo(resultado, antes)}

    @app.post("/api/motor/armar-live", dependencies=protegido)
    def motor_armar(req: ArmarLive) -> dict[str, Any]:
        ok, mensagem = st.engine.armar_live(req.confirmacao)
        if not ok:
            raise HTTPException(400, mensagem)
        return {"mensagem": mensagem, "status": st.engine.status()}

    @app.post("/api/motor/desarmar-live", dependencies=protegido)
    def motor_desarmar() -> dict[str, Any]:
        return {"mensagem": st.engine.desarmar_live(), "status": st.engine.status()}

    @app.post("/api/motor/fechar-tudo", dependencies=protegido)
    def motor_fechar_tudo() -> dict[str, Any]:
        return {"resultados": st.engine.fechar_tudo("botão de pânico"),
                "status": st.engine.status()}

    @app.post("/api/motor/fechar", dependencies=protegido)
    def motor_fechar(req: FecharPosicao) -> dict[str, Any]:
        snap = st.hub.ticker(req.symbol.upper())
        if snap is None:
            raise HTTPException(503, f"preço de {req.symbol} indisponível")
        res, trade = st.executor.fechar(req.symbol.upper(), snap.last_price,
                                        "manual", fracao=req.fracao)
        if not res.ok:
            raise HTTPException(400, res.mensagem)
        if trade is not None:
            st.store.salvar_trade(trade, modo=st.executor.modo)
            st.risk.registrar_trade(trade)
        if req.fracao >= 1.0:
            st.store.remover_posicao(req.symbol.upper())
        return {"mensagem": res.mensagem,
                "trade": trade.to_dict() if trade else None}

    # ------------------------------------------------------- guarda de fase
    @app.get("/api/guarda")
    def guarda_estado() -> dict[str, Any]:
        """O que a guarda de fase autoriza neste instante, e por quê."""
        return {
            "guarda": st.guarda.estado(),
            "propostas_pendentes": st.engine.propostas_pendentes(),
            "estrategias": [v.to_dict() for v in st.strategies.listar()],
            "aviso": ("Nenhuma ordem real sai sem uma versão de estratégia "
                      "vinculada e em fase assistido ou real_limitado. Sem "
                      "vínculo, o modo real não arma."),
        }

    @app.post("/api/guarda/vincular", dependencies=protegido)
    def guarda_vincular(req: VincularEstrategia) -> dict[str, Any]:
        try:
            st.guarda.vincular(req.chave)
        except Exception as exc:                        # noqa: BLE001
            raise HTTPException(400, str(exc)) from exc
        st.store.registrar_evento(
            "ALERTA", "guarda", f"estratégia {req.chave} vinculada ao motor",
            {"estado": st.guarda.estado()})
        return {"mensagem": f"{req.chave} vinculada ao motor",
                "guarda": st.guarda.estado()}

    @app.post("/api/guarda/desvincular", dependencies=protegido)
    def guarda_desvincular() -> dict[str, Any]:
        st.guarda.desvincular()
        st.store.registrar_evento("INFO", "guarda", "estratégia desvinculada")
        return {"mensagem": "nenhuma estratégia vinculada; o modo real não arma",
                "guarda": st.guarda.estado()}

    @app.post("/api/guarda/confirmar", dependencies=protegido)
    def guarda_confirmar(req: ConfirmarProposta) -> dict[str, Any]:
        """Confirma UMA ordem do modo assistido. Não vale para a próxima."""
        res = st.engine.confirmar_proposta(req.client_oid)
        if not res.get("ok"):
            raise HTTPException(400, res.get("motivo", "confirmação recusada"))
        return {"mensagem": res.get("motivo", ""), "resultado": res,
                "status": st.engine.status()}

    @app.post("/api/guarda/recusar", dependencies=protegido)
    def guarda_recusar(req: ConfirmarProposta) -> dict[str, Any]:
        res = st.engine.recusar_proposta(
            req.client_oid, req.motivo or "recusada pelo operador")
        if not res.get("ok"):
            raise HTTPException(400, res.get("motivo", "proposta inexistente"))
        return {"mensagem": res.get("motivo", ""), "resultado": res}

    # ------------------------------------------------- comandos operacionais
    @app.get("/api/diagnostico")
    def diagnostico_endpoint() -> dict[str, Any]:
        """Confere o sistema inteiro e diz o que cada falha impede."""
        d = diagnostico(st)
        return {**d.to_dict(), "texto": d.texto()}

    @app.get("/api/operacao/status")
    def status_operacional_endpoint() -> dict[str, Any]:
        s_op = status_operacional(st)
        return {**s_op, "texto": status_texto_fn(s_op)}

    @app.post("/api/operacao/parada-emergencia", dependencies=protegido)
    def parada_emergencia_endpoint(req: ParadaEmergencia) -> dict[str, Any]:
        """Para tudo e trava novos envios. A trava sobrevive a reinício."""
        return parada_emergencia(st, req.motivo,
                                 fechar_posicoes=req.fechar_posicoes)

    @app.post("/api/operacao/liberar-trava", dependencies=protegido)
    def liberar_trava_endpoint(req: LiberarTrava) -> dict[str, Any]:
        if req.confirmacao.strip().upper() != CONFIRMACAO_LIBERAR:
            raise HTTPException(
                400, f"envie exatamente '{CONFIRMACAO_LIBERAR}' para liberar "
                     f"a trava de operação")
        return liberar_trava(st)

    # ------------------------------------------------------- idempotência
    @app.get("/api/envios")
    def envios_estado() -> dict[str, Any]:
        """Intenções de ordem e o que se sabe sobre o destino de cada uma."""
        return {
            "idempotencia": st.idempotencia.estado(),
            "reconciliacao_subida": st.reconciliacao_subida.to_dict(),
            "aviso": ("Uma intenção 'pendente' é uma ordem cujo destino este "
                      "sistema não conseguiu estabelecer. Enquanto estiver "
                      "pendente, nenhuma ordem com o mesmo clientOid será "
                      "reenviada."),
        }

    @app.post("/api/envios/reconciliar", dependencies=protegido)
    def envios_reconciliar() -> dict[str, Any]:
        """Pergunta à corretora o que houve com cada intenção pendente."""
        rel = st.idempotencia.reconciliar_pendentes()
        st.reconciliacao_subida = rel
        if rel.exige_atencao:
            st.store.registrar_evento(
                "ALERTA", "idempotencia", "reconciliação manual com pendências",
                rel.to_dict())
        return {"resultado": rel.to_dict(),
                "mensagem": (
                    f"{rel.conferidas} intenção(ões) conferida(s); "
                    f"{len(rel.adotadas)} já estava(m) na corretora, "
                    f"{len(rel.ausentes)} não chegou(aram), "
                    f"{len(rel.indeterminadas)} sem resposta")}

    @app.post("/api/risco/rearmar", dependencies=protegido)
    def risco_rearmar() -> dict[str, Any]:
        st.risk.liberar_kill_switch()
        st.store.registrar_evento("ALERTA", "api", "kill switch rearmado manualmente")
        return {"mensagem": "kill switch rearmado", "risco": st.risk.estado.to_dict()}


    def _resumo_do_ciclo(resultado: dict[str, Any], ultimo_scan_antes: int) -> str:
        """Diz em uma linha o que o ciclo fez — inclusive quando não fez nada.

        Sem isto o painel respondia "ok" a qualquer ciclo, e "ok" some com a
        diferença entre "varri o mercado e nada passou" e "nem varri, porque o
        intervalo ainda não venceu". Quem clicou fica achando que houve
        varredura. Um botão de operação precisa dizer o que aconteceu.
        """
        partes: list[str] = []

        gestao = resultado.get("gestao") or []
        if gestao:
            partes.append(f"{len(gestao)} evento(s) de gestão de posição")

        if not resultado.get("scan"):
            intervalo = st.settings.exec.intervalo_scan_segundos
            falta = max(0, intervalo - (int(time.time() * 1000)
                                        - ultimo_scan_antes) // 1000)
            partes.append(
                f"SEM VARREDURA neste ciclo: o intervalo de {intervalo}s ainda "
                f"não venceu (faltam ~{falta}s). Só a gestão das posições "
                f"abertas rodou")
            return "; ".join(partes) + "."

        entradas = resultado.get("entradas") or []
        abertas = [e for e in entradas if e.get("ok")]
        bloqueadas = [e for e in entradas if not e.get("ok")]
        partes.append("mercado varrido")
        if abertas:
            partes.append(f"{len(abertas)} posição(ões) aberta(s): "
                          + ", ".join(e["symbol"] for e in abertas))
        if bloqueadas:
            partes.append(f"{len(bloqueadas)} entrada(s) barrada(s) pelo risco: "
                          + "; ".join(f"{e['symbol']} ({e.get('motivo', '')})"
                                      for e in bloqueadas[:3]))
        if not abertas and not bloqueadas:
            partes.append("NENHUMA oportunidade atendeu aos critérios — "
                          "capital preservado")
        return "; ".join(partes) + "."

    def _candles_ou_404(symbol: str, *, limit: int) -> list[Any]:
        """Par desconhecido é 404, não 500.

        Um erro 500 aqui faria o painel exibir "erro interno" para o que é
        apenas um símbolo digitado errado — e esconderia falhas reais do
        provedor no mesmo código de status.
        """
        try:
            return st.hub.candles(symbol.upper(), "1H", limit=limit)
        except (ExchangeError, ValueError) as exc:
            raise HTTPException(404, f"{symbol.upper()}: {exc}") from exc

    # ================================================= ciclo completo
    @app.get("/api/ciclo")
    def ciclo_analise(symbols: str = Query(default=""),
                      com_portfolio: bool = Query(default=True)
                      ) -> dict[str, Any]:
        """Ciclo completo: qualidade → regime → anomalias → agentes →
        consenso → Risk Engine. Cada parada registra o motivo."""
        alvos = [x.strip().upper() for x in symbols.split(",") if x.strip()] \
            or None
        posicoes = st.executor.posicoes() if com_portfolio else []
        resultado = st.orquestrador.ciclo(alvos, posicoes=posicoes)
        st.ultimo_ciclo = resultado
        payload = resultado.to_dict()
        payload["aviso"] = AVISO_PADRAO

        if st.shadow_ligado:
            # A ordem importa: liquidar ANTES de registrar evita conferir uma
            # decisão contra a própria vela que a originou.
            fechadas = st.shadow.liquidar_pendentes()
            novas = st.shadow.registrar_ciclo(resultado.analises)
            payload["shadow"] = {"registradas": len(novas),
                                 "liquidadas": fechadas}
            if novas:
                st.store.registrar_evento(
                    "INFO", "shadow",
                    f"{len(novas)} decisão(ões) registrada(s) em shadow mode",
                    {"ids": novas})
        return payload

    @app.get("/api/analise-completa/{symbol}")
    def analise_completa(symbol: str) -> dict[str, Any]:
        a = st.orquestrador.analisar(symbol.upper(),
                                     posicoes=st.executor.posicoes())
        if a.erro and a.etapa_final in ("normalizacao", "coleta"):
            raise HTTPException(404, a.erro)
        return {"analise": a.to_dict(), "aviso": AVISO_PADRAO}

    @app.post("/api/estatistica", dependencies=protegido)
    def registrar_estatistica(req: RegistrarEstatistica) -> dict[str, Any]:
        """Alimenta a estatística medida de um par/direção.

        Sem ela o agente quantitativo se abstém e nada pode ser
        'validado pelo modelo' — que é o comportamento correto."""
        ev = st.orquestrador.registrar_estatistica(
            req.symbol.upper(), Side(req.side), req.retornos_r)
        return {"symbol": req.symbol.upper(), "side": req.side,
                "ev": ev.to_dict()}

    @app.get("/api/relatorio-diario")
    def relatorio_diario() -> dict[str, Any]:
        ciclo = st.ultimo_ciclo
        if ciclo is None:
            ciclo = st.orquestrador.ciclo(posicoes=st.executor.posicoes())
            st.ultimo_ciclo = ciclo
        resumo_paper = st.store.resumo_trades("paper")
        resumo_live = st.store.resumo_trades("live")
        rel = st.orquestrador.relatorio_diario(
            ciclo,
            desempenho=[
                BlocoDesempenho(
                    "paper", resumo_paper["trades"], resumo_paper["win_rate"],
                    resumo_paper["pnl_usd"], resumo_paper["expectancy_r"],
                    resumo_paper["profit_factor"], resumo_paper["taxas_usd"],
                    st.risk.estado.drawdown_pct,
                    st.risk.estado.capital_atual),
                BlocoDesempenho(
                    "live", resumo_live["trades"], resumo_live["win_rate"],
                    resumo_live["pnl_usd"], resumo_live["expectancy_r"],
                    resumo_live["profit_factor"], resumo_live["taxas_usd"]),
            ],
            estrategias_por_fase=st.strategies.por_fase(),
            modo_de_dados="sintetico" if st.usar_sintetico else "bitget")
        return {"relatorio": rel.to_dict(), "texto": rel.para_texto()}

    # ================================================ dados e cobertura
    @app.get("/api/dados/cobertura")
    def cobertura_dados() -> dict[str, Any]:
        """O que o sistema sabe e o que NÃO sabe, declarado."""
        st.registry.revalidar()
        return {
            "resumo": st.registry.resumo(),
            "provedores": [p.to_dict() for p in st.registry.provedores()],
            "matriz": st.registry.matriz_cobertura(),
            "observacao": "combinação sem provedor conectado devolve FONTE "
                          "NÃO CONFIGURADA; a interface nunca preenche o "
                          "espaço com número plausível",
        }

    @app.get("/api/shadow")
    def shadow() -> dict[str, Any]:
        """O que as decisões tomadas ao vivo produziram, até agora."""
        resumo = st.shadow.resumo().to_dict()
        return {
            "ligado": st.shadow_ligado,
            "resumo": resumo,
            "decisoes": st.shadow.store.decisoes_shadow(limite=200),
            "minimos": {"decisoes": 40, "dias": 21},
            "aviso": AVISO_PADRAO,
        }

    @app.post("/api/shadow/liquidar", dependencies=protegido)
    def shadow_liquidar() -> dict[str, Any]:
        """Força a conferência das decisões pendentes contra o mercado."""
        n = st.shadow.liquidar_pendentes()
        return {"liquidadas": n, "resumo": st.shadow.resumo().to_dict()}

    @app.get("/api/saude")
    def saude() -> dict[str, Any]:
        return st.monitor.checar_tudo().to_dict()

    @app.get("/api/regime/{symbol}")
    def regime(symbol: str) -> dict[str, Any]:
        from .ops import detectar_regime
        velas = _candles_ou_404(symbol, limit=500)
        if len(velas) < 250:
            raise HTTPException(422, f"histórico insuficiente para "
                                     f"{symbol.upper()}")
        return detectar_regime(velas).to_dict()

    @app.get("/api/anomalias/{symbol}")
    def anomalias(symbol: str) -> dict[str, Any]:
        from .ops import RelatorioAnomalias, detectar_anomalias
        velas = _candles_ou_404(symbol, limit=200)
        rel = RelatorioAnomalias(symbol.upper(),
                                 detectar_anomalias(symbol.upper(), velas))
        return rel.to_dict()

    # ==================================================== validação
    @app.post("/api/validacao/estrategia", dependencies=protegido)
    def validar_estrategia(req: ValidarEstrategia) -> dict[str, Any]:
        """Roda walk-forward + Monte Carlo + overfitting e aplica os gates."""
        try:
            velas = st.hub.historico(req.symbol.upper(), req.timeframe,
                                     barras=req.barras)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if len(velas) < 1000:
            raise HTTPException(
                422, f"histórico insuficiente: {len(velas)} candles")

        lado = Side(req.lado)

        def runner(janela, params):
            r = rodar_backtest(
                req.symbol.upper(), req.timeframe, janela,
                st.settings.signal, st.settings.exec, side_filtro=lado,
                score_minimo=params.get("score_minimo"))
            return r.stats, r.trades

        params = {"score_minimo": req.score_minimo,
                  "atr_mult_stop": st.settings.signal.atr_mult_stop}
        rel = rodar_pipeline_completo(
            st.strategies, req.strategy_id, params, velas, runner,
            timeframe=req.timeframe, n_ciclos=req.n_ciclos,
            capital=st.settings.exec.capital_inicial_usd,
            risco_por_trade_frac=st.settings.risk.risco_por_trade_pct / 100.0)

        # A estatística medida alimenta o agente quantitativo.
        if rel.walk_forward and rel.walk_forward.trades_oos:
            st.orquestrador.registrar_estatistica(
                req.symbol.upper(), lado,
                [t.pnl_r for t in rel.walk_forward.trades_oos])

        st.store.registrar_evento(
            "INFO", "validacao",
            f"validação de {rel.chave_estrategia}: fase {rel.fase_final}",
            {"promovida": rel.promovida,
             "gate": rel.gate.to_dict() if rel.gate else None})
        return rel.to_dict()

    @app.post("/api/validacao/monte-carlo")
    def validacao_monte_carlo(req: MonteCarloRequest) -> dict[str, Any]:
        return monte_carlo(
            req.retornos_r, n_simulacoes=req.n_simulacoes,
            risco_por_trade_frac=req.risco_por_trade_frac,
            modo=req.modo).to_dict()

    @app.post("/api/validacao/comparar-modos")
    def validacao_comparar(req: MonteCarloRequest) -> dict[str, Any]:
        """IID contra blocos: mostra o quanto assumir independência engana."""
        return comparar_modos(
            req.retornos_r, n_simulacoes=req.n_simulacoes,
            risco_por_trade_frac=req.risco_por_trade_frac)

    # ==================================================== estratégias
    @app.get("/api/estrategias")
    def estrategias() -> dict[str, Any]:
        return st.strategies.to_dict()

    @app.get("/api/estrategias/criterios")
    def criterios_promocao() -> dict[str, Any]:
        return {"criterios": CriteriosPromocao().to_dict(),
                "fases": [f.value for f in Fase],
                "observacao": "as fases que dependem de execução ao vivo "
                              "(paper, shadow, assistido) NÃO podem ser "
                              "vencidas por backtest"}

    # ====================================================== portfólio
    @app.get("/api/portfolio")
    def portfolio() -> dict[str, Any]:
        posicoes = st.executor.posicoes()
        if not posicoes:
            return {"n_posicoes": 0,
                    "mensagem": "nenhuma posição aberta",
                    "cenarios_disponiveis": [c.nome
                                             for c in CENARIOS_PADRAO]}
        series = {p.symbol: st.hub.candles(p.symbol, "1H", limit=500)
                  for p in posicoes}
        matriz = matriz_correlacao(series, janela=400)
        return {
            "diversificacao": analisar_diversificacao(posicoes,
                                                      matriz).to_dict(),
            "correlacao": matriz.to_dict(),
        }

    @app.post("/api/portfolio/stress")
    def portfolio_stress(req: StressRequest) -> dict[str, Any]:
        posicoes = st.executor.posicoes()
        if not posicoes:
            raise HTTPException(422, "nenhuma posição aberta para estressar")
        return rodar_stress_test(posicoes, req.capital).to_dict()

    # ===================================================== multiativos
    @app.post("/api/ativos/acao")
    def ativo_acao(req: AnalisarAcao) -> dict[str, Any]:
        cov = st.registry.cobertura(AssetClass.ACAO, DataKind.FUNDAMENTOS)
        r = analisar_acao(req.ticker, req.fundamentos,
                          fonte=", ".join(cov.provedores) or cov.mensagem)
        return {"analise": r.to_dict(), "cobertura": cov.to_dict()}

    @app.post("/api/ativos/etf")
    def ativo_etf(req: AnalisarEtf) -> dict[str, Any]:
        return {"analise": analisar_etf(req.ticker, req.dados).to_dict()}

    @app.post("/api/ativos/renda-fixa")
    def ativo_renda_fixa(req: CompararRendaFixa) -> dict[str, Any]:
        try:
            titulos = [
                Titulo(nome=t.nome, tipo=TipoRendaFixa(t.tipo),
                       indexador=Indexador(t.indexador), taxa=t.taxa,
                       prazo_dias=t.prazo_dias, emissor=t.emissor,
                       risco_credito=t.risco_credito,
                       liquidez_diaria=t.liquidez_diaria,
                       marcacao_a_mercado=t.marcacao_a_mercado)
                for t in req.titulos
            ]
        except ValueError as exc:
            raise HTTPException(422, {
                "erro": f"tipo ou indexador inválido: {exc}",
                "tipos_validos": [t.value for t in TipoRendaFixa],
                "indexadores_validos": [i.value for i in Indexador],
            })
        macro = CenarioMacro(cdi_aa=req.cdi_aa, selic_aa=req.selic_aa,
                             ipca_aa=req.ipca_aa)
        return comparar_titulos(titulos, macro,
                                valor_aplicado=req.valor_aplicado)

    @app.post("/api/ativos/comparar")
    def ativos_comparar(req: CompararClasses) -> dict[str, Any]:
        try:
            cands = [
                CandidatoComparacao(
                    identificador=c.identificador,
                    classe=AssetClass(c.classe),
                    retorno_nominal_aa=c.retorno_nominal_aa,
                    incerteza_retorno=c.incerteza_retorno,
                    volatilidade_aa=c.volatilidade_aa,
                    drawdown_plausivel_pct=c.drawdown_plausivel_pct,
                    aliquota_ir=c.aliquota_ir, isento_ir=c.isento_ir,
                    liquidez_dias=c.liquidez_dias,
                    horizonte_minimo_meses=c.horizonte_minimo_meses,
                    risco_credito=c.risco_credito, garantia=c.garantia)
                for c in req.candidatos
            ]
        except ValueError as exc:
            raise HTTPException(422, {
                "erro": f"classe de ativo inválida: {exc}",
                "classes_validas": [c.value for c in AssetClass
                                    if c is not AssetClass.DESCONHECIDO],
            })
        perfil = PerfilInvestidor(
            horizonte_meses=req.horizonte_meses,
            tolerancia_drawdown_pct=req.tolerancia_drawdown_pct,
            necessidade_liquidez_dias=req.necessidade_liquidez_dias,
            objetivo=req.objetivo)
        return comparar(cands, inflacao_aa=req.inflacao_aa,
                        perfil=perfil).to_dict()

    # ========================================== journal e alertas
    @app.get("/api/journal")
    def journal_listar(limite: int = Query(default=50, ge=1, le=500)
                       ) -> dict[str, Any]:
        return {
            "entradas": [e.to_dict()
                         for e in st.journal.listar()[:limite]],
            "resumo": st.journal.resumo_por_quadrante(),
        }

    @app.get("/api/alertas")
    def alertas(limite: int = Query(default=50, ge=1, le=200),
                nivel: str = Query(default="")) -> dict[str, Any]:
        from .reporting import NivelAlerta
        nv = NivelAlerta(nivel) if nivel else None
        return {
            "alertas": [a.to_dict()
                        for a in st.alertas.listar(nivel=nv, limite=limite)],
            "resumo": st.alertas.resumo(),
        }

    # ============================================== risco expandido
    @app.get("/api/risco")
    def risco_status() -> dict[str, Any]:
        return st.risk_engine.status()

    @app.post("/api/risco/retomar", dependencies=protegido)
    def risco_retomar(req: RetomarOperacao) -> dict[str, Any]:
        ok, mensagem = st.risk_engine.retomar(req.confirmacao)
        if not ok:
            raise HTTPException(400, mensagem)
        st.store.registrar_evento("ALERTA", "risco",
                                  "operação retomada após halt")
        return {"mensagem": mensagem, "status": st.risk_engine.status()}

    @app.post("/api/risco/halt", dependencies=protegido)
    def risco_halt() -> dict[str, Any]:
        st.risk_engine.halt("halt manual pelo painel")
        st.store.registrar_evento("ALERTA", "risco", "TRADING HALTED manual")
        return {"mensagem": "TRADING HALTED", "status": st.risk_engine.status()}

    @app.get("/api/liquidacao")
    def liquidacao(entry: float = Query(gt=0), stop: float = Query(gt=0),
                   leverage: float = Query(ge=1), lado: str = Query(
                       default="long", pattern="^(long|short)$"),
                   atr_pct: float = Query(default=0.0, ge=0)
                   ) -> dict[str, Any]:
        """Distância até a liquidação, com as duas checagens."""
        from .risk import analisar as analisar_liq
        a = analisar_liq(entry, stop, Side(lado), leverage,
                         notional_usd=1000.0,
                         atr_pct=atr_pct if atr_pct > 0 else None)
        return a.to_dict()

    # ------------------------------------------------------------------ painel
    web_dir = Path(__file__).parent / "web"
    if web_dir.exists():
        app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

        @app.get("/")
        def painel() -> FileResponse:
            return FileResponse(str(web_dir / "index.html"))

    @app.exception_handler(ValueError)
    def erro_valor(_request: Any, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return app


app = None  # instanciado por scripts/serve.py ou uvicorn factory


def factory() -> FastAPI:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    return criar_app()
