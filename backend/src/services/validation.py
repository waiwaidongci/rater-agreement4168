def _entry_name(entry):
    return entry.get('declared_name') or entry.get('name')


def validate_scheme(study):
    scheme=study.get('category_scheme') or [
        {'code': None, 'name': name, 'declared_name': name, 'locale': 'zh-CN', 'term_version': None}
        for name in study['categories']
    ]
    categories=[entry.get('declared_name') or entry.get('name') for entry in scheme]
    raters=study['raters']
    if not 2<=len(categories)<=6 or len(set(categories))!=len(categories) or any(not isinstance(c,str) or not c.strip() or len(c)>80 for c in categories):raise ValueError('类别须2..6个唯一非空声明，顺序固定')
    if len(scheme)!=len(categories):raise ValueError('类别编码表与声明顺序长度不一致')
    codes=[entry.get('code') for entry in scheme if entry.get('code')]
    if len(codes)!=len(set(codes)):raise ValueError('同一研究内两个类别映射到相同稳定编码')
    if len(raters)!=3 or len(set(raters))!=3 or any(not isinstance(r,str) or not r.strip() or len(r)>80 for r in raters):raise ValueError('须声明3位唯一固定标记者')
    return scheme


def _vote_position(vote,scheme):
    code=vote.get('category_code');name=vote['category']
    if code:
        positions=[i for i,entry in enumerate(scheme) if entry.get('code')==code]
        if len(positions)!=1:raise ValueError('未知或重复的类别编码')
        return positions[0]
    positions=[i for i,entry in enumerate(scheme) if name in (_entry_name(entry),entry.get('name'))]
    if len(positions)!=1:raise ValueError('未知类别或同义名映射不唯一')
    return positions[0]


def validate_votes(study,specimens,votes):
    scheme=validate_scheme(study);raters=study['raters']
    if not 2<=len(specimens)<=240 or len({s['id'] for s in specimens})!=len(specimens) or len({s['reference'] for s in specimens})!=len(specimens):raise ValueError('须2..240个唯一试样')
    lookup={s['id']:[] for s in specimens};positions={}
    for v in votes:
        if v['specimen_id'] not in lookup or v['rater'] not in raters:raise ValueError('未知试样/类别/标记者')
        positions[id(v)]=_vote_position(v,scheme)
        lookup[v['specimen_id']].append(v['rater'])
    for names in lookup.values():
        if len(names)!=3 or len(set(names))!=3 or set(names)!=set(raters):raise ValueError('每试样须同一固定3人面板各一票，不可缺票/重复')
    return scheme,positions


def validate_source(study,specimens,votes):
    validate_votes(study,specimens,votes)


def validate_config(config):
    b=config['bootstrap_repeats'];seed=config['seed']
    if type(b) is not int or not 400<=b<=1000 or type(seed) is not int or not 0<=seed<=1000000000 or config.get('panel_scope')!='fixed_three_raters':raise ValueError('B须400..1000，seed合法且固定三人面板')
