#!/usr/bin/env python3
"""Status operacional — atalho para `cli.py status`.

Responde, sem abrir navegador: está operando? com qual estratégia e em que
fase? quanto risco está exposto? há ordem com destino desconhecido?

Saída 2 quando algo exige atenção (trava ativa ou envio pendente), para que
um cron consiga alertar sem interpretar texto.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cli import main                                    # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["status", *sys.argv[1:]]))
