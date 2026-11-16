from sqlalchemy import select
from fastapi import HTTPException
from src.models import AgreementStudy,Specimen,RaterVote
from src.utils.serialize import row
from src.services.categories import (
    DEFAULT_LOCALE,
    CategoryMappingError,
    add_alias,
    concept_for_code,
    current_term,
    ensure_concept,
    normalize_locale,
    resolve,
    resolve_quarantine,
    resolve_vote_category,
    study_scheme,
)
from .validation import validate_source
from .audit import record


def _scheme_row(entry):
    return {'code':entry['code'],'name':entry['name'],'locale':entry['locale'],
            'term_version':entry['term_version'],'declared_name':entry['declared_name']}


def create_study(db,data,actor,locale=DEFAULT_LOCALE):
    if db.scalar(select(AgreementStudy).where(AgreementStudy.name==data.name)):raise HTTPException(409,'研究名已存在')
    try:
        locale=normalize_locale(locale)
        seen_codes={}
        for name in data.categories:
            concept,_,_=ensure_concept(db,name,locale,actor)
            if concept.code in seen_codes:
                raise CategoryMappingError('duplicate_mapping',f'类别 {name!r} 与 {seen_codes[concept.code]!r} 映射到同一编码 {concept.code}')
            seen_codes[concept.code]=name
        db.flush()
        scheme=[]
        for name in data.categories:
            found=resolve(db,name,locale)
            concept=concept_for_code(db,found['code']) if found['status']=='resolved' else None
            term=current_term(db,concept.id,locale) if concept else None
            if concept is None or term is None:
                raise CategoryMappingError('unknown_category',f'类别 {name!r} 无法解析到稳定编码')
            scheme.append({'code':concept.code,'name':term.name,'locale':locale,
                           'term_version':term.version,'declared_name':name})
        source=data.model_dump(exclude={'specimens'})
        source['categories']=[entry['declared_name'] for entry in scheme]
        source['category_scheme']=scheme
        declared={entry['declared_name']:entry for entry in scheme}
        if len(declared)!=len(scheme):raise CategoryMappingError('duplicate_mapping','类别声明重复')
        by_code={entry['code']:entry for entry in scheme}
        vote_entries={}
        for s in data.specimens:
            for v in s.votes:
                entry=declared.get(v.category)
                if entry is None:
                    found=resolve(db,v.category,locale)
                    entry=by_code.get(found.get('code')) if found['status']=='resolved' else None
                if entry is None:
                    raise CategoryMappingError('unknown_category',f'未知类别 {v.category!r}；导入名称须在声明类别或其同义名中')
                vote_entries[(id(s),id(v))]=entry
        items=[{'id':i+1,'reference':s.reference,'description':s.description} for i,s in enumerate(data.specimens)]
        votes=[dict(v.model_dump(),specimen_id=i+1,category_code=vote_entries[(id(s),id(v))]['code'])
               for i,s in enumerate(data.specimens) for v in s.votes]
        validate_source(source,items,votes)
        study=AgreementStudy(**source);db.add(study);db.flush()
        for s in data.specimens:
            item=Specimen(study_id=study.id,reference=s.reference,description=s.description);db.add(item);db.flush()
            for v in s.votes:
                code=vote_entries[(id(s),id(v))]['code']
                db.add(RaterVote(specimen_id=item.id,rater=v.rater,category=v.category,category_code=code))
        db.commit()
        record(db,actor,'study.imported',{'study_id':study.id,'specimens':len(items),
                                          'category_codes':[entry['code'] for entry in scheme]})
        return row(study)
    except CategoryMappingError as error:
        db.rollback();raise HTTPException(422,str(error))
    except ValueError as error:
        db.rollback();raise HTTPException(422,str(error))


def source_data(db,id):
    study=db.get(AgreementStudy,id)
    if not study:raise HTTPException(404,'研究不存在')
    items=list(db.scalars(select(Specimen).where(Specimen.study_id==id).order_by(Specimen.reference)));ids=[s.id for s in items];votes=list(db.scalars(select(RaterVote).where(RaterVote.specimen_id.in_(ids)).order_by(RaterVote.specimen_id,RaterVote.rater)))
    payload=row(study)
    payload['category_scheme']=study_scheme(payload)
    return payload,[row(s) for s in items],[row(v) for v in votes]


def study_detail(db,id):
    study=db.get(AgreementStudy,id)
    if not study:raise HTTPException(404,'研究不存在')
    payload=row(study);scheme=study_scheme(payload);payload['categories']=study.categories
    payload['category_scheme']=scheme
    return payload


def edit_vote(db,id,data,actor):
    vote=db.get(RaterVote,id)
    if not vote:raise HTTPException(404,'票不存在')
    item=db.get(Specimen,vote.specimen_id);study_row=row(db.get(AgreementStudy,item.study_id))
    try:
        entry=resolve_vote_category(db,study_row,data.category)
    except CategoryMappingError as error:
        raise HTTPException(422,str(error))
    if not entry:raise HTTPException(422,'类别不在预先声明列表或其同义名')
    old=vote.category;old_code=vote.category_code
    vote.category=entry.get('name');vote.category_code=entry.get('code')
    db.commit();record(db,actor,'vote.revised',{'vote_id':id,'old':old,'old_code':old_code,
                                               'new':vote.category,'new_code':vote.category_code,'input':data.category})
    return row(vote)


def add_study_alias(db,study_id,data,actor):
    study=db.get(AgreementStudy,study_id)
    if not study:raise HTTPException(404,'研究不存在')
    try:
        result=add_alias(db,data.code,data.name,data.locale or DEFAULT_LOCALE,actor)
    except CategoryMappingError as error:
        db.rollback();raise HTTPException(422,str(error))
    record(db,actor,'category.alias_added',{'study_id':study_id,**result})
    return result


def resolve_entry(db,id,data,actor):
    try:
        item=resolve_quarantine(db,id,data.code,actor)
    except CategoryMappingError as error:
        raise HTTPException(422,str(error))
    record(db,actor,'category.quarantine_resolved',{'quarantine_id':id,'code':data.code})
    return row(item)


def edit_item(db,id,data,actor):
    item=db.get(Specimen,id)
    if not item:raise HTTPException(404,'试样不存在')
    item.description=data.description;db.commit();record(db,actor,'specimen.annotated',{'specimen_id':id});return row(item)
