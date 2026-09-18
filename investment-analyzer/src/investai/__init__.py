"""InvestAI — análise de oportunidades em cripto (futuros) e renda passiva.

O sistema é construído sobre uma premissa explícita: não existe entrada com
acerto garantido. O que ele faz é medir expectativa em dados históricos,
recusar operar quando a evidência é fraca e limitar o risco de cada operação
para que uma sequência ruim não zere a conta.
"""
from .config import Settings

__version__ = "1.0.0"
__all__ = ["Settings", "__version__"]
