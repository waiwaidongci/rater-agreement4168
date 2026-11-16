from src.services.validation import validate_votes


def panel_counts(study,specimens,votes,snapshot=None):
    scheme,positions=validate_votes(study,specimens,votes)
    ordered=sorted(specimens,key=lambda s:s['reference']);lookup={s['id']:{} for s in ordered}
    for vote in votes:lookup[vote['specimen_id']][vote['rater']]=vote
    entries=snapshot or scheme;names=[entry.get('name') for entry in entries[:len(scheme)]]
    rows=[]
    for specimen in ordered:
        labels=lookup[specimen['id']];counts=[0]*len(scheme)
        for rater in study['raters']:
            vote=labels[rater];counts[positions[id(vote)]]+=1
        row_votes=[]
        for rater in study['raters']:
            vote=labels[rater];position=positions[id(vote)]
            row_votes.append({'rater':rater,'category':names[position],
                              'category_code':scheme[position].get('code'),
                              'source_category':vote['category']})
        rows.append({'id':specimen['id'],'reference':specimen['reference'],
                     'description':specimen['description'],'counts':counts,'votes':row_votes})
    return rows
