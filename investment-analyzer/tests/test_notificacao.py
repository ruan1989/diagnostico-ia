"""Alertas que saem do processo.

A central de alertas guarda em memória: ótimo para o painel, inútil para
quem está dormindo. Este módulo leva os que importam para um lugar que vibra
no bolso — e, principalmente, NÃO leva o resto. Um canal que recebe cinquenta
mensagens por dia deixa de ser lido em uma semana, e aí o alerta que
importava chega no mesmo lugar dos outros quarenta e nove.
"""
import pytest

from investai.reporting.alertas import Alerta, CategoriaAlerta, NivelAlerta
from investai.reporting.notificacao import (
    Notificador, TransporteMemoria, TransporteTelegram, formatar,
    telegram_do_ambiente,
)

T0 = 1_700_000_000_000


class Relogio:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t

    def avancar(self, ms):
        self.t += ms
        return self.t


def alerta(titulo="kill switch acionado", nivel=NivelAlerta.URGENTE,
           symbol="BTCUSDT", ts=T0):
    return Alerta(CategoriaAlerta.RISCO, nivel, titulo,
                  "perda diária atingiu o limite configurado", ts=ts,
                  symbol=symbol, acao_do_sistema="novas entradas bloqueadas")


def montar(**kw):
    rel = Relogio()
    t = TransporteMemoria()
    return Notificador(t, relogio=rel, **kw), t, rel


# =====================================================================
# Filtros
# =====================================================================
def test_sem_canal_configurado_nao_envia_nem_quebra():
    n = Notificador(None)
    assert not n.configurado
    assert n.notificar(alerta()) is False


def test_urgente_e_entregue():
    n, t, _ = montar()
    assert n.notificar(alerta())
    assert len(t.enviadas) == 1
    assert "kill switch" in t.enviadas[0]


def test_informativo_fica_no_painel():
    n, t, _ = montar()
    assert not n.notificar(alerta(nivel=NivelAlerta.INFO))
    assert t.enviadas == []
    assert n.estado()["suprimidos_por_nivel"] == 1


def test_nivel_minimo_configuravel():
    n, t, _ = montar(nivel_minimo=NivelAlerta.ATENCAO)
    assert n.notificar(alerta(nivel=NivelAlerta.ATENCAO))
    assert not n.notificar(alerta(titulo="outro", nivel=NivelAlerta.INFO))


def test_duplicado_na_janela_e_suprimido():
    """Um feed congelado geraria um alerta por ciclo."""
    n, t, rel = montar()
    assert n.notificar(alerta())
    rel.avancar(60_000)
    assert not n.notificar(alerta())
    assert len(t.enviadas) == 1
    assert n.estado()["suprimidos_por_duplicidade"] == 1


def test_duplicado_volta_depois_da_janela():
    n, t, rel = montar(janela_dedup_ms=100_000)
    n.notificar(alerta())
    rel.avancar(100_001)
    assert n.notificar(alerta())
    assert len(t.enviadas) == 2


def test_alertas_de_pares_diferentes_nao_sao_duplicados():
    n, t, _ = montar()
    n.notificar(alerta(symbol="BTCUSDT"))
    assert n.notificar(alerta(symbol="ETHUSDT"))
    assert len(t.enviadas) == 2


def test_teto_por_hora_salva_o_canal_no_dia_ruim():
    """Se algo dispara em cascata, o canal não pode virar ruído.

    É justamente no dia ruim que ele precisa continuar sendo lido.
    """
    n, t, rel = montar(teto_por_hora=3)
    for i in range(6):
        rel.avancar(60_000)
        n.notificar(alerta(titulo=f"evento {i}"))
    assert len(t.enviadas) == 3
    assert n.estado()["suprimidos_por_teto"] == 3
    assert n.estado()["aguardando_resumo"] == 3


def test_teto_se_renova_depois_de_uma_hora():
    n, t, rel = montar(teto_por_hora=2)
    n.notificar(alerta(titulo="a"))
    n.notificar(alerta(titulo="b"))
    assert not n.notificar(alerta(titulo="c"))
    rel.avancar(3_600_001)
    assert n.notificar(alerta(titulo="d"))


