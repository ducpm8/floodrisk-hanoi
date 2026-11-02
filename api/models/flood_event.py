import uuid

from geoalchemy2 import Geography
from sqlalchemy import Boolean, Column, Date, DateTime, Enum, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from api.models.base import Base


class FloodEvent(Base):
    """
    A single recorded flood event at a specific location in Hanoi.
    Sourced from thoatnuochanoi.vn (official), news archives, or community reports.

    geom is stored as GEOGRAPHY(POINT, 4326) so ST_DWithin measures in meters.
    """

    __tablename__ = "flood_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    location_name = Column(String(255))           # Vietnamese name as scraped
    ward = Column(String(100))                    # normalized ward name
    district = Column(String(100))
    lat = Column(Numeric(9, 6))
    lng = Column(Numeric(9, 6))
    geom = Column(Geography(geometry_type="POINT", srid=4326))
    event_date = Column(Date, nullable=False)
    severity = Column(Enum("minor", "moderate", "severe", name="flood_severity"))
    depth_cm = Column(Integer, nullable=True)
    duration_hours = Column(Integer, nullable=True)
    source = Column(Enum("official", "news", "community", name="flood_source"))
    source_url = Column(Text, nullable=True)
    is_seed = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    cause = Column(Text, nullable=True)    # weather trigger extracted by Haiku; NULL for governance source
