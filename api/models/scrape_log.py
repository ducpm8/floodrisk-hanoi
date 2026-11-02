from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from api.models.base import Base


class ScrapeLog(Base):
    """
    One row per scraper run. Tracks source, timing, record count, errors.
    Append-only — never updated after 'finished_at' is set.
    """

    __tablename__ = "scrape_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(50), nullable=False)          # 'official', 'news_vnexpress', 'news_tuoitre'
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    records_added = Column(Integer, default=0)
    errors = Column(Text, nullable=True)                 # JSON-encoded list of error strings
    status = Column(String(20), nullable=False, default="running")  # running/success/partial/failed
