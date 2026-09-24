from .base import (
    ExchangeError, ExchangeUnreachable, InsufficientPermissions,
    MarketDataProvider, TradingProvider,
)
from .bitget import BitgetClient
from .keystore import ApiCredentials, CredentialError, Keystore, credenciais_do_ambiente
from .synthetic import SyntheticProvider

__all__ = [
    "ApiCredentials", "BitgetClient", "CredentialError", "ExchangeError",
    "ExchangeUnreachable",
    "InsufficientPermissions", "Keystore", "MarketDataProvider",
    "SyntheticProvider", "TradingProvider", "credenciais_do_ambiente",
]
from .feed_estado import (
    SILENCIO_PARA_RECONECTAR_MS, TOLERANCIA_PADRAO_MS, EstadoCanal,
    EstadoFeed, Lacuna, Mensagem,
)
from .feed_ws import (
    URL_PRIVADA, URL_PUBLICA, FeedWebSocket, assinar_login, canais_publicos,
)

__all__ += [
    "EstadoCanal", "EstadoFeed", "FeedWebSocket", "Lacuna", "Mensagem",
    "SILENCIO_PARA_RECONECTAR_MS", "TOLERANCIA_PADRAO_MS", "URL_PRIVADA",
    "URL_PUBLICA", "assinar_login", "canais_publicos",
]
from .ambiente import (
    PREFIXO_DEMO, Ambiente, cabecalhos_de, do_demo, para_demo,
    product_type_de, traduzir,
)
from .ambiente import resumo as resumo_ambiente

__all__ += [
    "Ambiente", "PREFIXO_DEMO", "cabecalhos_de", "do_demo", "para_demo",
    "product_type_de", "resumo_ambiente", "traduzir",
]
