import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

from investai.config import Settings                          # noqa: E402
from investai.datahub import DataHub                           # noqa: E402
from investai.exchanges import SyntheticProvider               # noqa: E402


@pytest.fixture
def provider() -> SyntheticProvider:
    # Timestamp fixo: sem isso os testes mudam de resultado ao virar a hora.
    return SyntheticProvider(seed=777, agora_ms=1_726_000_000_000)


@pytest.fixture
def hub(provider) -> DataHub:
    return DataHub(provider)


@pytest.fixture
def settings(tmp_path) -> Settings:
    s = Settings()
    s.data_dir = str(tmp_path)
    s.db_path = str(tmp_path / "test.db")
    s.universo = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    s.validar()
    return s


@pytest.fixture
def velas(provider):
    return provider.candles("BTCUSDT", "1H", limit=1500)


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch, tmp_path):
    """Nenhum teste pode depender do ambiente da máquina ou vazar para ele."""
    for chave in ("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_API_PASSPHRASE",
                  "INVESTAI_MODE", "INVESTAI_UNIVERSE", "INVESTAI_CAPITAL_USD",
                  "INVESTAI_SYNTHETIC", "INVESTAI_API_TOKEN", "BRAPI_TOKEN"):
        monkeypatch.delenv(chave, raising=False)
    monkeypatch.setenv("INVESTAI_DATA_DIR", str(tmp_path))


@pytest.fixture
def store(tmp_path):
    from investai.store import Store
    s = Store(tmp_path / "trading.db")
    yield s
    s.close()


@pytest.fixture
def motor(settings, hub, store):
    from investai.analysis.screener import Screener
    from investai.risk import RiskManager
    from investai.trading import Executor, TradingEngine
    screener = Screener(hub, settings)
    executor = Executor(settings.exec, modo="paper")
    risco = RiskManager(settings.risk, settings.exec.capital_inicial_usd)
    return TradingEngine(settings, screener, executor, risco, store)
