"""Registro de fontes de dados.

Este módulo existe para uma única finalidade: **tornar impossível o sistema
apresentar um número sem fonte**. Toda classe de ativo e todo tipo de dado
(preço, fundamentos, notícia, calendário econômico) declara aqui de qual
provedor depende. Se o provedor não estiver conectado, a consulta devolve
`NAO_CONFIGURADO` e a interface mostra `FONTE NÃO CONFIGURADA` — nunca um
valor plausível.

É a diferença entre um painel que diz "não sei" e um painel que inventa.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .quality import FONTE_NAO_CONFIGURADA, QualityStatus
from .symbols import AssetClass

log = logging.getLogger("investai.registry")


class DataKind(str, Enum):
    OHLCV = "ohlcv"
    TICKER = "ticker"
    ORDER_BOOK = "order_book"
    TRADES = "trades"
    FUNDING = "funding"
    OPEN_INTEREST = "open_interest"
    LIQUIDACOES = "liquidacoes"
    FUNDAMENTOS = "fundamentos"
    DIVIDENDOS = "dividendos"
    NOTICIAS = "noticias"
    CALENDARIO_ECONOMICO = "calendario_economico"
    CURVA_JUROS = "curva_juros"
    INDICADORES_MACRO = "indicadores_macro"
    SENTIMENTO = "sentimento"


class ProviderState(str, Enum):
    CONECTADO = "conectado"
    NAO_CONFIGURADO = "nao_configurado"
    ERRO = "erro"
    DESABILITADO = "desabilitado"


@dataclass(slots=True)
class ProviderInfo:
    """Descrição de um provedor e o que ele cobre."""

    nome: str
    descricao: str
    classes: tuple[AssetClass, ...]
    tipos: tuple[DataKind, ...]
    requer_credencial: bool = False
    url_docs: str = ""
    # Callable que devolve True se o provedor está utilizável agora.
    checar: Callable[[], bool] | None = None
    estado: ProviderState = ProviderState.NAO_CONFIGURADO
    ultimo_erro: str = ""
    ultimo_check_ms: int = 0
    instrucao_configuracao: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "nome": self.nome, "descricao": self.descricao,
            "classes": [c.value for c in self.classes],
            "tipos": [t.value for t in self.tipos],
            "requer_credencial": self.requer_credencial,
            "url_docs": self.url_docs, "estado": self.estado.value,
            "ultimo_erro": self.ultimo_erro,
            "ultimo_check_ms": self.ultimo_check_ms,
            "instrucao_configuracao": self.instrucao_configuracao,
        }


@dataclass(slots=True)
class Cobertura:
    """Resposta a "tenho dados para analisar isto?"."""

    classe: AssetClass
    tipo: DataKind
    disponivel: bool
    provedores: list[str] = field(default_factory=list)
    status: QualityStatus = QualityStatus.NAO_CONFIGURADO
    mensagem: str = FONTE_NAO_CONFIGURADA
    instrucao: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "classe": self.classe.value, "tipo": self.tipo.value,
            "disponivel": self.disponivel, "provedores": self.provedores,
            "status": self.status.value, "mensagem": self.mensagem,
            "instrucao": self.instrucao,
        }


class DataRegistry:
    def __init__(self) -> None:
        self._provedores: dict[str, ProviderInfo] = {}

    # ---------------------------------------------------------------- registro
    def registrar(self, info: ProviderInfo) -> None:
        self._provedores[info.nome] = info

    def remover(self, nome: str) -> bool:
        return self._provedores.pop(nome, None) is not None

    def marcar_conectado(self, nome: str) -> None:
        if p := self._provedores.get(nome):
            p.estado = ProviderState.CONECTADO
            p.ultimo_erro = ""
            p.ultimo_check_ms = int(time.time() * 1000)

    def marcar_erro(self, nome: str, erro: str) -> None:
        if p := self._provedores.get(nome):
            p.estado = ProviderState.ERRO
            p.ultimo_erro = erro
            p.ultimo_check_ms = int(time.time() * 1000)
            log.warning("provedor %s em erro: %s", nome, erro)

    def marcar_nao_configurado(self, nome: str) -> None:
        if p := self._provedores.get(nome):
            p.estado = ProviderState.NAO_CONFIGURADO
            p.ultimo_check_ms = int(time.time() * 1000)

    def revalidar(self) -> dict[str, str]:
        """Roda o `checar` de cada provedor e atualiza o estado."""
        resultado: dict[str, str] = {}
        for nome, p in self._provedores.items():
            if p.estado is ProviderState.DESABILITADO:
                resultado[nome] = p.estado.value
                continue
            if p.checar is None:
                resultado[nome] = p.estado.value
                continue
            try:
                ok = bool(p.checar())
            except Exception as exc:                    # noqa: BLE001
                self.marcar_erro(nome, f"{type(exc).__name__}: {exc}")
                resultado[nome] = ProviderState.ERRO.value
                continue
            if ok:
                self.marcar_conectado(nome)
            else:
                self.marcar_nao_configurado(nome)
            resultado[nome] = p.estado.value
        return resultado

    # --------------------------------------------------------------- consulta
    def provedores(self) -> list[ProviderInfo]:
        return list(self._provedores.values())

    def conectados(self) -> list[str]:
        return [n for n, p in self._provedores.items()
                if p.estado is ProviderState.CONECTADO]

    def cobertura(self, classe: AssetClass, tipo: DataKind) -> Cobertura:
        """Diz se há provedor conectado capaz de fornecer aquele dado."""
        candidatos = [
            p for p in self._provedores.values()
            if classe in p.classes and tipo in p.tipos
        ]
        conectados = [p for p in candidatos
                      if p.estado is ProviderState.CONECTADO]

        if conectados:
            return Cobertura(
                classe=classe, tipo=tipo, disponivel=True,
                provedores=[p.nome for p in conectados],
                status=(QualityStatus.OK if len(conectados) > 1
                        else QualityStatus.DEGRADADO),
                mensagem=("confirmação cruzada disponível"
                          if len(conectados) > 1
                          else "fonte única, sem confirmação cruzada"),
            )

        if not candidatos:
            return Cobertura(
                classe=classe, tipo=tipo, disponivel=False,
                status=QualityStatus.NAO_CONFIGURADO,
                mensagem=f"{FONTE_NAO_CONFIGURADA}: nenhum provedor deste "
                         f"sistema cobre {tipo.value} para {classe.value}",
                instrucao="É necessário implementar um conector para esta "
                          "combinação de classe e tipo de dado.",
            )

        com_erro = [p for p in candidatos if p.estado is ProviderState.ERRO]
        if com_erro:
            return Cobertura(
                classe=classe, tipo=tipo, disponivel=False,
                provedores=[p.nome for p in com_erro],
                status=QualityStatus.INADEQUADO,
                mensagem=f"provedor com erro: {com_erro[0].ultimo_erro}",
                instrucao=com_erro[0].instrucao_configuracao,
            )

        return Cobertura(
            classe=classe, tipo=tipo, disponivel=False,
            provedores=[p.nome for p in candidatos],
            status=QualityStatus.NAO_CONFIGURADO,
            mensagem=f"{FONTE_NAO_CONFIGURADA}: "
                     f"{', '.join(p.nome for p in candidatos)} não conectado",
            instrucao=candidatos[0].instrucao_configuracao,
        )

    def matriz_cobertura(self) -> dict[str, dict[str, Any]]:
        """Matriz completa classe × tipo, para o painel exibir sem inventar."""
        out: dict[str, dict[str, Any]] = {}
        for classe in AssetClass:
            if classe is AssetClass.DESCONHECIDO:
                continue
            out[classe.value] = {
                tipo.value: self.cobertura(classe, tipo).to_dict()
                for tipo in DataKind
            }
        return out

    def resumo(self) -> dict[str, Any]:
        por_estado: dict[str, list[str]] = {}
        for p in self._provedores.values():
            por_estado.setdefault(p.estado.value, []).append(p.nome)
        faltando = [
            f"{c.value}/{t.value}"
            for c in AssetClass if c is not AssetClass.DESCONHECIDO
            for t in DataKind
            if not self.cobertura(c, t).disponivel
        ]
        return {
            "total_provedores": len(self._provedores),
            "por_estado": por_estado,
            "conectados": self.conectados(),
            "combinacoes_sem_fonte": len(faltando),
            "exemplos_sem_fonte": faltando[:20],
        }


def registry_padrao(*, bitget_ok: Callable[[], bool] | None = None,
                    brapi_ok: Callable[[], bool] | None = None,
                    sintetico: bool = False) -> DataRegistry:
    """Monta o registro com os provedores que este sistema realmente possui.

    As entradas não implementadas são declaradas de propósito, com estado
    `nao_configurado` e instrução de configuração. Declarar a lacuna é melhor
    do que omiti-la: o painel mostra exatamente o que o sistema NÃO sabe.
    """
    reg = DataRegistry()

    if sintetico:
        reg.registrar(ProviderInfo(
            nome="sintetico",
            descricao="Gerador determinístico para demonstração e teste. "
                      "NÃO são cotações de mercado.",
            classes=(AssetClass.CRIPTO,),
            tipos=(DataKind.OHLCV, DataKind.TICKER, DataKind.FUNDING,
                   DataKind.OPEN_INTEREST, DataKind.ORDER_BOOK),
            checar=lambda: True,
            estado=ProviderState.CONECTADO,
            instrucao_configuracao="Desligue INVESTAI_SYNTHETIC para usar "
                                   "dados reais de mercado.",
        ))

    reg.registrar(ProviderInfo(
        nome="bitget",
        descricao="Bitget API v2 — candles, ticker, funding e open interest "
                  "de futuros USDT-M. Dados públicos não exigem credencial.",
        classes=(AssetClass.CRIPTO,),
        tipos=(DataKind.OHLCV, DataKind.TICKER, DataKind.FUNDING,
               DataKind.OPEN_INTEREST),
        requer_credencial=False,
        url_docs="https://www.bitget.com/api-doc/contract/intro",
        checar=bitget_ok,
        instrucao_configuracao="Requer acesso de rede a api.bitget.com. "
                               "Credencial só é necessária para operar.",
    ))

    reg.registrar(ProviderInfo(
        nome="brapi",
        descricao="brapi.dev — cotação de ativos da B3 (ações, FIIs, ETFs).",
        classes=(AssetClass.ACAO, AssetClass.FII, AssetClass.ETF,
                 AssetClass.INDICE),
        tipos=(DataKind.TICKER, DataKind.OHLCV),
        requer_credencial=True,
        url_docs="https://brapi.dev/docs",
        checar=brapi_ok,
        instrucao_configuracao="Defina BRAPI_TOKEN no .env.",
    ))

    reg.registrar(ProviderInfo(
        nome="snapshot_fii",
        descricao="Arquivo local data/fiis_snapshot.json com fundamentais de "
                  "FII mantidos manualmente.",
        classes=(AssetClass.FII,),
        tipos=(DataKind.FUNDAMENTOS, DataKind.DIVIDENDOS),
        instrucao_configuracao="Preencha data/fiis_snapshot.json com os dados "
                               "do relatório gerencial do fundo e dos "
                               "informes na B3 (fundos.net).",
    ))

    # --------------------------------------------------- lacunas declaradas
    lacunas = [
        ("fundamentos_acoes",
         "Fundamentos de ações (receita, margens, ROE/ROIC, dívida, FCF).",
         (AssetClass.ACAO,), (DataKind.FUNDAMENTOS, DataKind.DIVIDENDOS),
         "Requer conector a um provedor de dados fundamentalistas. "
         "Sem ele, a análise de ações permanece indisponível."),
        ("renda_fixa",
         "Taxas de Tesouro, CDB, LCI/LCA, debêntures e curva de juros.",
         (AssetClass.RENDA_FIXA,), (DataKind.CURVA_JUROS, DataKind.FUNDAMENTOS),
         "Requer conector a um provedor de renda fixa ou importação manual."),
        ("etf_composicao",
         "Composição, benchmark e tracking error de ETFs.",
         (AssetClass.ETF,), (DataKind.FUNDAMENTOS,),
         "Requer conector ao informe de composição do ETF."),
        ("macro",
         "Juros, inflação, PIB, emprego, câmbio e commodities.",
         (AssetClass.INDICE, AssetClass.CAMBIO, AssetClass.COMMODITY),
         (DataKind.INDICADORES_MACRO, DataKind.CALENDARIO_ECONOMICO),
         "Requer conector a uma fonte macro (ex.: SGS do Banco Central) ou "
         "importação do calendário econômico."),
        ("noticias",
         "Notícias de mercado com classificação de impacto.",
         (AssetClass.CRIPTO, AssetClass.ACAO, AssetClass.FII),
         (DataKind.NOTICIAS, DataKind.SENTIMENTO),
         "Requer conector a um agregador de notícias. Sem ele, o agente de "
         "notícias reporta abstenção em vez de sentimento neutro."),
        ("liquidacoes",
         "Liquidações agregadas em futuros de cripto.",
         (AssetClass.CRIPTO,), (DataKind.LIQUIDACOES,),
         "Requer stream de liquidações da exchange."),
        ("order_book",
         "Profundidade do livro de ofertas em tempo real.",
         (AssetClass.CRIPTO,), (DataKind.ORDER_BOOK, DataKind.TRADES),
         "Requer conexão WebSocket ao livro da exchange. O simulador de "
         "paper trading usa livro sintético enquanto isto não existir."),
    ]
    for nome, desc, classes, tipos, instrucao in lacunas:
        reg.registrar(ProviderInfo(
            nome=nome, descricao=desc, classes=classes, tipos=tipos,
            estado=ProviderState.NAO_CONFIGURADO,
            instrucao_configuracao=instrucao,
        ))

    return reg
