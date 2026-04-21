
"""
Receptor simples de eventos Iceberg do Quantower.
Salva os dados em arquivos JSONL para treinar ML.
"""
from fastapi import FastAPI
from pydantic import BaseModel
from typing import List
import json
import os
from datetime import datetime
import uvicorn

app = FastAPI(title="Iceberg Receiver")

class IcebergEvent(BaseModel):
    symbol: str
    timestamp: str
    price: float
    side: str
    iceberg_type: str
    probability: float
    peak_size: float
    executed_size: float
    refill_count: int
    time_since_trade_ms: float

DATA_DIR = "C:/data/iceberg"
os.makedirs(DATA_DIR, exist_ok=True)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/iceberg/events")
def receive_events(events: List[IcebergEvent]):
    if not events:
        return {"received": 0, "saved": False}
    
    # Salvar em arquivo JSONL
    date_str = datetime.now().strftime("%Y%m%d")
    # Remove caracteres invalidos para Windows: / \ : * ? " < > |
    symbol = events[0].symbol.replace("/", "_").replace(" ", "_").replace(":", "_")
    file_path = os.path.join(DATA_DIR, f"iceberg_{symbol}_{date_str}.jsonl")
    
    with open(file_path, "a") as f:
        for e in events:
            f.write(json.dumps(e.model_dump()) + "\n")
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Recebidos {len(events)} eventos de {events[0].symbol} -> {file_path}")
    return {"received": len(events), "saved": True, "file": file_path}

if __name__ == "__main__":
    print("=" * 50)
    print("ICEBERG RECEIVER - Aguardando dados do Quantower")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=8002)
