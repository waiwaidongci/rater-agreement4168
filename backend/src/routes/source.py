from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from src.config.database import get_db
from src.controllers.dependencies import current_user,writers,admins
from src.models import AgreementStudy,Specimen,RaterVote
from src.schemas.study import StudyInput
from src.schemas.vote import VotePatch
from src.schemas.specimen import SpecimenPatch
from src.schemas.category import AliasInput,RenameInput,QuarantineResolveInput
from src.services.source import create_study,edit_vote,edit_item,add_study_alias,resolve_entry,study_detail
from src.services.categories import (
    DEFAULT_LOCALE,CategoryMappingError,registry,rename_concept,quarantine_rows,
    normalize_locale,study_scheme,
)
from src.services.audit import record
from src.utils.serialize import row
router=APIRouter()
@router.get('/studies')
def studies(db:Session=Depends(get_db),user=Depends(current_user)):return [row(s) for s in db.scalars(select(AgreementStudy).order_by(AgreementStudy.id))]
@router.get('/studies/{id}')
def study(id:int,db:Session=Depends(get_db),user=Depends(current_user)):return study_detail(db,id)
@router.post('/studies')
def create(data:StudyInput,db:Session=Depends(get_db),user=Depends(writers),locale:str=DEFAULT_LOCALE):return create_study(db,data,user.email,locale)
@router.post('/studies/{id}/aliases')
def alias(id:int,data:AliasInput,db:Session=Depends(get_db),user=Depends(writers)):return add_study_alias(db,id,data,user.email)
@router.get('/specimens')
def items(db:Session=Depends(get_db),user=Depends(current_user)):return [row(s) for s in db.scalars(select(Specimen).order_by(Specimen.id))]
@router.patch('/specimens/{id}')
def item(id:int,data:SpecimenPatch,db:Session=Depends(get_db),user=Depends(writers)):return edit_item(db,id,data,user.email)
@router.get('/votes')
def votes(db:Session=Depends(get_db),user=Depends(current_user)):return [row(v) for v in db.scalars(select(RaterVote).order_by(RaterVote.specimen_id,RaterVote.rater))]
@router.patch('/votes/{id}')
def vote(id:int,data:VotePatch,db:Session=Depends(get_db),user=Depends(writers)):return edit_vote(db,id,data,user.email)
@router.get('/categories')
def categories(db:Session=Depends(get_db),user=Depends(current_user)):return {'locales':{'zh-CN':'简体中文','en':'English'},'concepts':registry(db)}
@router.post('/categories/{code}/rename')
def rename(code:str,data:RenameInput,db:Session=Depends(get_db),user=Depends(admins)):
 try:
  result=rename_concept(db,code,data.name,data.locale or DEFAULT_LOCALE,user.email)
 except CategoryMappingError as error:
  raise HTTPException(422,str(error))
 record(db,user.email,'category.renamed',result);return result
@router.get('/categories/quarantine')
def quarantined(db:Session=Depends(get_db),user=Depends(current_user),include_resolved:bool=False):
 return {'items':[row(q) for q in quarantine_rows(db,include_resolved)]}
@router.post('/categories/quarantine/{id}/resolve')
def resolve(id:int,data:QuarantineResolveInput,db:Session=Depends(get_db),user=Depends(admins)):return resolve_entry(db,id,data,user.email)
@router.post('/categories/migrate')
def migrate(db:Session=Depends(get_db),user=Depends(admins)):
 from src.services.migration import migrate_all
 summary=migrate_all(db);record(db,user.email,'category.migrated',summary);return summary
@router.get('/categories/verify')
def category_verify(db:Session=Depends(get_db),user=Depends(current_user)):
 from src.services.migration import verify
 return verify(db)
