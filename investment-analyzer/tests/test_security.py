"""Keystore e endurecimento: segredo não pode vazar em log, disco ou API."""
import json
import os
import stat

import pytest

from investai.exchanges import ApiCredentials, CredentialError, Keystore
from investai.exchanges.bitget import BitgetClient, _assinar
from investai.exchanges.keystore import credenciais_do_ambiente


CRED = ApiCredentials("bg_chave_publica_1234", "segredo_ultra_secreto",
                      "minha_passphrase")


def test_ciclo_completo_salvar_carregar(tmp_path):
    ks = Keystore(tmp_path / "ks.json")
    ks.salvar(CRED, "senha_mestra_forte")
    recuperado = ks.carregar("senha_mestra_forte")
    assert recuperado == CRED


def test_arquivo_nasce_com_permissao_restrita(tmp_path):
    """0600: só o dono lê. Outro usuário na máquina não deve conseguir."""
    caminho = tmp_path / "ks.json"
    Keystore(caminho).salvar(CRED, "senha_mestra_forte")
    modo = stat.S_IMODE(os.stat(caminho).st_mode)
    assert modo == 0o600


def test_segredo_nao_aparece_em_texto_claro_no_disco(tmp_path):
    caminho = tmp_path / "ks.json"
    Keystore(caminho).salvar(CRED, "senha_mestra_forte")
    bruto = caminho.read_text(encoding="utf-8")
    assert "segredo_ultra_secreto" not in bruto
    assert "minha_passphrase" not in bruto
    assert "bg_chave_publica_1234" not in bruto


def test_senha_errada_e_rejeitada(tmp_path):
    ks = Keystore(tmp_path / "ks.json")
    ks.salvar(CRED, "senha_mestra_forte")
    with pytest.raises(CredentialError, match="senha mestra incorreta"):
        ks.carregar("senha_errada_123")


def test_arquivo_corrompido_e_rejeitado(tmp_path):
    caminho = tmp_path / "ks.json"
    ks = Keystore(caminho)
    ks.salvar(CRED, "senha_mestra_forte")
    payload = json.loads(caminho.read_text())
    payload["dados"] = payload["dados"][:-10] + "AAAAAAAAAA"
    caminho.write_text(json.dumps(payload))
    with pytest.raises(CredentialError):
        ks.carregar("senha_mestra_forte")


def test_salt_diferente_a_cada_salvamento(tmp_path):
    """Salt fixo permitiria rainbow table sobre senhas mestras."""
    c1, c2 = tmp_path / "a.json", tmp_path / "b.json"
    Keystore(c1).salvar(CRED, "senha_mestra_forte")
    Keystore(c2).salvar(CRED, "senha_mestra_forte")
    s1 = json.loads(c1.read_text())["kdf"]["salt"]
    s2 = json.loads(c2.read_text())["kdf"]["salt"]
    assert s1 != s2


def test_senha_mestra_curta_recusada(tmp_path):
    with pytest.raises(CredentialError, match="8 caracteres"):
        Keystore(tmp_path / "ks.json").salvar(CRED, "curta")


def test_credencial_incompleta_recusada(tmp_path):
    ks = Keystore(tmp_path / "ks.json")
    with pytest.raises(CredentialError):
        ks.salvar(ApiCredentials("chave", "", "pass"), "senha_mestra_forte")


def test_keystore_ausente_da_erro_claro(tmp_path):
    with pytest.raises(CredentialError, match="não encontrado"):
        Keystore(tmp_path / "nao_existe.json").carregar("senha_mestra_forte")


def test_apagar_keystore(tmp_path):
    ks = Keystore(tmp_path / "ks.json")
    ks.salvar(CRED, "senha_mestra_forte")
    assert ks.apagar() is True
    assert ks.existe is False
    assert ks.apagar() is False


# ----------------------------------------------------- vazamento em log/repr
def test_repr_da_credencial_nao_expoe_segredo():
    """Traceback e log usam repr. Ele não pode conter o secret."""
    texto = repr(CRED)
    assert "segredo_ultra_secreto" not in texto
    assert "minha_passphrase" not in texto
    assert "bg_c" in texto      # prefixo visível para conferência


def test_mascara_mostra_so_as_pontas():
    m = CRED.mascara()
    assert m.startswith("bg_c") and m.endswith("1234")
    assert "publica" not in m


def test_mascara_de_chave_curta_nao_vaza_nada():
    assert ApiCredentials("abc", "s", "p").mascara() == "***"


def test_credenciais_do_ambiente_exige_os_tres_campos(monkeypatch):
    monkeypatch.setenv("BITGET_API_KEY", "chave_teste_123")
    monkeypatch.setenv("BITGET_API_SECRET", "secret_teste_123")
    assert credenciais_do_ambiente() is None     # falta passphrase
    monkeypatch.setenv("BITGET_API_PASSPHRASE", "pass")
    assert credenciais_do_ambiente() is not None


# ------------------------------------------------------------- assinatura
def test_assinatura_e_deterministica_e_sensivel():
    a = _assinar("secret", "1700000000000", "GET", "/api/v2/teste", "")
    b = _assinar("secret", "1700000000000", "GET", "/api/v2/teste", "")
    c = _assinar("secret", "1700000000001", "GET", "/api/v2/teste", "")
    d = _assinar("outro", "1700000000000", "GET", "/api/v2/teste", "")
    assert a == b
    assert a != c and a != d


def test_assinatura_cobre_o_corpo():
    """Se o corpo ficasse fora da assinatura, ele poderia ser alterado."""
    sem = _assinar("s", "1", "POST", "/p", "")
    com = _assinar("s", "1", "POST", "/p", '{"size":"1"}')
    assert sem != com


def test_endpoint_privado_sem_credencial_e_bloqueado_antes_da_rede():
    from investai.exchanges.base import InsufficientPermissions
    cliente = BitgetClient(credenciais=None)
    with pytest.raises(InsufficientPermissions):
        cliente._headers("GET", "/api/v2/mix/account/accounts", "")


def test_verificar_credenciais_sem_chave_nao_faz_requisicao():
    assert BitgetClient(credenciais=None).verificar_credenciais()["ok"] is False


def test_timeframe_nao_suportado_e_rejeitado_localmente():
    with pytest.raises(ValueError, match="timeframe"):
        BitgetClient().candles("BTCUSDT", "3m")
