from fastapi import APIRouter,Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from src.config.database import get_db
from src.controllers.dependencies import current_user,writers,admins
from src.models import AgreementRun,AgreementStudy,ReviewMemo
from src.schemas.memo import MemoInput,ApprovalInput
from src.services.memos import create_memo,approve_memo
from src.services.categories import decorate_result,study_scheme
from src.controllers.export import export_memo
from src.utils.serialize import row
router=APIRouter()

def _decorated(db,memo):
    payload=row(memo)
    snapshot=payload.get('result_snapshot') or {}
    run=db.get(AgreementRun,memo.run_id)
    study=db.get(AgreementStudy,run.study_id) if run else None
    payload['result_snapshot']=decorate_result(db,snapshot,study_scheme(row(study)) if study else [])
    return payload

@router.get('/memos')
def memos(db:Session=Depends(get_db),user=Depends(current_user)):return [_decorated(db,m) for m in db.scalars(select(ReviewMemo).order_by(ReviewMemo.id.desc()))]
@router.post('/memos')
def create(data:MemoInput,db:Session=Depends(get_db),user=Depends(writers)):return create_memo(db,data,user.email)
@router.patch('/memos/{id}/approval')
def approve(id:int,data:ApprovalInput,db:Session=Depends(get_db),user=Depends(admins)):return approve_memo(db,id,data,user.email)
@router.get('/memos/{id}/export')
def export(id:int,db:Session=Depends(get_db),user=Depends(current_user),locale:str|None=None):return export_memo(db,id,user.email,locale)
