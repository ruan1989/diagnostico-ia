from transformers import pipeline

# Carrega pipeline de NLP pré-treinado (você pode trocar por modelo médico mais robusto depois)
class NLPDiagnostico:
    def __init__(self):
        self.qa_pipeline = pipeline("question-answering", model="distilbert-base-uncased-distilled-squad")

    def responder_pergunta(self, pergunta, contexto):
        resposta = self.qa_pipeline(question=pergunta, context=contexto)
        return resposta['answer']
