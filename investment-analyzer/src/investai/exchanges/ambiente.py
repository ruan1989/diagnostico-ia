"""Ambiente de execução: real ou demo trading.

Por que demo não é "quase real"
-------------------------------
Shadow mode prova que as DECISÕES valem alguma coisa: ele registra o que o
sistema faria e confere contra o que o mercado fez. O que ele não toca é o
encanamento — assinatura aceita, tamanho arredondado ao passo do contrato,
stop anexado à ordem de abertura, `clientOid` impedindo duplicata,
reconciliação sem divergência.

Nada disso aparece em simulação, e tudo isso quebra na primeira ordem real.
Demo trading é onde essas coisas quebram sem custar dinheiro.

Como a Bitget separa os dois
----------------------------
Não é outro endpoint: é o MESMO, com três diferenças que precisam andar
juntas ou nada funciona:

    productType   `USDT-FUTURES` vira `SUSDT-FUTURES`
    símbolo       `BTCUSDT` vira `SBTCSUSDT`
    cabeçalho     `paptrading: 1` em toda requisição

Esquecer uma das três não dá erro claro: dá "símbolo não existe" ou, pior,
uma ordem real enviada achando que era demo. Por isso a tradução mora aqui,
em um lugar só, em vez de espalhada por cada chamada.

A tradução do símbolo
---------------------
O par de demo é o par real com `S` no começo e `S` antes da moeda de
margem. `BTCUSDT` → `SBTCSUSDT`; `ETHUSDT` → `SETHSUSDT`. A conversão é
reversível, e os dois sentidos são testados: um símbolo que vai traduzido e
volta diferente produziria posição fantasma na reconciliação.
"""
from __future__ import annotations

from enum import Enum
from typing import Any


class Ambiente(str, Enum):
    REAL = "real"
    DEMO = "demo"

    @property
    def e_demo(self) -> bool:
        return self is Ambiente.DEMO


DESCRICAO: dict[Ambiente, str] = {
    Ambiente.REAL: "conta real; ordens movimentam dinheiro de verdade",
    Ambiente.DEMO: "ambiente de teste da corretora; ordens são reais no "
                   "protocolo e falsas no dinheiro",
}

PREFIXO_DEMO = "S"


def product_type_de(product_type: str, ambiente: Ambiente) -> str:
    """`USDT-FUTURES` no real, `SUSDT-FUTURES` no demo.

    A conversão vale nos dois sentidos, e o sentido de volta importa tanto
    quanto o de ida: um cliente que sai do demo carregando `SUSDT-FUTURES`
    consultaria contas que não existem no real e concluiria saldo zero.
    """
    pt = product_type.upper()
    if ambiente.e_demo:
        return pt if pt.startswith("S") else f"S{pt}"
    # Volta ao real: tira o `S` inicial, desde que o resto continue sendo um
    # productType plausível. A checagem do sufixo evita comer o `S` de um
    # nome que legitimamente comece com ele.
    if pt.startswith("S") and pt[1:].endswith("-FUTURES"):
        return pt[1:]
    return pt


def para_demo(symbol: str, margin_coin: str = "USDT") -> str:
    """`BTCUSDT` → `SBTCSUSDT`."""
    s = symbol.upper()
    coin = margin_coin.upper()
    if s.startswith(PREFIXO_DEMO) and f"{PREFIXO_DEMO}{coin}" in s:
        return s                                # já traduzido
    base = s[:-len(coin)] if s.endswith(coin) else s
    return f"{PREFIXO_DEMO}{base}{PREFIXO_DEMO}{coin}"


def do_demo(symbol: str, margin_coin: str = "USDT") -> str:
    """`SBTCSUSDT` → `BTCUSDT`. O caminho de volta, para a reconciliação."""
    s = symbol.upper()
    coin = margin_coin.upper()
    alvo = f"{PREFIXO_DEMO}{coin}"
    if not s.startswith(PREFIXO_DEMO) or not s.endswith(alvo):
        return s
    return f"{s[len(PREFIXO_DEMO):-len(alvo)]}{coin}"


def traduzir(symbol: str, ambiente: Ambiente,
             margin_coin: str = "USDT") -> str:
    """Símbolo no formato que o ambiente espera."""
    if ambiente.e_demo:
        return para_demo(symbol, margin_coin)
    return do_demo(symbol, margin_coin)


def cabecalhos_de(ambiente: Ambiente) -> dict[str, str]:
    """Cabeçalhos extras. É o que de fato liga o modo demo na Bitget."""
    return {"paptrading": "1"} if ambiente.e_demo else {}


def resumo(ambiente: Ambiente, product_type: str,
           margin_coin: str = "USDT") -> dict[str, Any]:
    return {
        "ambiente": ambiente.value,
        "descricao": DESCRICAO[ambiente],
        "product_type": product_type_de(product_type, ambiente),
        "cabecalhos": cabecalhos_de(ambiente),
        "exemplo_simbolo": traduzir("BTCUSDT", ambiente, margin_coin),
        "aviso": (
            "Ordens no ambiente demo NÃO movimentam dinheiro, e resultados "
            "medidos nele NÃO se somam a resultados reais: o livro de demo "
            "não tem a mesma profundidade nem os mesmos participantes."
            if ambiente.e_demo else
            "Ambiente REAL: toda ordem enviada daqui movimenta dinheiro."),
    }


__all__ = [
    "Ambiente", "DESCRICAO", "PREFIXO_DEMO", "cabecalhos_de", "do_demo",
    "para_demo", "product_type_de", "resumo", "traduzir",
]
