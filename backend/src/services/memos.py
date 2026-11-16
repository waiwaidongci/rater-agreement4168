from sqlalchemy import select
from fastapi import HTTPException
from src.models import AgreementRun,ReviewMemo
from src.utils.serialize import row
from src.services.categories import DEFAULT_LOCALE,decorate_result,study_scheme
from .audit import record

def create_memo(db,data,actor):
 run=db.get(AgreementRun,data.run_id)
 if not run or run.status!='completed':raise HTTPException(422,'仅成功运行可建立口径复核说明')
 if db.scalar(select(ReviewMemo).where(ReviewMemo.name==data.name)):raise HTTPException(409,'说明名已存在')
 snapshot=run.result
 if snapshot and not snapshot.get('category_entries'):
  from src.models import AgreementStudy
  study=db.get(AgreementStudy,run.study_id)
  snapshot=decorate_result(db,snapshot,study_scheme(row(study)) if study else [])
 memo=ReviewMemo(**data.model_dump(),result_snapshot=snapshot,created_by=actor);db.add(memo);db.commit();record(db,actor,'memo.created',{'memo_id':memo.id,'category_codes':[e.get('code') for e in (snapshot or {}).get('category_entries',[])]});return row(memo)

def approve_memo(db,id,data,actor):
 memo=db.scalar(select(ReviewMemo).where(ReviewMemo.id==id).with_for_update())
 if not memo:raise HTTPException(404,'说明不存在')
 if memo.status!='draft':raise HTTPException(409,'说明已审阅，不可重复审批')
 memo.status=data.status;memo.review_note=data.review_note;db.commit();record(db,actor,'memo.reviewed',{'memo_id':id,'status':memo.status});return row(memo)
