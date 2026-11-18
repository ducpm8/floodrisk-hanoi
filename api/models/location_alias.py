from sqlalchemy import Column, Float, Numeric, String

from api.models.base import Base


class LocationAlias(Base):
    __tablename__ = "location_aliases"

    alias_norm = Column(String, primary_key=True)
    alias_vi = Column(String)
    ward_name = Column(String(100), nullable=False)
    alias_type = Column(String(20), nullable=False)
    confidence = Column(Float, nullable=False, default=1.0)
    source = Column(String(20), nullable=False)
    # Optional precise coordinates for landmark/apartment aliases (migration 020).
    # NULL means "use the ward centroid".
    lat = Column(Numeric(9, 6), nullable=True)
    lng = Column(Numeric(9, 6), nullable=True)
