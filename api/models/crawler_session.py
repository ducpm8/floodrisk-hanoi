from sqlalchemy import Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid

from api.models.base import Base


class CrawlerSession(Base):
    """
    Persistent cookie store for social media crawlers.
    One row per source (facebook, instagram). Upserted on each successful crawl.
    """

    __tablename__ = "crawler_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_name = Column(String(50), nullable=False, unique=True)
    cookies_encrypted = Column(Text, nullable=True)
    last_valid_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), nullable=False, default="active")  # active/expired/blocked
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
