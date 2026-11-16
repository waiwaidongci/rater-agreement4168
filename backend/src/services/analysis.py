from .validation import validate_config
from .counts import panel_counts
from .fleiss import fleiss
from .bootstrap import row_bootstrap
from .fingerprint import fingerprint
from .categories import DEFAULT_LOCALE


def analyze(study,specimens,votes,config,snapshot=None):
    validate_config(config)
    if not snapshot:
        snapshot=[{'code':entry.get('code'),'name':entry.get('name'),
                   'locale':entry.get('locale',DEFAULT_LOCALE),
                   'term_version':entry.get('term_version')}
                  for entry in (study.get('category_scheme') or [
                      {'code':None,'name':name,'locale':DEFAULT_LOCALE,'term_version':None}
                      for name in study['categories']])]
    rows=panel_counts(study,specimens,votes,snapshot);matrix=[r['counts'] for r in rows];result=fleiss(matrix)
    for row,P in zip(rows,result['item_agreement']):row['agreement']=P
    bootstrap=row_bootstrap(matrix,config['bootstrap_repeats'],config['seed'])
    entries=[dict(entry) for entry in snapshot]
    return {**result,'source_study':study,'categories':[entry['name'] for entry in entries],
            'category_codes':[entry.get('code') for entry in entries],
            'category_entries':entries,
            'category_locale':entries[0].get('locale',DEFAULT_LOCALE) if entries else DEFAULT_LOCALE,
            'raters':study['raters'],'items':rows,'bootstrap':bootstrap,
            'fingerprint':fingerprint(study,specimens,votes,config),
            'interpretation':'Agreement relative to empirical category marginals, not accuracy against a gold standard. Fixed complete three-person panel; bootstrap resamples whole iid specimens, not votes or new raters.'}
