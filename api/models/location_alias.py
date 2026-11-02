from sqlalchemy import Column, Float, String

from api.models.base import Base


class LocationAlias(Base):
    __tablename__ = "location_aliases"

    alias_norm = Column(String, primary_key=True)
    alias_vi = Column(String)
    ward_name = Column(String(100), nullable=False)
    alias_type = Column(String(20), nullable=False)
    confidence = Column(Float, nullable=False, default=1.0)
    source = Column(String(20), nullable=False)
