#!/usr/bin/env python3
"""Parada de emergência — atalho para `cli.py parar-tudo`.

Trava o envio de ordens, desarma o modo real, para o motor e fecha as
posições a mercado, nessa ordem. A trava é gravada no banco e sobrevive a
reinício: religar exige `liberar-operacao --sim`.

A confirmação `--sim` é obrigatória de propósito. Este comando gasta dinheiro
(fechar a mercado paga spread e taxa) e não deve disparar por engano de
histórico do shell.

    python scripts/emergency_stop.py --sim
    python scripts/emergency_stop.py --sim --manter-posicoes
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cli import main                                    # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["parar-tudo", *sys.argv[1:]]))