def test_resumo_impede_que_a_cascata_suma_sem_rastro():
    """Sem isso o operador acharia que estava quieto no dia em que não estava."""
    n, t, rel = montar(teto_por_hora=1)
    n.notificar(alerta(titulo="primeiro"))
    for i in range(4):
        rel.avancar(60_000)
        n.notificar(alerta(titulo=f"suprimido {i}"))
    assert n.despejar_resumo()
    resumo = t.enviadas[-1]
    assert "4 alerta(s) não enviados" in resumo
    assert "suprimido 0" in resumo
    assert n.estado()["aguardando_resumo"] == 0


def test_resumo_sem_pendencia_nao_manda_nada():
    n, t, _ = montar()
    assert not n.despejar_resumo()
    assert t.enviadas == []


# =====================================================================
# Falha de entrega
# =====================================================================
def test_falha_nao_derruba_o_ciclo():
    """O sistema precisa continuar gerenciando posição aberta mesmo sem
    conseguir avisar ninguém."""
    n, t, _ = montar()
    t.falhar = True
    assert n.notificar(alerta()) is False        # não levanta
    assert n.estado()["falhas"] == 1


def test_falha_fica_registrada_e_nao_passa_em_branco():
    """Um canal que falha em silêncio é pior que não ter canal."""
    n, t, _ = montar()
    t.falhar = True
    n.notificar(alerta())
    e = n.estado()
    assert not e["saudavel"]
    assert "transporte configurado para falhar" in e["ultima_falha"]["erro"]
    assert "para de olhar o painel" in e["observacao"]


def test_canal_sadio_reporta_saudavel():
    n, _, _ = montar()
    n.notificar(alerta())
    assert n.estado()["saudavel"]
    assert n.estado()["entregues"] == 1


def test_teste_de_canal_prova_que_funciona():
    n, t, _ = montar()
    r = n.testar()
    assert r["ok"]
    assert "não indica nenhum evento de mercado" in t.enviadas[0]


def test_teste_de_canal_reporta_a_falha():
    n, t, _ = montar()
    t.falhar = True
    r = n.testar()
    assert not r["ok"]
    assert r["erro"]


def test_teste_sem_canal_diz_que_nao_ha_canal():
    r = Notificador(None).testar()
    assert not r["ok"]
    assert "nenhum canal externo" in r["motivo"]


# =====================================================================
# Formatação
# =====================================================================
def test_formato_e_curto_e_traz_a_acao_do_sistema():
    texto = formatar(alerta())
    assert "[URGENTE]" in texto
    assert "par: BTCUSDT" in texto
    assert "ação do sistema: novas entradas bloqueadas" in texto
    assert len(texto.splitlines()) <= 5


def test_formato_sem_par_nao_inventa_linha():
    texto = formatar(alerta(symbol=""))
    assert "par:" not in texto


def test_alerta_com_linguagem_promocional_nem_chega_a_ser_criado():
    """A proibição vive no construtor do alerta, não no notificador.

    É o lugar certo: impede que a linguagem apareça também no painel e no
    journal.
    """
    from investai.reporting.alertas import AlertaInvalido
    with pytest.raises(AlertaInvalido):
        Alerta(CategoriaAlerta.OPORTUNIDADE, NivelAlerta.URGENTE,
               "lucro garantido em BTCUSDT", "entre agora")


# =====================================================================
# Segredo
# =====================================================================
def test_telegram_exige_token_e_chat():
    """Um canal pela metade falharia em silêncio."""
    with pytest.raises(ValueError, match="obrigatórios"):
        TransporteTelegram("", "123")
    with pytest.raises(ValueError):
        TransporteTelegram("token", "")


def test_token_nao_aparece_no_repr_nem_na_descricao():
    tg = TransporteTelegram("123456:TOKEN_SUPER_SECRETO", "999")
    assert "TOKEN_SUPER_SECRETO" not in repr(tg)
    assert "TOKEN_SUPER_SECRETO" not in tg.descricao()
    assert "999" in repr(tg)


