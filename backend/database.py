from pymongo import MongoClient
import os

_client = None


def _get_client() -> MongoClient:
    global _client
    if _client is None:
        uri = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
        _client = MongoClient(uri)
    return _client


def get_db():
    return _get_client()["diagnostico_saude"]


db = _get_client()["diagnostico_saude"]
usuarios_collection = db["usuarios"]
historico_collection = db["historico"]
