"""Guarda credenciais de API cifradas em disco.

Regras que este módulo impõe:

* A SENHA DA CONTA BITGET NUNCA É USADA. Automação na Bitget funciona com
  chave de API (key + secret + passphrase). Crie a chave com permissão de
  leitura e trade e SEM permissão de saque/transferência.
* O arquivo é cifrado (Fernet/AES-128-CBC + HMAC) com chave derivada por
  scrypt a partir de uma senha mestra que só você conhece.
* O arquivo nasce com permissão 0600 e o segredo nunca é logado nem
  devolvido pela API HTTP — só o prefixo da key, para conferência.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

# 2**15 * 8 * 128 = 32 MiB de trabalho por tentativa: caro para força bruta,
# irrelevante para o uso legítimo (uma derivação por login).
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1


@dataclass(frozen=True, slots=True)
class ApiCredentials:
    api_key: str
    api_secret: str
    passphrase: str

    def mascara(self) -> str:
        if len(self.api_key) <= 8:
            return "*" * len(self.api_key)
        return f"{self.api_key[:4]}...{self.api_key[-4:]}"

    def __repr__(self) -> str:  # evita vazar segredo em traceback/log
        return f"ApiCredentials(api_key={self.mascara()!r}, secret=***, passphrase=***)"


class CredentialError(RuntimeError):
    pass


def _derive(senha: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=32, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return base64.urlsafe_b64encode(kdf.derive(senha.encode("utf-8")))


class Keystore:
    def __init__(self, caminho: str | Path):
        self.caminho = Path(caminho)

    @property
    def existe(self) -> bool:
        return self.caminho.exists()

    def salvar(self, cred: ApiCredentials, senha_mestra: str) -> None:
        if not senha_mestra or len(senha_mestra) < 8:
            raise CredentialError("senha mestra deve ter ao menos 8 caracteres")
        if not (cred.api_key and cred.api_secret and cred.passphrase):
            raise CredentialError("api_key, api_secret e passphrase são obrigatórios")
        salt = secrets.token_bytes(16)
        token = Fernet(_derive(senha_mestra, salt)).encrypt(
            json.dumps({
                "api_key": cred.api_key,
                "api_secret": cred.api_secret,
                "passphrase": cred.passphrase,
            }).encode("utf-8")
        )
        payload = {
            "versao": 1,
            "kdf": {"algoritmo": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R,
                    "p": SCRYPT_P, "salt": base64.b64encode(salt).decode()},
            "dados": token.decode(),
        }
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.caminho.with_suffix(".tmp")
        # cria já com 0600 para não existir janela de arquivo legível
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, self.caminho)
        os.chmod(self.caminho, 0o600)

    def carregar(self, senha_mestra: str) -> ApiCredentials:
        if not self.existe:
            raise CredentialError(f"keystore não encontrado em {self.caminho}")
        payload = json.loads(self.caminho.read_text(encoding="utf-8"))
        kdf = payload["kdf"]
        salt = base64.b64decode(kdf["salt"])
        chave = _derive(senha_mestra, salt)
        try:
            dados = json.loads(Fernet(chave).decrypt(payload["dados"].encode()))
        except InvalidToken as exc:
            raise CredentialError("senha mestra incorreta ou arquivo corrompido") from exc
        return ApiCredentials(dados["api_key"], dados["api_secret"], dados["passphrase"])

    def apagar(self) -> bool:
        if self.existe:
            self.caminho.unlink()
            return True
        return False


def credenciais_do_ambiente() -> ApiCredentials | None:
    """Alternativa ao keystore: variáveis de ambiente (útil em servidor/Docker)."""
    key = os.environ.get("BITGET_API_KEY", "").strip()
    secret = os.environ.get("BITGET_API_SECRET", "").strip()
    passphrase = os.environ.get("BITGET_API_PASSPHRASE", "").strip()
    if key and secret and passphrase:
        return ApiCredentials(key, secret, passphrase)
    return None
