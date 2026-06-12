import json
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
CONFIG_FILE = DATA_DIR / "config.json"
CERT_DIR = DATA_DIR / "cert"
XML_DIR = DATA_DIR / "xmls"
DB_PATH = DATA_DIR / "sefaz.db"


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    (XML_DIR / "nfe").mkdir(parents=True, exist_ok=True)
    (XML_DIR / "cte").mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {
        "cnpj": "",
        "cert_filename": "",
        "cert_password": "",
        "schedule_hour": 7,
        "schedule_minute": 0,
        "ambiente": "1",  # 1=producao, 2=homologacao
        "uf_code": "43",  # RS=43
        "sync_nfe": True,
        "sync_cte": True,
    }


def save_config(data: dict):
    CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def get_cert_path() -> Path | None:
    cfg = load_config()
    if cfg.get("cert_filename"):
        p = CERT_DIR / cfg["cert_filename"]
        return p if p.exists() else None
    return None
