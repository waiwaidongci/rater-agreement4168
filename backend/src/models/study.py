from sqlalchemy.orm import Mapped,mapped_column
from sqlalchemy import String,JSON,Text
from src.config.database import Base
class AgreementStudy(Base):
 __tablename__='agreement_studies'
 id:Mapped[int]=mapped_column(primary_key=True)
 name:Mapped[str]=mapped_column(String(150),unique=True)
 filename:Mapped[str]=mapped_column(String(180))
 description:Mapped[str]=mapped_column(Text,default='')
 categories:Mapped[list]=mapped_column(JSON)
 category_scheme:Mapped[list|None]=mapped_column(JSON,nullable=True)
 raters:Mapped[list]=mapped_column(JSON)
