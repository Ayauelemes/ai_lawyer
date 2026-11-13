from dataclasses import dataclass, field
from datetime import datetime
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet


def kz_today():
    return datetime.now(ZoneInfo("Asia/Almaty")).date()


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default="sqlite:///local-data/legal.db", repr=False)
    data_key: str = field(default="", repr=False)
    llm_url: str = ""
    llm_model: str = ""
    llm_key: str = field(default="", repr=False)
    embedding_model: str = ""
    allow_case_transfer: bool = False
    pdf_font: str = ""
    ocr_languages: str = "rus+kaz+eng"

    @classmethod
    def from_env(cls):
        return cls(
            database_url=os.getenv("ZH_DATABASE_URL", "sqlite:///local-data/legal.db"),
            data_key=os.getenv("ZH_DATA_KEY", ""),
            llm_url=os.getenv("ZH_LLM_URL", ""),
            llm_model=os.getenv("ZH_LLM_MODEL", ""),
            llm_key=os.getenv("ZH_LLM_KEY", ""),
            embedding_model=os.getenv("ZH_EMBEDDING_MODEL", ""),
            allow_case_transfer=os.getenv("ZH_ALLOW_CASE_TRANSFER") == "1",
            pdf_font=os.getenv("ZH_PDF_FONT", ""),
            ocr_languages=os.getenv("ZH_OCR_LANGUAGES", "rus+kaz+eng"),
        )

    def cipher(self):
        key = self.data_key
        if not key and Path("local-data/data.key").is_file():
            key = Path("local-data/data.key").read_text().strip()
        if not key:
            raise RuntimeError("Set ZH_DATA_KEY or run zhkh-legal init")
        return Fernet(key.encode("ascii"))
