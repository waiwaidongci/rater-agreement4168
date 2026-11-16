import os

os.environ['JWT_SECRET'] = 'test-secret'
os.environ['DATABASE_URL'] = 'sqlite+pysqlite:///:memory:'
os.environ['SEED_DEMO'] = 'false'

import csv
import io
import json
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.config.database import Base, get_db
from src.models import (
    AgreementRun,
    AgreementStudy,
    CategoryAlias,
    CategoryConcept,
    CategoryQuarantine,
    CategoryTerm,
    RaterVote,
    ReviewMemo,
    Specimen,
    User,
)
from src.services.analysis import analyze
from src.services.categories import registry
from src.services.migration import migrate_all, verify
from src.utils.security import hash_password
import src.main as main


@pytest.fixture()
def env(monkeypatch):
    engine = create_engine('sqlite+pysqlite://', connect_args={'check_same_thread': False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)

    class FakeRedis:
        values = {}

        def __init__(self, *args, **kwargs):
            pass

        @classmethod
        def from_url(cls, *args, **kwargs):
            return cls()

        def incr(self, key):
            FakeRedis.values[key] = FakeRedis.values.get(key, 0) + 1
            return FakeRedis.values[key]

        def expire(self, key, seconds):
            return True

    monkeypatch.setattr('src.middlewares.rate_limit.Redis', FakeRedis)
    monkeypatch.setattr('src.worker.analyze_run.delay', lambda run_id: None, raising=False)

    def override_db():
        with Session() as db:
            yield db

    main.app.dependency_overrides[get_db] = override_db
    with Session() as db:
        for role in ('admin', 'analyst', 'viewer'):
            db.add(User(email=f'{role}@x.demo', name=role, role=role,
                        password_hash=hash_password('Secret2026!')))
        db.commit()
    client = TestClient(main.app)
    yield {'client': client, 'Session': Session}

    main.app.dependency_overrides.clear()


def token(client, email):
    response = client.post('/auth/login', json={'email': email, 'password': 'Secret2026!'})
    assert response.status_code == 200, response.text
    return {'Authorization': 'Bearer ' + response.json()['token']}


def legacy_study(Session, categories=('砂岩', '泥岩'), votes=None, name='legacy'):
    with Session() as db:
        study = AgreementStudy(name=name, filename='legacy.json', description='',
                               categories=list(categories), raters=['A', 'B', 'C'])
        db.add(study)
        db.flush()
        rows = votes or [['砂岩', '砂岩', '砂岩'], ['砂岩', '砂岩', '泥岩'],
                         ['砂岩', '泥岩', '泥岩'], ['泥岩', '泥岩', '泥岩']]
        for index, labels in enumerate(rows):
            specimen = Specimen(study_id=study.id, reference=f'L-{index}')
            db.add(specimen)
            db.flush()
            for rater, label in zip(['A', 'B', 'C'], labels):
                db.add(RaterVote(specimen_id=specimen.id, rater=rater, category=label))
        db.commit()
        return study.id


def counts(Session):
    with Session() as db:
        return {model.__tablename__: len(db.scalars(select(model)).all())
                for model in (CategoryConcept, CategoryTerm, CategoryAlias,
                              CategoryQuarantine, RaterVote, AgreementRun, ReviewMemo)}


def _complete_run(Session, run_id):
    """Simulate the worker finishing a queued run with its frozen snapshot."""
    from src.services.source import source_data

    with Session() as db:
        run = db.get(AgreementRun, run_id)
        study, items, votes = source_data(db, run.study_id)
        result = analyze(study, items, votes, run.config, snapshot=run.category_snapshot)
        run.result = result
        run.kappa = result['kappa']
        run.pbar = result['pbar']
        run.pe = result['pe']
        run.status = 'completed'
        db.commit()


def test_legacy_rows_readable_before_and_after_migration(env):
    Session, client = env['Session'], env['client']
    study_id = legacy_study(Session)
    analyst = token(client, 'analyst@x.demo')

    before = client.get(f'/studies/{study_id}', headers=analyst).json()
    assert before['category_scheme'][0]['code'] is None
    assert before['categories'] == ['砂岩', '泥岩']

    with Session() as db:
        study = db.get(AgreementStudy, study_id)
        assert study.category_scheme is None
        assert all(vote.category_code is None for vote in db.scalars(select(RaterVote)))

    admin = token(client, 'admin@x.demo')
    first = client.post('/categories/migrate', headers=admin).json()
    assert first['migrated'] == 1 and first['votes_coded'] == 12 and first['failed'] == 0

    after = client.get(f'/studies/{study_id}', headers=analyst).json()
    assert [entry['name'] for entry in after['category_scheme']] == ['砂岩', '泥岩']
    assert all(entry['code'] for entry in after['category_scheme'])
    with Session() as db:
        votes = list(db.scalars(select(RaterVote)))
        assert all(vote.category == '砂岩' or vote.category == '泥岩' for vote in votes)
        assert all(vote.category_code for vote in votes)


def test_migration_rerun_is_idempotent(env):
    Session, client = env['Session'], env['client']
    legacy_study(Session)
    admin = token(client, 'admin@x.demo')
    first = client.post('/categories/migrate', headers=admin).json()
    snapshot = counts(Session)
    with Session() as db:
        codes = {vote.id: vote.category_code for vote in db.scalars(select(RaterVote))}

    second = client.post('/categories/migrate', headers=admin).json()
    assert second['migrated'] == 0 and second['unchanged'] == 1
    assert second['votes_coded'] == 0 and second['quarantined'] == 0
    assert counts(Session) == snapshot
    with Session() as db:
        assert {vote.id: vote.category_code for vote in db.scalars(select(RaterVote))} == codes
    assert first['migrated'] == 1

    third = client.post('/categories/migrate', headers=admin).json()
    assert third['unchanged'] == 1 and counts(Session) == snapshot


def test_rename_only_affects_new_runs(env):
    Session, client = env['Session'], env['client']
    study_id = legacy_study(Session)
    admin = token(client, 'admin@x.demo')
    analyst = token(client, 'analyst@x.demo')
    client.post('/categories/migrate', headers=admin)

    with Session() as db:
        study = db.get(AgreementStudy, study_id)
        scheme = study.category_scheme
        old_entry = dict(scheme[0])
        items = [{'id': s.id, 'reference': s.reference, 'description': s.description}
                 for s in db.scalars(select(Specimen))]
        ids = [item['id'] for item in items]
        votes = [{'id': v.id, 'specimen_id': v.specimen_id, 'rater': v.rater,
                  'category': v.category, 'category_code': v.category_code}
                 for v in db.scalars(select(RaterVote).where(RaterVote.specimen_id.in_(ids)))]
        study_row = {'name': study.name, 'filename': study.filename,
                     'description': study.description, 'categories': study.categories,
                     'category_scheme': scheme, 'raters': study.raters}
        old_result = analyze(study_row, items, votes,
                             {'bootstrap_repeats': 400, 'seed': 231,
                              'panel_scope': 'fixed_three_raters'},
                             snapshot=[{'code': e['code'], 'name': e['name'],
                                        'locale': 'zh-CN', 'term_version': e['term_version']}
                                       for e in scheme])
        run = AgreementRun(study_id=study_id, name='old-run',
                           config={'bootstrap_repeats': 400, 'seed': 231,
                                   'panel_scope': 'fixed_three_raters'},
                           status='completed', result=old_result,
                           kappa=old_result['kappa'], pbar=old_result['pbar'],
                           pe=old_result['pe'], created_by='analyst@x.demo',
                           category_locale='zh-CN',
                           category_snapshot=old_result['category_entries'])
        db.add(run)
        db.commit()
        run_id = run.id
        memo = ReviewMemo(run_id=run_id, name='approved-memo',
                          interpretation='手工解释说明文本，长度足够。',
                          next_step='独立复核争议试样与图谱边界。',
                          result_snapshot=old_result, status='draft',
                          created_by='analyst@x.demo')
        db.add(memo)
        db.commit()
        memo_id = memo.id

    response = client.post(f'/categories/{old_entry["code"]}/rename',
                           json={'name': '石英砂岩', 'locale': 'zh-CN'}, headers=admin)
    assert response.status_code == 200 and response.json()['version'] == 2
    assert client.post(f'/categories/{old_entry["code"]}/rename',
                       json={'name': '石英砂岩', 'locale': 'zh-CN'},
                       headers=analyst).status_code == 403

    historical_response = client.get(f'/runs/{run_id}', headers=analyst)
    assert historical_response.status_code == 200, historical_response.text
    historical = historical_response.json()
    assert historical['result']['categories'][0] == '砂岩'
    assert historical['result']['category_entries'][0]['name'] == '砂岩'
    assert historical['result']['category_entries'][0]['term_version'] == old_entry['term_version']

    created = client.post('/runs', json={'study_id': study_id, 'name': 'new-run',
                                         'bootstrap_repeats': 400, 'seed': 231},
                          headers=analyst)
    assert created.status_code == 200, created.text
    new_run = created.json()
    assert new_run['category_snapshot'][0]['name'] == '石英砂岩'
    assert new_run['category_snapshot'][0]['term_version'] == 2
    assert new_run['category_snapshot'][0]['code'] == old_entry['code']

    approved = client.patch(f'/memos/{memo_id}/approval',
                            json={'status': 'approved', 'review_note': '同意，保留原名称。'},
                            headers=admin)
    assert approved.status_code == 200
    memo_after = client.get('/memos', headers=analyst).json()[0]
    assert memo_after['result_snapshot']['categories'][0] == '砂岩'
    assert memo_after['result_snapshot']['category_entries'][0]['name'] == '砂岩'


def test_synonyms_resolve_to_one_code_and_conflicts_are_isolated(env):
    Session, client = env['Session'], env['client']
    admin = token(client, 'admin@x.demo')
    analyst = token(client, 'analyst@x.demo')
    with Session() as db:
        from src.services.categories import add_alias, create_concept
        sandstone = create_concept(db, '砂岩', 'zh-CN', 'seed', {'en': 'Sandstone'})
        mudstone = create_concept(db, '泥岩', 'zh-CN', 'seed', {'en': 'Mudstone'})
        db.commit()
        add_alias(db, sandstone.code, '砂质岩', 'zh-CN', 'seed')
        add_alias(db, mudstone.code, '泥质岩', 'zh-CN', 'seed')
        sandstone_code, mudstone_code = sandstone.code, mudstone.code

    payload = {'name': '含同义名的新导入', 'filename': 'syn.json',
               'description': '', 'categories': ['砂岩', '泥岩'], 'raters': ['A', 'B', 'C'],
               'specimens': [
                   {'reference': 'S-1', 'votes': [{'rater': 'A', 'category': '砂岩'},
                                                  {'rater': 'B', 'category': '砂质岩'},
                                                  {'rater': 'C', 'category': '砂岩'}]},
                   {'reference': 'S-2', 'votes': [{'rater': 'A', 'category': '泥质岩'},
                                                  {'rater': 'B', 'category': '泥岩'},
                                                  {'rater': 'C', 'category': '泥岩'}]}]}
    first = client.post('/studies', json=payload, headers=analyst)
    assert first.status_code == 200, first.text
    study_id = first.json()['id']
    scheme = client.get(f'/studies/{study_id}', headers=analyst).json()['category_scheme']
    assert [entry['code'] for entry in scheme] == [sandstone_code, mudstone_code]
    with Session() as db:
        votes = list(db.scalars(select(RaterVote)))
        stored = {(vote.category, vote.category_code) for vote in votes}
        assert stored == {('砂岩', sandstone_code), ('砂质岩', sandstone_code),
                          ('泥岩', mudstone_code), ('泥质岩', mudstone_code)}

    alias = client.post(f'/studies/{study_id}/aliases',
                        json={'code': sandstone_code, 'name': '石英砂屑岩', 'locale': 'zh-CN'},
                        headers=analyst)
    assert alias.status_code == 200 and alias.json()['created'] is True
    repeat = client.post(f'/studies/{study_id}/aliases',
                         json={'code': sandstone_code, 'name': '石英砂屑岩', 'locale': 'zh-CN'},
                         headers=analyst)
    assert repeat.json()['created'] is False

    conflicting = client.post(f'/studies/{study_id}/aliases',
                              json={'code': sandstone_code, 'name': '泥岩', 'locale': 'zh-CN'},
                              headers=analyst)
    assert conflicting.status_code == 422

    # Unknown vote category on import is rejected with an explanation.
    bad = dict(payload, name='未知类别导入')
    bad['specimens'] = [{'reference': 'S-1', 'votes': [
        {'rater': 'A', 'category': '未知岩'}, {'rater': 'B', 'category': '砂岩'},
        {'rater': 'C', 'category': '砂岩'}]}, payload['specimens'][1]]
    rejected = client.post('/studies', json=bad, headers=analyst)
    assert rejected.status_code == 422 and '未知类别' in rejected.json()['detail']


def test_quarantine_unknown_and_duplicate_mappings(env):
    Session, client = env['Session'], env['client']
    admin = token(client, 'admin@x.demo')

    # Two concepts share a synonym by construction (simulating bad legacy data).
    with Session() as db:
        from src.services.categories import create_concept
        one = create_concept(db, '砂岩', 'zh-CN', 'seed')
        two = create_concept(db, '泥岩', 'zh-CN', 'seed')
        db.add(CategoryAlias(concept_id=one.id, locale='zh-CN', name='混称',
                             normalized='混称', created_by='seed'))
        db.add(CategoryAlias(concept_id=two.id, locale='zh-CN', name='混称',
                             normalized='混称', created_by='seed'))
        db.commit()
        study = AgreementStudy(name='坏映射', filename='bad.json', description='',
                               categories=['混称', '泥岩'], raters=['A', 'B', 'C'])
        db.add(study)
        db.flush()
        rows = [['混称', '混称', '神秘岩'], ['泥岩', '泥岩', '泥岩'], ['混称', '泥岩', '泥岩'],
                ['泥岩', '混称', '混称']]
        for index, labels in enumerate(rows):
            specimen = Specimen(study_id=study.id, reference=f'B-{index}')
            db.add(specimen)
            db.flush()
            for rater, label in zip(['A', 'B', 'C'], labels):
                db.add(RaterVote(specimen_id=specimen.id, rater=rater, category=label))
        db.commit()
        study_id = study.id

    first = client.post('/categories/migrate', headers=admin).json()
    assert first['partial'] == 1 and first['failed'] == 0
    assert first['quarantined'] >= 2

    queue = client.get('/categories/quarantine', headers=admin).json()['items']
    reasons = {(item['reason_code'], item['raw_name']) for item in queue}
    assert ('ambiguous_mapping', '混称') in reasons
    assert ('unknown_category', '神秘岩') in reasons
    assert ('unmapped_concept', '混称') in reasons
    item = next(item for item in queue if item['reason_code'] == 'unknown_category')
    assert item['status'] == 'open'

    before = counts(Session)
    second = client.post('/categories/migrate', headers=admin).json()
    assert counts(Session) == before
    assert second['quarantined'] == 0 and second['partial'] == 1

    with Session() as db:
        bad_study = db.get(AgreementStudy, study_id)
        assert {entry['code'] for entry in bad_study.category_scheme} == {None, bad_study.category_scheme[1]['code']}
        unknown_votes = [v for v in db.scalars(select(RaterVote)) if v.category == '神秘岩']
        assert unknown_votes and all(v.category_code is None for v in unknown_votes)
        assert all(v.category == '神秘岩' for v in unknown_votes)

    # Old data remains readable even while the mapping is isolated.
    detail = client.get(f'/studies/{study_id}', headers=admin).json()
    assert detail['categories'] == ['混称', '泥岩']

    # Resolving requires a real code and keeps the original row for audit.
    with Session() as db:
        code = db.scalar(select(CategoryConcept.code))
    resolved = client.post(f'/categories/quarantine/{item["id"]}/resolve',
                           json={'code': code}, headers=admin)
    assert resolved.status_code == 200 and resolved.json()['status'] == 'resolved'
    again = client.post(f'/categories/quarantine/{item["id"]}/resolve',
                        json={'code': code}, headers=admin)
    assert again.status_code == 200 and again.json()['resolved_code'] == code


def test_cross_language_export_uses_frozen_versions(env):
    Session, client = env['Session'], env['client']
    admin = token(client, 'admin@x.demo'),
    admin = admin[0]
    analyst = token(client, 'analyst@x.demo')
    payload = {'name': '双语导出研究', 'filename': 'bi.json', 'description': '',
               'categories': ['砂岩', '泥岩'], 'raters': ['A', 'B', 'C'],
               'specimens': [
                   {'reference': 'E-1', 'votes': [{'rater': 'A', 'category': '砂岩'},
                                                  {'rater': 'B', 'category': '砂岩'},
                                                  {'rater': 'C', 'category': '泥岩'}]},
                   {'reference': 'E-2', 'votes': [{'rater': 'A', 'category': '泥岩'},
                                                  {'rater': 'B', 'category': '泥岩'},
                                                  {'rater': 'C', 'category': '泥岩'}]}]}
    study_id = client.post('/studies', json=payload, headers=analyst).json()['id']
    scheme = client.get(f'/studies/{study_id}', headers=analyst).json()['category_scheme']
    # Add English names explicitly.
    from src.services.categories import rename_concept
    with Session() as db:
        rename_concept(db, scheme[0]['code'], 'Sandstone', 'en', 't')
        rename_concept(db, scheme[1]['code'], 'Mudstone', 'en', 't')

    run = client.post('/runs', json={'study_id': study_id, 'name': 'bi-run',
                                     'bootstrap_repeats': 400, 'seed': 231,
                                     'category_locale': 'en'}, headers=analyst).json()
    assert [entry['name'] for entry in run['category_snapshot']] == ['Sandstone', 'Mudstone']
    assert run['category_locale'] == 'en'
    _complete_run(Session, run['id'])

    memo = client.post('/memos', json={'run_id': run['id'], 'name': 'bi-memo',
                                       'interpretation': '双语导出核对用说明文本。',
                                       'next_step': '核对导出名称与编码对应关系。'},
                       headers=analyst).json()
    assert memo.get('id'), memo
    assert client.patch(f'/memos/{memo["id"]}/approval',
                        json={'status': 'approved', 'review_note': '同意双语导出核对。'},
                        headers=admin).status_code == 200

    # Rename Chinese *after* the snapshot; the frozen export must not change.
    client.post(f'/categories/{scheme[0]["code"]}/rename',
                json={'name': '石英砂岩', 'locale': 'zh-CN'}, headers=admin)

    response = client.get(f'/memos/{memo["id"]}/export?locale=en', headers=analyst)
    assert response.status_code == 200
    rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
    header = rows[0]
    names = header.index('类别名称（导出）')
    codes = header.index('类别编码')
    snapshot_names = header.index('类别名称（快照）')
    status_index = header.index('翻译状态')
    assert rows[1][names] == 'Sandstone | Mudstone'
    assert rows[1][snapshot_names] == 'Sandstone | Mudstone'
    assert all(code.startswith('CAT-') for code in rows[1][codes].split(' | '))
    assert set(value for row in rows[1:] for value in row[status_index].split(' | ')) == {'translated'}

    original = client.get(f'/memos/{memo["id"]}/export', headers=analyst)
    rows_original = list(csv.reader(io.StringIO(original.content.decode('utf-8-sig'))))
    assert rows_original[1][names] == 'Sandstone | Mudstone'
    assert rows_original[1][rows_original[0].index('导出语言')] == 'original'

    chinese = client.get(f'/memos/{memo["id"]}/export?locale=zh-CN', headers=analyst)
    rows_zh = list(csv.reader(io.StringIO(chinese.content.decode('utf-8-sig'))))
    # Frozen version 1 of the Chinese name is used, not the post-rename label.
    assert rows_zh[1][names] == '砂岩 | 泥岩'
    assert all(value == 'translated' for value in rows_zh[1][status_index].split(' | '))

    bad = client.get(f'/memos/{memo["id"]}/export?locale=fr', headers=analyst)
    assert bad.status_code == 422


def test_failure_recovery_resumes_without_duplicates(env, monkeypatch):
    Session, client = env['Session'], env['client']
    admin = token(client, 'admin@x.demo')
    first_id = legacy_study(Session, name='study-one')
    second_id = legacy_study(Session, categories=('粉砂岩', '页岩'), name='study-two',
                             votes=[['粉砂岩', '粉砂岩', '粉砂岩'],
                                    ['粉砂岩', '页岩', '页岩'],
                                    ['页岩', '页岩', '页岩']])
    with Session() as db:
        # The second study declares an alias that already belongs to study one's
        # concept only after the first migration; force an error mid-way.
        pass

    import src.services.migration as migration

    calls = {'count': 0}
    original = migration._legacy_scheme

    def flaky(db, study, quarantined):
        calls['count'] += 1
        if calls['count'] == 2:
            raise RuntimeError('simulated failure on second study')
        return original(db, study, quarantined)

    monkeypatch.setattr(migration, '_legacy_scheme', flaky)
    failed = client.post('/categories/migrate', headers=admin).json()
    assert failed['failed'] == 1 and failed['migrated'] == 1
    assert failed['failures'][0]['study_id'] == second_id
    with Session() as db:
        assert db.get(AgreementStudy, first_id).category_scheme
        assert db.get(AgreementStudy, second_id).category_scheme is None
        assert any(item.reason_code == 'migration_error'
                   for item in db.scalars(select(CategoryQuarantine)))

    monkeypatch.setattr(migration, '_legacy_scheme', original)
    after_first = counts(Session)
    recovered = client.post('/categories/migrate', headers=admin).json()
    assert recovered['failed'] == 0 and recovered['migrated'] == 1
    assert recovered['unchanged'] == 1
    after_second = counts(Session)
    # The recovered study adds its own two concepts, but the already-migrated
    # study is untouched: no duplicate rows for the same names.
    assert after_second['category_concepts'] == after_first['category_concepts'] + 2
    assert after_second['category_terms'] == after_first['category_terms'] + 2
    assert after_second['category_aliases'] == after_first['category_aliases'] + 2
    with Session() as db:
        names = [term.normalized for term in db.scalars(select(CategoryTerm))]
        assert len(names) == len(set(names)) == 4

    rerun = client.post('/categories/migrate', headers=admin).json()
    assert rerun['unchanged'] == 2 and rerun['migrated'] == 0
    assert counts(Session) == after_second
    report = client.get('/categories/verify', headers=admin).json()
    assert all(study['legacy'] is False for study in report['studies'])
    assert all(study['uncoded_votes'] == 0 for study in report['studies'])


def test_unknown_legacy_name_stays_readable_and_is_not_rewritten(env):
    Session, client = env['Session'], env['client']
    admin = token(client, 'admin@x.demo')
    study_id = legacy_study(Session, votes=[['砂岩', '砂岩', '外星岩'],
                                            ['砂岩', '泥岩', '泥岩'],
                                            ['泥岩', '泥岩', '泥岩']])
    client.post('/categories/migrate', headers=admin)
    with Session() as db:
        vote = db.scalar(select(RaterVote).where(RaterVote.category == '外星岩'))
        assert vote.category == '外星岩' and vote.category_code is None
        assert db.get(AgreementStudy, study_id).category_scheme
    report = client.get('/categories/verify', headers=admin).json()
    entry = next(s for s in report['studies'] if s['study_id'] == study_id)
    assert entry['uncoded_votes'] == 1
    assert report['open_quarantine'] >= 1
