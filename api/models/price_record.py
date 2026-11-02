import uuid

from sqlalchemy import BigInteger, Boolean, Column, Date, DateTime, Enum, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from api.models.base import Base


class PriceRecord(Base):
    """
    Monthly-aggregated price data per ward, sourced from batdongsan.com.vn.
    data_caveat must always be surfaced in the UI — these are listing prices,
    not confirmed sale prices.
    """

    __tablename__ = "price_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ward = Column(String(100))                    # normalized ward name
    district = Column(String(100))
    property_type = Column(
        Enum("apartment", "house", "land", name="property_type")
    )
    price_per_m2_vnd = Column(BigInteger)
    recorded_month = Column(Date)                 # first day of month
    last_updated = Column(DateTime(timezone=True))
    source = Column(String(255))
    source_url = Column(Text, nullable=True)
    data_caveat = Column(
        Text, default="Listing price, not confirmed sale price"
    )
    is_seed = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
