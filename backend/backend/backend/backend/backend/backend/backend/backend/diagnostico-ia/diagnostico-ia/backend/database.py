from pymongo import MongoClient

client = MongoClient("mongodb+srv://<usuario>:<senha>@<cluster>.mongodb.net/?retryWrites=true&w=majority")
db = client["diagnostico_saude"]

usuarios_collection = db["usuarios"]
historico_collection = db["historico"]
