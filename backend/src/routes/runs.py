from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from src.config.database import get_db
from src.controllers.dependencies import current_user,writers
from src.models import AgreementRun
from src.schemas.run import RunInput
from src.services.runs import create_run
from src.services.categories import decorate_result,study_scheme
from src.utils.serialize import row
router=APIRouter()


def _with_categories(db,payload):
    from src.models import AgreementStudy
    scheme=[]
    if payload.get('study_id'):
        study_row=db.get(AgreementStudy,payload['study_id'])
        scheme=study_scheme(row(study_row)) if study_row else []
    payload=dict(payload)
    if payload.get('result') is not None:
        payload['result']=decorate_result(db,payload['result'],scheme)
    if payload.get('category_snapshot') and not (payload.get('result') or {}).get('category_entries'):
        payload['category_entries']=payload['category_snapshot']
    if not payload.get('category_locale'):payload['category_locale']='zh-CN'
    return payload


@router.get('/runs')
def runs(db:Session=Depends(get_db),user=Depends(current_user)):return [_with_categories(db,row(r)) for r in db.scalars(select(AgreementRun).order_by(AgreementRun.id.desc()))]
@router.get('/runs/{id}')
def one(id:int,db:Session=Depends(get_db),user=Depends(current_user)):
 run=db.get(AgreementRun,id)
 if not run:raise HTTPException(404,'运行不存在')
 return _with_categories(db,row(run))
@router.post('/runs')
def create(data:RunInput,db:Session=Depends(get_db),user=Depends(writers)):return create_run(db,data,user.email)
