# -*- coding: utf-8 -*-
"""Su GitHub config.json non esiste (contiene credenziali): lo ricostruisce
   con i valori riservati, lasciando intatto il profilo della candidata."""
import io, json, os

QUI = os.path.dirname(os.path.abspath(__file__))
percorso = os.path.join(QUI, "config.json")
if os.path.exists(percorso):
    print("config.json gia' presente: non tocco niente.")
    raise SystemExit(0)

modello = json.load(io.open(os.path.join(QUI, "config.esempio.json"), encoding="utf-8"))
modello["adzuna"]["app_id"] = os.environ.get("ADZUNA_APP_ID", "")
modello["adzuna"]["app_key"] = os.environ.get("ADZUNA_APP_KEY", "")
json.dump(modello, io.open(percorso, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("config.json ricostruito dalle credenziali riservate.")
