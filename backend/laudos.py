from datetime import datetime, timezone


def gerar_laudo(usuario_id: str, sintomas: str, possiveis_doencas: list, tratamentos: dict) -> dict:
    return {
        "usuario_id": usuario_id,
        "data": datetime.now(timezone.utc).isoformat(),
        "sintomas_relatados": sintomas,
        "diagnostico_gerado": possiveis_doencas,
        "tratamentos_recomendados": tratamentos,
        "aviso": (
            "Este diagnóstico é gerado por IA e tem caráter meramente informativo. "
            "Procure atendimento médico presencial para confirmação e exames clínicos."
        ),
    }
