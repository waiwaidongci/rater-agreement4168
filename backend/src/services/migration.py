"""Idempotent migration of legacy category names to stable codes.

Rules
-----
* A study that already has a complete ``category_scheme`` and coded votes is
  left byte-for-byte untouched (re-running changes no row).
* Legacy studies get concepts/terms/aliases and ``vote.category_code``
  backfilled by *name*; displayed names, run results and approved memos are
  never rewritten.
* Names that are unknown (present on a vote but not declared) or that map to
  more than one concept are recorded once in ``category_quarantine`` with a
  reason. They keep their original name and remain readable.
* Each study is committed independently. A failure rolls back that study;
  re-running resumes and does not duplicate concepts or aliases.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.models import AgreementStudy, CategoryConcept, CategoryQuarantine, RaterVote, Specimen
from src.services.categories import (
    DEFAULT_LOCALE,
    CategoryMappingError,
    create_concept,
    current_term,
    quarantine,
    resolve,
    _add_alias_row,
)


@dataclass
class StudyOutcome:
    study_id: int
    status: str
    codes: dict[str, str] = field(default_factory=dict)
    votes_coded: int = 0
    quarantined: int = 0
    reasons: list[str] = field(default_factory=list)


def _quarantine(db: Session, study_id: int, raw: str, reason_code: str, reason: str,
                details: dict | None = None) -> bool:
    return quarantine(db, source='migration', study_id=study_id, raw_name=raw,
                      locale=DEFAULT_LOCALE, reason_code=reason_code, reason=reason,
                      details=details)


def _legacy_scheme(db: Session, study: AgreementStudy, quarantined: list[str]) -> list[dict]:
    """Assign codes to each declared category. Ambiguity is isolated, not guessed."""
    scheme: list[dict] = []
    used: dict[str, str] = {}

    def quarantined_entry(name: str) -> dict:
        return {'code': None, 'name': name, 'locale': DEFAULT_LOCALE,
                'term_version': None, 'declared_name': name}

    for name in study.categories or []:
        found = resolve(db, name, DEFAULT_LOCALE)
        if found['status'] == 'ambiguous':
            reason = f"类别 {name!r} 同时匹配多个现有编码，无法确定稳定映射"
            if _quarantine(db, study.id, name, 'ambiguous_mapping', reason,
                           {'codes': found['codes']}):
                quarantined.append(reason)
            scheme.append(quarantined_entry(name))
            continue

        if found['status'] == 'unknown':
            concept = create_concept(db, name, DEFAULT_LOCALE, 'migration')
        else:
            concept = db.get(CategoryConcept, found['concept_id'])
            try:
                _add_alias_row(db, concept.id, DEFAULT_LOCALE, name, 'migration')
            except CategoryMappingError as error:
                if _quarantine(db, study.id, name, error.reason_code, str(error), error.details):
                    quarantined.append(str(error))
                scheme.append(quarantined_entry(name))
                continue

        if concept.code in used:
            reason = f"类别 {name!r} 与 {used[concept.code]!r} 映射到同一编码 {concept.code}"
            if _quarantine(db, study.id, name, 'duplicate_mapping', reason,
                           {'code': concept.code, 'declared': used[concept.code]}):
                quarantined.append(reason)
            scheme.append(quarantined_entry(name))
            continue

        term = current_term(db, concept.id, DEFAULT_LOCALE)
        used[concept.code] = name
        scheme.append({'code': concept.code, 'name': term.name if term else name,
                       'locale': DEFAULT_LOCALE,
                       'term_version': term.version if term else None,
                       'declared_name': name})
    return scheme


def migrate_study(db: Session, study: AgreementStudy) -> StudyOutcome:
    specimen_ids = [row[0] for row in db.execute(
        select(Specimen.id).where(Specimen.study_id == study.id)).all()]
    votes = list(db.scalars(select(RaterVote).where(
        RaterVote.specimen_id.in_(specimen_ids)))) if specimen_ids else []

    changed = False
    quarantined: list[str] = []
    if not study.category_scheme:
        study.category_scheme = _legacy_scheme(db, study, quarantined)
        changed = True

    scheme = study.category_scheme or []
    by_name: dict[str, dict] = {}
    for entry in scheme:
        for key in (entry.get('declared_name'), entry.get('name')):
            if key:
                by_name.setdefault(key, entry)
    declared_names = sorted(by_name)

    coded = 0
    for vote in votes:
        if vote.category_code:
            continue
        entry = by_name.get(vote.category)
        if entry is None:
            reason = f"逐票类别 {vote.category!r} 不在研究声明类别中，未写入编码"
            if _quarantine(db, study.id, vote.category, 'unknown_category', reason,
                           {'vote_id': vote.id, 'declared': declared_names}):
                quarantined.append(reason)
            continue
        if not entry.get('code'):
            reason = f"逐票类别 {vote.category!r} 的编码因重复/歧义映射被隔离，未写入编码"
            if _quarantine(db, study.id, vote.category, 'unmapped_concept', reason,
                           {'vote_id': vote.id}):
                quarantined.append(reason)
            continue
        vote.category_code = entry['code']
        coded += 1
        changed = True

    incomplete = any(not entry.get('code') for entry in scheme)
    status = 'partial' if incomplete else ('migrated' if changed else 'unchanged')
    return StudyOutcome(study_id=study.id, status=status,
                        codes={entry.get('declared_name') or entry.get('name'): entry.get('code')
                               for entry in scheme},
                        votes_coded=coded, quarantined=len(quarantined),
                        reasons=sorted(set(quarantined)))


def migrate_all(db: Session) -> dict:
    """Migrate every legacy study. Each study commits or rolls back alone."""
    summary = {'studies': 0, 'migrated': 0, 'unchanged': 0, 'partial': 0,
               'failed': 0, 'votes_coded': 0, 'quarantined': 0, 'failures': []}
    study_ids = [row[0] for row in db.execute(
        select(AgreementStudy.id).order_by(AgreementStudy.id)).all()]
    for study_id in study_ids:
        try:
            study = db.get(AgreementStudy, study_id)
            if not study:
                continue
            outcome = migrate_study(db, study)
            db.commit()
            summary['studies'] += 1
            summary[outcome.status] = summary.get(outcome.status, 0) + 1
            summary['votes_coded'] += outcome.votes_coded
            summary['quarantined'] += outcome.quarantined
        except Exception as error:  # recover per study; next run retries it
            db.rollback()
            summary['failed'] += 1
            summary['failures'].append({'study_id': study_id, 'error': str(error)[:500]})
            try:
                _quarantine(db, study_id, '<study>', 'migration_error',
                            f'迁移失败：{str(error)[:300]}', {'study_id': study_id})
                db.commit()
            except Exception:
                db.rollback()
    return summary


def verify(db: Session) -> dict:
    """Read-only verification used by the API/page."""
    studies = []
    for study in db.scalars(select(AgreementStudy).order_by(AgreementStudy.id)):
        specimen_ids = [row[0] for row in db.execute(
            select(Specimen.id).where(Specimen.study_id == study.id)).all()]
        votes = list(db.scalars(select(RaterVote).where(
            RaterVote.specimen_id.in_(specimen_ids)))) if specimen_ids else []
        scheme = study.category_scheme or []
        codes = [entry.get('code') for entry in scheme]
        studies.append({
            'study_id': study.id,
            'name': study.name,
            'legacy': not bool(scheme),
            'categories': study.categories,
            'scheme': scheme,
            'total_votes': len(votes),
            'coded_votes': sum(1 for vote in votes if vote.category_code),
            'uncoded_votes': sum(1 for vote in votes if not vote.category_code),
            'duplicate_codes': len(codes) - len(set(code for code in codes if code)),
        })
    open_count = db.scalar(select(func.count()).select_from(CategoryQuarantine).where(
        CategoryQuarantine.status == 'open')) or 0
    return {'studies': studies, 'open_quarantine': open_count}
