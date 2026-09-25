#!/usr/bin/env python3
"""Diagnóstico do sistema — atalho para `cli.py diagnostico`.

Existe como arquivo próprio porque é o comando que alguém procura às três da
manhã, quando não lembra a sintaxe do subcomando.

Códigos de saída, pensados para cron e monitoramento:
    0  nenhum bloqueio, nenhum aviso
    1  há bloqueio: o sistema NÃO deve operar real
    2  só avisos: pode operar, mas leia cada um
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cli import main                                    # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["diagnostico", *sys.argv[1:]]))
