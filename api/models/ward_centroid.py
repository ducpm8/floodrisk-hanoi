from geoalchemy2 import Geography
from sqlalchemy import Column, Numeric, String

from api.models.base import Base


class WardCentroid(Base):
    """
    Pre-seeded Hanoi ward centroids (579 wards from OSM Overpass).
    Used by the search API to resolve ward name → lat/lng → ST_DWithin query.

    ward_name is the normalized (ASCII, lowercase) form used for lookup.
    ward_name_vi is the display name with Vietnamese diacritics.
    """

    __tablename__ = "ward_centroids"

    ward_name = Column(String(100), primary_key=True)   # normalized: "dong da"
    ward_name_vi = Column(String(100), nullable=False)  # display: "Đống Đa"
    district = Column(String(100), nullable=False)
    lat = Column(Numeric(9, 6), nullable=False)
    lng = Column(Numeric(9, 6), nullable=False)
    geom = Column(Geography(geometry_type="POINT", srid=4326), nullable=False)
