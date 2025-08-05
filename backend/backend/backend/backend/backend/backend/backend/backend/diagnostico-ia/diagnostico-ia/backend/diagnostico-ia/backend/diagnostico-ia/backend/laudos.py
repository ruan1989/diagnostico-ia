from datetime import datetime

def gerar_laudo(usuario_id, sintomas, possiveis_doencas, tratamentos):
    laudo = {
        "usuario_id": usuario_id,
        "data": datetime.utcnow().isoformat(),
        "sintomas_relatados": sintomas,
        "diagnostico_gerado": possiveis_doencas,
        "tratamentos_recomendados": tratamentos,
        "recomendacoes_gerais": "Procure atendimento médico presencial para confirmação e exames clínicos."
    }
    return laudo
