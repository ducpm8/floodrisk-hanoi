import uuid

from sqlalchemy import Boolean, Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from api.models.base import Base


class RawFloodStaging(Base):
    """
    Staging row for unprocessed flood content (social post or news article).
    ai_extraction_worker reads processed=False rows and sets processed=True when done.
    """

    __tablename__ = "raw_flood_staging"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content = Column(Text, nullable=False)
    source_type = Column(String(20), nullable=False)   # 'social_media' | 'news' | 'governance' | 'manual' | 'youtube'
    source_url = Column(Text, nullable=True)
    post_timestamp = Column(DateTime(timezone=True), nullable=True)
    processed = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    external_id = Column(Text, nullable=True)   # Facebook post ID, YouTube video ID, etc.
