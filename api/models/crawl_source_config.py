from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from api.models.base import Base


class CrawlSourceConfig(Base):
    __tablename__ = "crawl_source_config"

    id = Column(Integer, primary_key=True)
    source_name = Column(String(50), unique=True, nullable=False)
    source_type = Column(String(20), nullable=False)   # governance | news | social
    source_url = Column(String(500), nullable=False)
    crawler_class = Column(String(100), nullable=False)
    is_enabled = Column(Boolean, nullable=False, default=True)
    last_crawled_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)
    updated_by = Column(String(100), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
