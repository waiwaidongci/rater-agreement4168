from sqlalchemy.orm import Mapped,mapped_column
from sqlalchemy import String,Text,JSON,DateTime,UniqueConstraint,Index,Integer
from datetime import datetime,timezone
from src.config.database import Base


class CategoryConcept(Base):
    """A stable rock-core class. `code` never changes when display names change."""
    __tablename__='category_concepts'
    id:Mapped[int]=mapped_column(primary_key=True)
    code:Mapped[str]=mapped_column(String(40),unique=True,index=True)
    created_by:Mapped[str]=mapped_column(String(120),default='system')
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))


class CategoryTerm(Base):
    """Versioned display name for a concept/locale. A rename appends a version."""
    __tablename__='category_terms'
    __table_args__=(
        UniqueConstraint('concept_id','locale','version',name='uq_category_term_version'),
        Index('ix_category_terms_lookup','locale','normalized'),
    )
    id:Mapped[int]=mapped_column(primary_key=True)
    concept_id:Mapped[int]=mapped_column(Integer,index=True)
    locale:Mapped[str]=mapped_column(String(16),index=True)
    name:Mapped[str]=mapped_column(String(80))
    normalized:Mapped[str]=mapped_column(String(80),index=True)
    version:Mapped[int]=mapped_column(Integer,default=1)
    created_by:Mapped[str]=mapped_column(String(120),default='system')
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))


class CategoryAlias(Base):
    """Synonym (or retired display name) that resolves to one stable code."""
    __tablename__='category_aliases'
    __table_args__=(
        UniqueConstraint('concept_id','locale','normalized',name='uq_category_alias'),
        Index('ix_category_aliases_lookup','locale','normalized'),
    )
    id:Mapped[int]=mapped_column(primary_key=True)
    concept_id:Mapped[int]=mapped_column(Integer,index=True)
    locale:Mapped[str]=mapped_column(String(16),index=True)
    name:Mapped[str]=mapped_column(String(80))
    normalized:Mapped[str]=mapped_column(String(80),index=True)
    created_by:Mapped[str]=mapped_column(String(120),default='system')
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))


class CategoryQuarantine(Base):
    """An import/migration name that could not be mapped safely. Never dropped silently."""
    __tablename__='category_quarantine'
    __table_args__=(
        UniqueConstraint('fingerprint',name='uq_category_quarantine_fingerprint'),
        Index('ix_category_quarantine_status','status','reason_code'),
    )
    id:Mapped[int]=mapped_column(primary_key=True)
    fingerprint:Mapped[str]=mapped_column(String(64))
    source:Mapped[str]=mapped_column(String(80),default='migration')
    study_id:Mapped[int|None]=mapped_column(Integer,nullable=True,index=True)
    raw_name:Mapped[str]=mapped_column(String(80))
    normalized_name:Mapped[str]=mapped_column(String(80),index=True)
    locale:Mapped[str]=mapped_column(String(16),default='zh-CN')
    reason_code:Mapped[str]=mapped_column(String(40))
    reason:Mapped[str]=mapped_column(Text)
    details:Mapped[dict]=mapped_column(JSON,default=dict)
    status:Mapped[str]=mapped_column(String(16),default='open')
    resolved_code:Mapped[str|None]=mapped_column(String(40),nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