def test_token_nao_aparece_no_estado_do_notificador():
    n = Notificador(TransporteTelegram("123456:TOKEN_SUPER_SECRETO", "999"))
    assert "TOKEN_SUPER_SECRETO" not in str(n.estado())


def test_erro_do_telegram_nao_cita_a_url():
    """A URL da API carrega o token no caminho."""
    class RespostaRuim:
        status_code = 401

    class ClienteFalso:
        def post(self, url, json):
            return RespostaRuim()

        def close(self):
            pass

    tg = TransporteTelegram("123456:TOKEN_SUPER_SECRETO", "999",
                            client=ClienteFalso())
    with pytest.raises(RuntimeError) as exc:
        tg.enviar("oi")
    assert "TOKEN_SUPER_SECRETO" not in str(exc.value)
    assert "401" in str(exc.value)


def test_telegram_manda_o_texto_para_o_chat_certo():
    visto = {}

    class RespostaBoa:
        status_code = 200

    class ClienteFalso:
        def post(self, url, json):
            visto["url"] = url
            visto["json"] = json
            return RespostaBoa()

        def close(self):
            pass

    TransporteTelegram("tok", "555", client=ClienteFalso()).enviar("mensagem")
    assert visto["json"]["chat_id"] == "555"
    assert visto["json"]["text"] == "mensagem"
    assert visto["json"]["disable_web_page_preview"] is True


def test_sem_variaveis_de_ambiente_nao_ha_canal():
    """Segredo em variável de ambiente, nunca em arquivo do repositório."""
    assert telegram_do_ambiente({}) is None
    assert telegram_do_ambiente({"TELEGRAM_BOT_TOKEN": "t"}) is None
    assert telegram_do_ambiente({"TELEGRAM_CHAT_ID": "c"}) is None


def test_com_as_duas_variaveis_o_canal_existe():
    t = telegram_do_ambiente({"TELEGRAM_BOT_TOKEN": "tok",
                              "TELEGRAM_CHAT_ID": "123"})
    assert t is not None
    assert t.chat_id == "123"


# =====================================================================
# Diagnóstico
# =====================================================================
def test_diagnostico_aponta_canal_ausente(estado_diag):
    from investai.ops.comandos import diagnostico
    d = diagnostico(estado_diag, agora_ms=T0)
    canal = [c for c in d.checagens if c.nome == "canal de alertas"][0]
    assert not canal.ok
    assert "ninguém vê o painel dormindo" in canal.detalhe


def test_diagnostico_aponta_canal_quebrado(estado_diag):
    from investai.ops.comandos import diagnostico
    t = TransporteMemoria()
    t.falhar = True
    estado_diag.notificador = Notificador(t)
    estado_diag.notificador.notificar(alerta())
    d = diagnostico(estado_diag, agora_ms=T0)
    canal = [c for c in d.checagens if c.nome == "canal de alertas"][0]
    assert not canal.ok
    assert "falha" in canal.detalhe


def test_diagnostico_aprova_canal_sadio(estado_diag):
    from investai.ops.comandos import diagnostico
    estado_diag.notificador = Notificador(TransporteMemoria())
    d = diagnostico(estado_diag, agora_ms=T0)
    canal = [c for c in d.checagens if c.nome == "canal de alertas"][0]
    assert canal.ok


@pytest.fixture
def estado_diag(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path
    from investai.api import AppState
    from investai.config import Settings
    origem = Path(__file__).resolve().parent.parent / "data" / "fiis_snapshot.json"
    shutil.copy(origem, tmp_path / "fiis_snapshot.json")
    monkeypatch.setenv("INVESTAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("INVESTAI_SYNTHETIC", "1")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    s = Settings.from_env()
    s.universo = ("BTCUSDT",)
    st = AppState(settings=s, usar_sintetico=True)
    yield st
    st.store.close()
