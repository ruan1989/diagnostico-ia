"""Comandos operacionais: diagnóstico, status e parada de emergência.

Por que existem
---------------
Um sistema que mexe em dinheiro precisa de três respostas que não dependam
do painel estar aberto, do navegador funcionar ou de alguém saber ler JSON:

    status              está operando? com o que? quanto risco está exposto?
    diagnostico         o que está quebrado, e o que isso impede
    parada_emergencia   parar tudo agora e travar novos envios

O terceiro é o mais importante e o menos usado. Ele precisa funcionar quando
tudo está errado, o que significa: nenhuma dependência de rede para decidir
travar, e a trava tem de sobreviver a um reinício.

A trava persistente
-------------------
Uma parada de emergência que se perde no reinício não é parada de emergência
— é uma pausa. `TravaOperacao` grava o bloqueio no banco, e a guarda de fase
o consulta em cada ordem. Religar exige um comando explícito, não um
`systemctl restart`.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("investai.comandos")

CHAVE_TRAVA = "trava_operacao"

# Códigos de saída, para uso em script e em monitoramento.
SAIDA_OK = 0
SAIDA_BLOQUEIO = 1
SAIDA_AVISO = 2


@dataclass(slots=True)
class Trava:
    ativa: bool
    motivo: str = ""
    desde_ms: int = 0
    origem: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"ativa": self.ativa, "motivo": self.motivo,
                "desde_ms": self.desde_ms, "origem": self.origem}


class TravaOperacao:
    """Bloqueio de envio de ordens que sobrevive a reinício do processo."""

    def __init__(self, store: Any):
        self.store = store

    def ler(self) -> Trava:
        bruto = self.store.get_estado(CHAVE_TRAVA)
        if not bruto:
            return Trava(False)
        if isinstance(bruto, str):
            try:
                bruto = json.loads(bruto)
            except ValueError:
                # Valor corrompido é tratado como trava ATIVA. Na dúvida
                # sobre se alguém pediu parada, não se opera.
                return Trava(True, "registro de trava ilegível no banco",
                             origem="banco")
        if not isinstance(bruto, dict):
            return Trava(True, "registro de trava em formato inesperado",
                         origem="banco")
        return Trava(bool(bruto.get("ativa")), str(bruto.get("motivo", "")),
                     int(bruto.get("desde_ms", 0)),
                     str(bruto.get("origem", "")))

    @property
    def ativa(self) -> bool:
        return self.ler().ativa

    def ativar(self, motivo: str, *, origem: str = "manual",
               agora_ms: int | None = None) -> Trava:
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
        t = Trava(True, motivo, agora, origem)
        self.store.set_estado(CHAVE_TRAVA, t.to_dict())
        return t

    def liberar(self, *, agora_ms: int | None = None) -> Trava:
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
        t = Trava(False, "", agora, "liberada manualmente")
        self.store.set_estado(CHAVE_TRAVA, t.to_dict())
        return t


# --------------------------------------------------------------- diagnóstico
@dataclass(slots=True)
class Checagem:
    nome: str
    ok: bool
    detalhe: str
    # `bloqueia` separa "está ruim" de "impede operar". Sem essa distinção, o
    # diagnóstico ou assusta com o que não importa ou cala sobre o que
    # importa.
    bloqueia: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"nome": self.nome, "ok": self.ok, "detalhe": self.detalhe,
                "bloqueia": self.bloqueia}


@dataclass(slots=True)
class Diagnostico:
    checagens: list[Checagem] = field(default_factory=list)
    gerado_em_ms: int = 0

    @property
    def bloqueios(self) -> list[Checagem]:
        return [c for c in self.checagens if not c.ok and c.bloqueia]

    @property
    def avisos(self) -> list[Checagem]:
        return [c for c in self.checagens if not c.ok and not c.bloqueia]

    @property
    def pode_operar_real(self) -> bool:
        return not self.bloqueios

    @property
    def codigo_saida(self) -> int:
        if self.bloqueios:
            return SAIDA_BLOQUEIO
        if self.avisos:
            return SAIDA_AVISO
        return SAIDA_OK

    def to_dict(self) -> dict[str, Any]:
        return {
            "gerado_em_ms": self.gerado_em_ms,
            "pode_operar_real": self.pode_operar_real,
            "codigo_saida": self.codigo_saida,
            "checagens": [c.to_dict() for c in self.checagens],
            "bloqueios": [c.to_dict() for c in self.bloqueios],
            "avisos": [c.to_dict() for c in self.avisos],
        }

    def texto(self) -> str:
        linhas = ["DIAGNÓSTICO DO SISTEMA", "=" * 60]
        for c in self.checagens:
            marca = "ok  " if c.ok else ("FALHA" if c.bloqueia else "aviso")
            linhas.append(f"[{marca:>5}] {c.nome}: {c.detalhe}")
        linhas.append("=" * 60)
        if self.bloqueios:
            linhas.append(
                f"NÃO PODE OPERAR REAL: {len(self.bloqueios)} bloqueio(s).")
        elif self.avisos:
            linhas.append(
                f"Operação real possível, com {len(self.avisos)} aviso(s). "
                f"Aviso não é permissão: leia cada um.")
        else:
            linhas.append("Nenhum bloqueio e nenhum aviso nas checagens acima. "
                          "Isso NÃO é opinião sobre a estratégia dar lucro.")
        return "\n".join(linhas)


def _checar(nome: str, fn, *, bloqueia: bool = False) -> Checagem:
    """Roda uma checagem sem deixar que a exceção dela derrube o diagnóstico.

    Um diagnóstico que quebra no meio é pior que nenhum: esconde tudo que
    vinha depois.
    """
    try:
        ok, detalhe = fn()
        return Checagem(nome, bool(ok), str(detalhe), bloqueia)
    except Exception as exc:                            # noqa: BLE001
        return Checagem(nome, False, f"{type(exc).__name__}: {exc}", bloqueia)


def diagnostico(estado: Any, *, agora_ms: int | None = None) -> Diagnostico:
    """Confere o sistema inteiro e diz o que cada falha impede."""
    agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
    d = Diagnostico(gerado_em_ms=agora)
    st = estado

    # ---------------------------------------------------------- banco
    def banco():
        st.store.registrar_evento("INFO", "diagnostico", "checagem de escrita")
        n = len(st.store.eventos(limite=1))
        return n >= 1, f"banco em {st.store.caminho} responde a leitura e escrita"
    d.checagens.append(_checar("banco de dados", banco, bloqueia=True))

    # ---------------------------------------------------------- trava
    def trava():
        t = TravaOperacao(st.store).ler()
        if t.ativa:
            return False, (f"PARADA DE EMERGÊNCIA ATIVA desde "
                           f"{t.desde_ms}: {t.motivo or 'sem motivo registrado'}. "
                           f"Nenhuma ordem real sai até liberar explicitamente")
        return True, "nenhuma parada de emergência ativa"
    d.checagens.append(_checar("trava de operação", trava, bloqueia=True))

    # ---------------------------------------------------------- dados
    def dados():
        symbol = (st.settings.universo or ("BTCUSDT",))[0]
        velas = st.hub.candles(symbol, st.settings.signal.timeframe_principal,
                               limit=60)
        if not velas:
            return False, f"nenhuma vela devolvida para {symbol}"
        idade_h = (agora - velas[-1].ts) / 3_600_000
        fonte = "SINTÉTICO (dados simulados)" if st.usar_sintetico else "Bitget"
        if st.usar_sintetico:
            return False, (f"provider {fonte}: {len(velas)} velas de {symbol}. "
                           f"Números do painel NÃO são cotação real")
        return True, (f"provider {fonte}: {len(velas)} velas de {symbol}, "
                      f"última com {idade_h:.1f}h de idade")
    d.checagens.append(_checar("camada de dados", dados, bloqueia=True))

    # ---------------------------------------------------------- credencial
    def credencial():
        if st.credenciais is None:
            return False, ("nenhuma chave de API conectada — o sistema analisa "
                           "e simula, mas não opera")
        if st.usar_sintetico:
            return False, (f"chave {st.credenciais.mascara()} armazenada mas "
                           f"NÃO verificada contra a Bitget (modo sintético)")
        if st.erro_conexao:
            return False, f"chave conectada com erro: {st.erro_conexao}"
        return True, f"chave {st.credenciais.mascara()} conectada e verificada"
    d.checagens.append(_checar("credencial", credencial))

    # ---------------------------------------------------------- estratégias
    def estrategias():
        por_fase = st.strategies.por_fase()
        if not por_fase:
            return False, ("nenhuma estratégia registrada; nada foi medido "
                           "ainda")
        resumo = ", ".join(f"{fase}: {len(ch)}" for fase, ch in
                           sorted(por_fase.items()))
        return True, resumo
    d.checagens.append(_checar("registro de estratégias", estrategias))

    # ---------------------------------------------------------- guarda
    def guarda():
        g = st.guarda.estado()
        if g["chave_vinculada"] is None:
            return False, ("nenhuma estratégia vinculada ao motor; o modo real "
                           "não arma e nenhuma ordem real sai")
        v = g["versao"] or {}
        if not v.get("operavel_real") and v.get("fase") != "assistido":
            return False, (f"{g['chave_vinculada']} está em {v.get('fase')}; "
                           f"faltam as fases "
                           f"{', '.join(v.get('faltam_fases') or [])}")
        return True, (f"{g['chave_vinculada']} em {v.get('fase')}; teto por "
                      f"ordem US$ {g.get('teto_notional_usd')}")
    d.checagens.append(_checar("guarda de fase", guarda))

    # ---------------------------------------------------------- idempotência
    def idem():
        e = st.idempotencia.estado()
        if e["pendentes"]:
            return False, (f"{e['pendentes']} intenção(ões) de ordem com "
                           f"destino desconhecido. Pode haver posição real "
                           f"aberta que este sistema não gerencia")
        if not e["pode_consultar"]:
            return False, ("a conexão atual não sabe consultar ordem por "
                           "clientOid; a idempotência de reinício fica cega")
        return True, "nenhuma intenção de ordem pendente"
    d.checagens.append(_checar("idempotência de ordens", idem, bloqueia=True))

    # ------------------------------------------------- reconciliação
    def reconciliacao():
        rec = getattr(st, "reconciliador", None)
        if rec is None:
            return False, "reconciliador não montado neste processo"
        rel = rec.ultimo
        if rel is None:
            return False, ("estado nunca conferido contra a corretora nesta "
                           "sessão; rode /api/reconciliacao/conferir")
        if rel.erro:
            return False, f"última conferência não concluiu: {rel.erro}"
        if rel.deve_pausar:
            return False, ("divergência com a corretora: "
                           + "; ".join(d.detalhe[:80] for d in rel.divergencias
                                       if d.pausa))
        if rel.divergencias:
            return False, (f"{len(rel.divergencias)} divergência(s) menor(es) "
                           f"com a corretora")
        return True, (f"estado coerente com a corretora "
                      f"({rel.posicoes_locais} posição(ões))")
    d.checagens.append(_checar("reconciliação", reconciliacao, bloqueia=True))

    # ---------------------------------------------------------- risco
    def risco():
        r = st.risk.estado
        if r.kill_switch:
            return False, f"kill switch ACIONADO: {r.motivo_kill}"
        return True, (f"capital US$ {r.capital_atual:.2f}, "
                      f"PnL do dia US$ {r.pnl_dia:+.2f}, "
                      f"{r.perdas_consecutivas} perda(s) consecutiva(s)")
    d.checagens.append(_checar("gestão de risco", risco, bloqueia=True))

    # ---------------------------------------------------------- shadow
    def shadow():
        r = st.shadow.resumo().to_dict()
        if r.get("avisos"):
            return False, "; ".join(r["avisos"])
        return True, (f"{r.get('liquidadas', 0)} decisão(ões) liquidada(s) "
                      f"com amostra suficiente")
    d.checagens.append(_checar("shadow mode", shadow))

    # ---------------------------------------------------------- saúde
    def saude():
        rel = st.monitor.checar_tudo()
        nome = rel.estado_geral.value
        if not rel.pode_abrir_posicao:
            # Não poder ABRIR não é o mesmo que não poder GERENCIAR: a
            # exposição de uma posição já aberta não desaparece junto com o
            # feed, e o diagnóstico precisa dizer as duas coisas.
            gestao = ("gestão de posição aberta segue ativa"
                      if rel.pode_gerenciar_posicao
                      else "gestão de posição aberta TAMBÉM comprometida")
            return False, (f"saúde em {nome}: não pode abrir posição "
                           f"({'; '.join(rel.motivos) or 'sem motivo detalhado'}); "
                           f"{gestao}")
        return True, f"monitor de saúde em {nome}"
    d.checagens.append(_checar("monitor de saúde", saude, bloqueia=True))

    return d


# ------------------------------------------------------------------- status
def status(estado: Any) -> dict[str, Any]:
    """Retrato curto do que está acontecendo agora."""
    st = estado
    motor = st.engine.status()
    trava = TravaOperacao(st.store).ler()
    posicoes = st.executor.posicoes()
    return {
        "modo": st.executor.modo,
        "armado_live": bool(motor["motor"]["armado_live"]),
        "motor_rodando": bool(motor["motor"]["rodando"]),
        "trava_operacao": trava.to_dict(),
        "provider": "sintetico" if st.usar_sintetico else "bitget",
        "credencial": (st.credenciais.mascara()
                       if st.credenciais else "não conectada"),
        "guarda": st.guarda.estado(),
        "risco": st.risk.estado.to_dict(),
        "posicoes_abertas": len(posicoes),
        "posicoes": [
            {"symbol": p.symbol, "side": p.side.value, "size": p.size,
             "entry": p.entry, "stop_loss": p.stop_loss,
             "notional_usd": round(p.notional_usd, 2),
             "risco_usd": round(p.risk_usd, 2), "modo": p.modo}
            for p in posicoes],
        "envios_pendentes": st.idempotencia.estado()["pendentes"],
        "propostas_aguardando": len(st.engine.propostas_pendentes()),
        "shadow": st.shadow.resumo().to_dict(),
        "ordens_enviadas": motor["motor"]["ordens_enviadas"],
        "bloqueadas_por_fase": motor["motor"].get("bloqueadas_por_fase", 0),
    }


def status_texto(s: dict[str, Any]) -> str:
    linhas = ["STATUS", "=" * 60]
    trava = s["trava_operacao"]
    if trava["ativa"]:
        linhas.append(f"!! PARADA DE EMERGÊNCIA ATIVA: {trava['motivo']}")
    linhas += [
        f"modo               {s['modo']}"
        + ("  (armado para ordens reais)" if s["armado_live"] else ""),
        f"motor              {'rodando' if s['motor_rodando'] else 'parado'}",
        f"dados              {s['provider']}",
        f"credencial         {s['credencial']}",
        f"estratégia no ar   {s['guarda']['chave_vinculada'] or 'nenhuma'}",
    ]
    v = s["guarda"].get("versao") or {}
    if v:
        linhas.append(f"fase               {v.get('fase')}"
                      + (f"  (faltam: {', '.join(v.get('faltam_fases') or [])})"
                         if v.get("faltam_fases") else ""))
    r = s["risco"]
    linhas += [
        f"capital            US$ {r.get('capital_atual', 0):.2f}",
        f"kill switch        {'ACIONADO' if r.get('kill_switch') else 'livre'}",
        f"posições abertas   {s['posicoes_abertas']}",
        f"envios pendentes   {s['envios_pendentes']}",
        f"propostas          {s['propostas_aguardando']} aguardando confirmação",
    ]
    for p in s["posicoes"]:
        linhas.append(f"  - {p['symbol']} {p['side']} {p['size']} @ "
                      f"{p['entry']} stop {p['stop_loss']} "
                      f"(risco US$ {p['risco_usd']:.2f}, {p['modo']})")
    sh = s.get("shadow") or {}
    linhas.append(f"shadow             {sh.get('liquidadas', 0)} liquidada(s) de "
                  f"{sh.get('total', 0)} decisão(ões)")
    for aviso in (sh.get("avisos") or []):
        linhas.append(f"  aviso: {aviso}")
    return "\n".join(linhas)


# -------------------------------------------------------- parada de emergência
def parada_emergencia(estado: Any, motivo: str = "comando de emergência", *,
                      fechar_posicoes: bool = True,
                      agora_ms: int | None = None) -> dict[str, Any]:
    """Para tudo agora e trava novos envios.

    A ordem das operações importa e é esta:

      1. trava primeiro. Se o passo 2 falhar no meio, o sistema fica travado,
         não meio-aberto;
      2. desarma o modo real;
      3. para o motor;
      4. fecha as posições.

    Travar antes de fechar é contraintuitivo, mas é o certo: fechar posição é
    a parte que depende de rede e pode falhar. Se a trava viesse por último,
    uma falha ali deixaria o sistema livre para abrir posição nova.
    """
    st = estado
    agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
    passos: list[str] = []
    erros: list[str] = []

    t = TravaOperacao(st.store).ativar(motivo, origem="parada_emergencia",
                                       agora_ms=agora)
    passos.append(f"trava de operação ATIVADA: {motivo}")

    try:
        passos.append(st.engine.desarmar_live())
    except Exception as exc:                            # noqa: BLE001
        erros.append(f"desarmar modo real: {exc}")

    try:
        passos.append(st.engine.parar())
    except Exception as exc:                            # noqa: BLE001
        erros.append(f"parar motor: {exc}")

    fechadas: list[str] = []
    if fechar_posicoes:
        try:
            fechadas = st.engine.fechar_tudo(f"parada de emergência: {motivo}")
            passos.extend(fechadas)
        except Exception as exc:                        # noqa: BLE001
            erros.append(f"fechar posições: {exc}")

    restantes = []
    try:
        restantes = [p.symbol for p in st.executor.posicoes()]
    except Exception as exc:                            # noqa: BLE001
        erros.append(f"conferir posições restantes: {exc}")

    st.store.registrar_evento(
        "ALERTA", "emergencia", f"parada de emergência: {motivo}",
        {"passos": passos, "erros": erros, "restantes": restantes})

    return {
        "ok": not erros and not restantes,
        "trava": t.to_dict(),
        "passos": passos,
        "fechadas": fechadas,
        "posicoes_restantes": restantes,
        "erros": erros,
        "mensagem": (
            "sistema travado e posições encerradas"
            if not erros and not restantes else
            "sistema TRAVADO, mas a limpeza não terminou — confira na Bitget: "
            + "; ".join(erros + [f"posição aberta em {s}" for s in restantes])),
    }


def liberar_trava(estado: Any, *, agora_ms: int | None = None) -> dict[str, Any]:
    """Religa o sistema depois de uma parada. Exige ação explícita."""
    t = TravaOperacao(estado.store).liberar(agora_ms=agora_ms)
    estado.store.registrar_evento(
        "ALERTA", "emergencia", "trava de operação liberada manualmente")
    return {"trava": t.to_dict(),
            "mensagem": ("trava liberada; o modo real ainda precisa ser armado "
                         "de novo e a estratégia vinculada continua sendo "
                         "conferida em cada ordem")}


__all__ = [
    "CHAVE_TRAVA", "Checagem", "Diagnostico", "SAIDA_AVISO", "SAIDA_BLOQUEIO",
    "SAIDA_OK", "Trava", "TravaOperacao", "diagnostico", "liberar_trava",
    "parada_emergencia", "status", "status_texto",
]
