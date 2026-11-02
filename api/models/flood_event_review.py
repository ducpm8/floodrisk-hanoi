import uuid

from sqlalchemy import Column, Date, DateTime, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from api.models.base import Base


class FloodEventReview(Base):
    """
    Low-confidence LLM extractions awaiting human review before promotion to flood_events.
    """

    __tablename__ = "flood_events_review"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    staging_id = Column(UUID(as_uuid=True), nullable=True)   # FK to raw_flood_staging.id
    location_name = Column(String(255), nullable=True)
    ward = Column(String(100), nullable=True)
    district = Column(String(100), nullable=True)
    lat = Column(Numeric(9, 6), nullable=True)
    lng = Column(Numeric(9, 6), nullable=True)
    event_date = Column(Date, nullable=True)
    depth_cm = Column(Integer, nullable=True)
    depth_description = Column(Text, nullable=True)
    duration_hours = Column(Integer, nullable=True)
    severity = Column(String(20), nullable=True)
    confidence = Column(String(10), nullable=False)           # 'high' | 'medium' | 'low'
    source_type = Column(String(20), nullable=True)
    source_url = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
