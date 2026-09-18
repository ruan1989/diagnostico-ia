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

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .analysis.screener import Screener
from .backtest.engine import rodar_backtest
from .config import Settings
from .datahub import DataHub
from .exchanges import (
    ApiCredentials, BitgetClient, CredentialError, ExchangeError, Keystore,
    SyntheticProvider, credenciais_do_ambiente,
)
from .models import FiiOpportunity, Side
from .passive import carteira_sugerida, provider_padrao, ranquear
from .risk import RiskManager
from .store import Store
from .trading import CONFIRMACAO_LIVE, Executor, TradingEngine

log = logging.getLogger("investai.api")

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


class CarteiraFii(BaseModel):
    capital: float = Field(gt=0)
    max_por_fundo_pct: float = Field(default=20.0, gt=0, le=100)
    min_score: float = Field(default=62.0, ge=0, le=100)
    max_fundos: int = Field(default=8, ge=1, le=30)


class FecharPosicao(BaseModel):
    symbol: str
    fracao: float = Field(default=1.0, gt=0, le=1.0)


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

        self.risk = RiskManager(self.settings.risk,
                                self.settings.exec.capital_inicial_usd)
        self.executor = Executor(self.settings.exec, backend=self.bitget,
                                 modo="paper")
        self.engine = TradingEngine(self.settings, self.screener, self.executor,
                                    self.risk, self.store)
        self.fii_provider = provider_padrao(
            self.settings.data_dir,
            token_brapi=os.environ.get("BRAPI_TOKEN", ""),
            usar_rede=not usar_sintetico)

        if self.credenciais is not None:
            self.conectar(self.credenciais)

    def _montar_provider(self) -> Any:
        if self.usar_sintetico:
            log.warning("usando provider SINTÉTICO — dados simulados, não de mercado")
            return SyntheticProvider()
        cliente = BitgetClient(self.credenciais,
                               product_type=self.settings.exec.product_type,
                               margin_coin=self.settings.exec.margin_coin)
        self.bitget = cliente
        return cliente

    def conectar(self, cred: ApiCredentials) -> dict[str, Any]:
        """Anexa credenciais ao cliente e valida contra a exchange."""
        self.credenciais = cred
        if self.usar_sintetico:
            self.erro_conexao = ""
            return {"ok": True, "api_key": cred.mascara(),
                    "aviso": "modo sintético ativo: credencial armazenada mas "
                             "nenhuma ordem real será enviada"}
        if self.bitget is None:
            self.bitget = BitgetClient(
                cred, product_type=self.settings.exec.product_type,
                margin_coin=self.settings.exec.margin_coin)
        else:
            self.bitget.cred = cred
        self.executor.backend = self.bitget
        diag = self.bitget.verificar_credenciais()
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
        return {"resultado": st.engine.ciclo(), "status": st.engine.status()}

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

    @app.post("/api/risco/rearmar", dependencies=protegido)
    def risco_rearmar() -> dict[str, Any]:
        st.risk.liberar_kill_switch()
        st.store.registrar_evento("ALERTA", "api", "kill switch rearmado manualmente")
        return {"mensagem": "kill switch rearmado", "risco": st.risk.estado.to_dict()}

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
