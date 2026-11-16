from fastapi import HTTPException
from src.models import ReviewMemo
from src.services.audit import record
from src.services.categories import DEFAULT_LOCALE,CategoryMappingError,localize_entries
from src.utils.csv_export import csv_response


def _entries(result):
    entries=result.get('category_entries')
    if entries:return entries
    return [{'code':None,'name':name,'locale':DEFAULT_LOCALE,'term_version':None,'legacy':True}
            for name in result.get('categories',[])]


def export_memo(db,id,actor,locale=None):
 memo=db.get(ReviewMemo,id)
 if not memo:raise HTTPException(404,'说明不存在')
 if memo.status!='approved':raise HTTPException(409,'仅批准说明可导出')
 r=memo.result_snapshot;b=r['bootstrap'];ci=b['interval']
 try:localized=localize_entries(db,_entries(r),locale)
 except CategoryMappingError as error:raise HTTPException(422,str(error))
 label=locale or 'original'
 headers=['复核说明','研究','指纹','Fleissκ','Pbar','Pe','有效重复','退化重复','经验区间下限','经验区间上限','试样','逐项P','类别计数','类别编码','类别名称（快照）','类别名称（导出）','名称语言','翻译状态','导出语言','人工解释','下一步','审核意见']
 rows=[[memo.name,r['source_study']['name'],r['fingerprint'],r['kappa'],r['pbar'],r['pe'],b['valid_repeats'],b['invalid_repeats'],ci[0] if ci else '',ci[1] if ci else '',item['reference'],item['agreement'],str(item['counts']),' | '.join(str(e.get('code') or '') for e in localized),' | '.join(str(e.get('name') or '') for e in localized),' | '.join(str(e.get('export_name') or '') for e in localized),' | '.join(str(e.get('name_locale') or e.get('locale') or DEFAULT_LOCALE) for e in localized),' | '.join(str(e.get('translation_status')) for e in localized),label,memo.interpretation,memo.next_step,memo.review_note] for item in r['items']]
 record(db,actor,'memo.exported',{'memo_id':id,'locale':label});return csv_response(f'rater-review-{id}-{label}.csv',rows,headers)
