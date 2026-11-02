import uuid

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from api.models.base import Base


class CrawlRun(Base):
    __tablename__ = "crawl_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_name = Column(String(50), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)
    records_fetched = Column(Integer, nullable=False, default=0)
    records_new = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False)   # running | success | partial | failed
    error_message = Column(Text, nullable=True)
