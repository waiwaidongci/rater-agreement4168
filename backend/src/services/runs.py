from sqlalchemy import select
from fastapi import HTTPException
from src.models import AgreementRun
from src.utils.serialize import row
from src.services.categories import (
    DEFAULT_LOCALE,
    CategoryMappingError,
    category_snapshot,
    normalize_locale,
    study_scheme,
)
from .source import source_data
from .validation import validate_source,validate_config
from .audit import record


def create_run(db,data,actor):
    study,items,votes=source_data(db,data.study_id);config=data.model_dump(exclude={'study_id','name','category_locale'})
    try:validate_source(study,items,votes);validate_config(config)
    except ValueError as error:raise HTTPException(422,str(error))
    if db.scalar(select(AgreementRun).where(AgreementRun.name==data.name)):raise HTTPException(409,'运行名已存在')
    try:
        locale=normalize_locale(data.category_locale)
        snapshot=category_snapshot(db,study_scheme(study),locale)
    except CategoryMappingError as error:
        raise HTTPException(422,str(error))
    run=AgreementRun(study_id=data.study_id,name=data.name,config=config,created_by=actor,
                     category_locale=locale,category_snapshot=snapshot);db.add(run);db.commit()
    try:
        from src.worker import analyze_run
        analyze_run.delay(run.id)
    except Exception:
        run.status='failed';run.error='队列不可用，请稍后重建任务';db.commit();raise HTTPException(503,run.error)
    record(db,actor,'run.queued',{'run_id':run.id,'category_locale':locale,
                                  'category_snapshot':[{'code':e.get('code'),'name':e.get('name'),'term_version':e.get('term_version')} for e in snapshot]})
    return row(run)
