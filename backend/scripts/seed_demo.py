import json
import numpy as np
from sqlalchemy import select,func
from src.config.database import Base,engine,SessionLocal
from src.models import User,AgreementStudy,Specimen,RaterVote,AgreementRun,ReviewMemo
from src.utils.security import hash_password
from src.utils.serialize import row
from src.services.analysis import analyze
from src.services.categories import create_concept,registry

CATEGORY_TERMS=[
 ('砂岩',{'en':'Sandstone'},['砂质岩','砂岩类']),
 ('粉砂岩',{'en':'Siltstone'},['粉砂质岩']),
 ('泥岩',{'en':'Mudstone'},['泥质岩','页岩']),
 ('碳酸盐岩',{'en':'Carbonate rock'},['碳酸盐']),
]

def ensure_categories(actor='system'):
 with SessionLocal() as db:
  from src.services.categories import add_alias,concept_for_code,current_term,registry
  for name,translations,aliases in CATEGORY_TERMS:
   found=[c for c in registry(db) if (c['names'].get('zh-CN') or {}).get('name')==name]
   if found:
    code=found[0]['code']
   else:
    code=create_concept(db,name,'zh-CN',actor,translations).code;db.commit()
   # Add English and synonyms if a pre-existing concept lacked them.
   concept=concept_for_code(db,code)
   if current_term(db,concept.id,'en') is None:
    from src.services.categories import rename_concept
    rename_concept(db,code,translations['en'],'en',actor)
   for alias in aliases:
    try:add_alias(db,code,alias,'zh-CN',actor)
    except Exception:pass
  db.commit()

def seed():
 Base.metadata.create_all(engine)
 ensure_categories()
 with SessionLocal() as db:
  for role,name in [('admin','纪岚 · 图谱研究负责人'),('analyst','秦砚 · 标记分析员'),('viewer','陶野 · 口径观察员')]:
   if not db.scalar(select(User).where(User.email==f'{role}@shanxu.demo')):db.add(User(email=f'{role}@shanxu.demo',name=name,role=role,password_hash=hash_password('Shanxu2026!')))
  db.commit();rng=np.random.default_rng(231);names=['SX-31A 多类图谱评议','SX-31B 类别失衡评议','SX-31C 单类边界评议'];categories=['砂岩','粉砂岩','泥岩','碳酸盐岩'];raters=['标记者甲','标记者乙','标记者丙']
  from src.services.categories import concept_for_code, current_term
  concept_rows={c['code']:c for c in registry(db)}
  code_by_name={c['names']['zh-CN']['name']:c['code'] for c in concept_rows.values()}
  term_by_name={name:current_term(db,concept_for_code(db,code_by_name[name]).id,'zh-CN') for name in categories}
  scheme=[{'code':code_by_name[name],'name':name,'locale':'zh-CN','term_version':term_by_name[name].version,'declared_name':name} for name in categories]
  for index,name in enumerate(names):
   if db.scalar(select(AgreementStudy).where(AgreementStudy.name==name)):continue
   study=AgreementStudy(name=name,filename=f'sx-core-panel-{index+1}-202611.json',description=['固定面板独立标记，类别较均衡','主类占比高；机会基线须一起读','全单类Pe=1，κ未定义，不当作κ1'][index],categories=categories,category_scheme=scheme,raters=raters);db.add(study);db.flush()
   for i in range(120):
    item=Specimen(study_id=study.id,reference=f'SX{index+1}-IMG-{i+1:03d}',description='匿名独立岩芯图像编号；非真实现场样本');db.add(item);db.flush();latent=int(rng.choice(4,p=[.25]*4 if index==0 else [.88,.04,.04,.04])) if index<2 else 0
    for rater in raters:
     label=latent if index==2 or rng.random()<(.84 if index==0 else .94) else int(rng.choice([c for c in range(4) if c!=latent]));db.add(RaterVote(specimen_id=item.id,rater=rater,category=categories[label],category_code=code_by_name[categories[label]]))
   db.commit()
  run=db.scalar(select(AgreementRun).where(AgreementRun.name=='A研究固定面板口径核对'))
  if not run:
   study=db.scalar(select(AgreementStudy).where(AgreementStudy.name==names[0]));items=[row(s) for s in db.scalars(select(Specimen).where(Specimen.study_id==study.id))];ids=[s['id'] for s in items];votes=[row(v) for v in db.scalars(select(RaterVote).where(RaterVote.specimen_id.in_(ids)))];config={'bootstrap_repeats':400,'seed':231,'panel_scope':'fixed_three_raters'};snapshot=[{'code':entry['code'],'name':entry['name'],'locale':'zh-CN','term_version':entry['term_version']} for entry in study.category_scheme];result=analyze(row(study),items,votes,config,snapshot=snapshot);run=AgreementRun(study_id=study.id,name='A研究固定面板口径核对',config=config,status='completed',result=result,kappa=result['kappa'],pbar=result['pbar'],pe=result['pe'],created_by='analyst@shanxu.demo',category_locale='zh-CN',category_snapshot=snapshot);db.add(run);db.commit()
  if not db.scalar(select(ReviewMemo).where(ReviewMemo.name=='A研究分类边界复核初稿')):db.add(ReviewMemo(run_id=run.id,name='A研究分类边界复核初稿',interpretation='κ是相对类别基率的一致性，不是与金标准对照的准确率。',next_step='固定原三位标记者，独立复核争议试样的类别定义与图谱边界。',result_snapshot=run.result,created_by='analyst@shanxu.demo'));db.commit()
  counts={m.__tablename__:db.scalar(select(func.count()).select_from(m)) for m in [User,AgreementStudy,Specimen,RaterVote,AgreementRun,ReviewMemo]};print(json.dumps(counts));return counts
if __name__=='__main__':seed()
