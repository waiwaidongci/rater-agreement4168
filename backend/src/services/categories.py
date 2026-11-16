"""Traceable rock-core category registry.

A category is a *concept* with an immutable ``code``. Display names are
versioned per locale; synonyms and retired names are aliases. Imports and
migration resolve names to codes, while run snapshots and review memos keep
the locale + version + name that existed when they were frozen.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import unicodedata
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.models import (
    CategoryAlias,
    CategoryConcept,
    CategoryQuarantine,
    CategoryTerm,
)

DEFAULT_LOCALE = 'zh-CN'
SUPPORTED_LOCALES = {'zh-CN': '简体中文', 'en': 'English'}
MAX_NAME = 80


class CategoryMappingError(ValueError):
    def __init__(self, reason_code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.reason_code = reason_code
        self.details = details or {}


def normalize_locale(locale: str | None) -> str:
    value = (locale or DEFAULT_LOCALE).strip()
    if value not in SUPPORTED_LOCALES:
        raise CategoryMappingError('unsupported_locale', f'不支持的语言 {value!r}；可用 {"/".join(SUPPORTED_LOCALES)}')
    return value


def normalize_name(name: str) -> str:
    if not isinstance(name, str):
        raise CategoryMappingError('invalid_name', '类别名必须是字符串')
    value = unicodedata.normalize('NFKC', name).strip()
    value = ' '.join(value.split())
    if not value or len(value) > MAX_NAME:
        raise CategoryMappingError('invalid_name', '类别名须为1..80个非空字符')
    return value.casefold()


def _new_code(db: Session) -> str:
    for _ in range(12):
        code = 'CAT-' + secrets.token_hex(4).upper()
        if not db.scalar(select(CategoryConcept).where(CategoryConcept.code == code)):
            return code
    raise RuntimeError('无法生成唯一类别编码')


def latest_version(db: Session, concept_id: int) -> int:
    value = db.scalar(select(func.max(CategoryTerm.version)).where(CategoryTerm.concept_id == concept_id))
    return int(value or 0)


def term_for_version(db: Session, concept_id: int, locale: str, version: int) -> CategoryTerm | None:
    """Best available term at/below a frozen version, locale first, then default."""
    rows = list(db.scalars(select(CategoryTerm).where(
        CategoryTerm.concept_id == concept_id,
        CategoryTerm.version <= version,
    )))
    exact = [r for r in rows if r.locale == locale]
    if exact:
        return max(exact, key=lambda r: r.version)
    default = [r for r in rows if r.locale == DEFAULT_LOCALE]
    if default:
        return max(default, key=lambda r: r.version)
    return max(rows, key=lambda r: r.version) if rows else None


def current_term(db: Session, concept_id: int, locale: str) -> CategoryTerm | None:
    return term_for_version(db, concept_id, locale, latest_version(db, concept_id))


def concept_for_code(db: Session, code: str) -> CategoryConcept | None:
    return db.scalar(select(CategoryConcept).where(CategoryConcept.code == code))


def _matches(db: Session, normalized: str) -> dict[int, list[dict]]:
    result: dict[int, list[dict]] = {}
    for alias in db.scalars(select(CategoryAlias).where(CategoryAlias.normalized == normalized)):
        result.setdefault(alias.concept_id, []).append(
            {'kind': 'alias', 'locale': alias.locale, 'name': alias.name})
    for term in db.scalars(select(CategoryTerm).where(CategoryTerm.normalized == normalized)):
        result.setdefault(term.concept_id, []).append(
            {'kind': 'term', 'locale': term.locale, 'name': term.name, 'version': term.version})
    return result


def resolve(db: Session, name: str, locale: str = DEFAULT_LOCALE) -> dict:
    """Resolve a possibly synonymous name. Never writes.

    A name that matches several concepts is only accepted when exactly one of
    those matches is a *current* display name; otherwise it is ambiguous and
    must be quarantined rather than guessed.
    """
    normalized = normalize_name(name)
    matches = _matches(db, normalized)
    concepts = [db.get(CategoryConcept, concept_id) for concept_id in matches]
    concepts = [c for c in concepts if c]
    if not concepts:
        return {'status': 'unknown', 'raw_name': name, 'normalized': normalized,
                'locale': locale, 'matches': []}
    if len(concepts) > 1:
        current = [c for c in concepts if any(
            term.version == latest_version(db, c.id)
            for term in db.scalars(select(CategoryTerm).where(
                CategoryTerm.concept_id == c.id,
                CategoryTerm.normalized == normalized)))]
        if len(current) == 1:
            concepts = current
        else:
            return {'status': 'ambiguous', 'raw_name': name, 'normalized': normalized,
                    'locale': locale, 'codes': sorted(c.code for c in concepts),
                    'matches': {c.code: matches[c.id] for c in concepts}}
    concept = concepts[0]
    term = current_term(db, concept.id, locale)
    return {'status': 'resolved', 'raw_name': name, 'normalized': normalized,
            'locale': locale, 'code': concept.code, 'concept_id': concept.id,
            'term': term, 'matches': matches[concept.id]}


def _add_alias_row(db: Session, concept_id: int, locale: str, name: str, actor: str) -> bool:
    """Record a synonym. Names are unique across concepts (history included)."""
    normalized = normalize_name(name)
    existing = db.scalar(select(CategoryAlias).where(
        CategoryAlias.concept_id == concept_id,
        CategoryAlias.locale == locale,
        CategoryAlias.normalized == normalized,
    ))
    if existing:
        return False
    other_alias = db.scalar(select(CategoryAlias).where(
        CategoryAlias.normalized == normalized,
        CategoryAlias.concept_id != concept_id,
    ))
    other_term = db.scalar(select(CategoryTerm).where(
        CategoryTerm.normalized == normalized,
        CategoryTerm.concept_id != concept_id,
    ))
    if other_alias or other_term:
        raise CategoryMappingError(
            'duplicate_mapping', f'名称 {name!r} 已属于另一个类别，不能重复映射',
            {'name': name, 'normalized': normalized, 'concept_id': concept_id})
    db.add(CategoryAlias(concept_id=concept_id, locale=locale, name=name.strip(),
                         normalized=normalized, created_by=actor))
    return True


def create_concept(db: Session, name: str, locale: str, actor: str,
                   translations: dict[str, str] | None = None) -> CategoryConcept:
    locale = normalize_locale(locale)
    names = {locale: name.strip()}
    for loc, translated in (translations or {}).items():
        names[normalize_locale(loc)] = translated.strip()
    concept = CategoryConcept(code=_new_code(db), created_by=actor)
    db.add(concept)
    db.flush()
    for loc, label in names.items():
        normalized = normalize_name(label)
        db.add(CategoryTerm(concept_id=concept.id, locale=loc, name=label,
                            normalized=normalized, version=1, created_by=actor))
        db.add(CategoryAlias(concept_id=concept.id, locale=loc, name=label,
                             normalized=normalized, created_by=actor))
    db.flush()
    return concept


def ensure_concept(db: Session, name: str, locale: str, actor: str) -> tuple[CategoryConcept, CategoryTerm, bool]:
    """Resolve for a new import, creating a concept only for a truly new name."""
    locale = normalize_locale(locale)
    found = resolve(db, name, locale)
    if found['status'] == 'ambiguous':
        raise CategoryMappingError(
            'ambiguous_mapping', f'类别 {name!r} 同时匹配多个编码，需先解决重复映射',
            {'name': name, 'codes': found['codes']})
    if found['status'] == 'unknown':
        concept = create_concept(db, name, locale, actor)
        created = True
    else:
        concept = db.get(CategoryConcept, found['concept_id'])
        _add_alias_row(db, concept.id, locale, name, actor)
        created = False
    term = current_term(db, concept.id, locale)
    if not term:
        raise CategoryMappingError('missing_term', f'类别 {name!r} 缺少显示名')
    return concept, term, created


def add_alias(db: Session, code: str, name: str, locale: str, actor: str) -> dict:
    concept = concept_for_code(db, code)
    if not concept:
        raise CategoryMappingError('unknown_code', f'类别编码 {code} 不存在')
    found = resolve(db, name, locale)
    if found['status'] == 'ambiguous' or (found['status'] == 'resolved' and found['code'] != code):
        raise CategoryMappingError(
            'duplicate_mapping', f'同义名 {name!r} 已映射到其他编码',
            {'name': name, 'codes': found.get('codes') or [found.get('code')]})
    created = _add_alias_row(db, concept.id, locale, name, actor)
    db.commit()
    return {'code': code, 'name': name.strip(), 'locale': locale, 'created': created}


def rename_concept(db: Session, code: str, name: str, locale: str, actor: str) -> dict:
    """Append a new version. Historical versions and aliases stay untouched.

    Because every name (current or historical) is globally unique per concept,
    a rename can never silently take over another category's spelling.
    """
    locale = normalize_locale(locale)
    concept = concept_for_code(db, code)
    if not concept:
        raise CategoryMappingError('unknown_code', f'类别编码 {code} 不存在')
    normalized = normalize_name(name)
    found = resolve(db, name, locale)
    if found['status'] == 'ambiguous':
        raise CategoryMappingError('ambiguous_mapping', f'新名称 {name!r} 存在重复映射')
    if found['status'] == 'resolved' and found['code'] != code:
        raise CategoryMappingError('duplicate_mapping', f'新名称 {name!r} 已属于 {found["code"]}')

    old_version = latest_version(db, concept.id)
    current = current_term(db, concept.id, locale) if old_version else None
    if current and current.locale == locale and current.normalized == normalized:
        return {'code': code, 'version': old_version, 'name': current.name,
                'locale': locale, 'changed': False, 'previous_version': old_version}

    new_version = old_version + 1
    previous = list(db.scalars(select(CategoryTerm).where(
        CategoryTerm.concept_id == concept.id, CategoryTerm.version == old_version)))
    copied_locales = set()
    for term in previous:
        db.add(CategoryTerm(concept_id=concept.id, locale=term.locale, name=term.name,
                            normalized=term.normalized, version=new_version, created_by=actor))
        copied_locales.add(term.locale)
    if locale in copied_locales:
        row = db.scalar(select(CategoryTerm).where(
            CategoryTerm.concept_id == concept.id,
            CategoryTerm.locale == locale,
            CategoryTerm.version == new_version,
        ))
        row.name = name.strip()
        row.normalized = normalized
        row.created_by = actor
    else:
        db.add(CategoryTerm(concept_id=concept.id, locale=locale, name=name.strip(),
                            normalized=normalized, version=new_version, created_by=actor))
    _add_alias_row(db, concept.id, locale, name, actor)
    db.commit()
    return {'code': code, 'version': new_version, 'name': name.strip(), 'locale': locale,
            'changed': True, 'previous_version': old_version}


def registry(db: Session) -> list[dict]:
    concepts = list(db.scalars(select(CategoryConcept).order_by(CategoryConcept.id)))
    output = []
    for concept in concepts:
        terms = list(db.scalars(select(CategoryTerm).where(
            CategoryTerm.concept_id == concept.id).order_by(CategoryTerm.version, CategoryTerm.locale)))
        aliases = list(db.scalars(select(CategoryAlias).where(
            CategoryAlias.concept_id == concept.id).order_by(CategoryAlias.locale, CategoryAlias.name)))
        version = max((t.version for t in terms), default=0)
        names = {}
        for locale in SUPPORTED_LOCALES:
            term = current_term(db, concept.id, locale)
            names[locale] = {'name': term.name, 'version': term.version, 'resolved_locale': term.locale} if term else None
        output.append({
            'code': concept.code,
            'latest_version': version,
            'names': names,
            'aliases': [{'locale': a.locale, 'name': a.name} for a in aliases],
            'history': [{'locale': t.locale, 'version': t.version, 'name': t.name} for t in terms],
        })
    return output


def study_scheme(study: Any) -> list[dict]:
    """Return a study's scheme, deriving a read-only view for un-migrated data."""
    scheme = study.get('category_scheme') if isinstance(study, dict) else study.category_scheme
    categories = study.get('categories') if isinstance(study, dict) else study.categories
    if scheme:
        return [dict(entry) for entry in scheme]
    return [{'code': None, 'name': name, 'locale': DEFAULT_LOCALE,
             'term_version': None, 'declared_name': name} for name in categories]


def category_snapshot(db: Session, scheme: list[dict], locale: str) -> list[dict]:
    """Freeze current display names for a new run. Renames cannot alter this."""
    locale = normalize_locale(locale)
    entries = []
    for item in scheme:
        code = item.get('code')
        concept = concept_for_code(db, code) if code else None
        term = None
        if concept:
            term = current_term(db, concept.id, locale)
        name = term.name if term else item.get('name')
        entries.append({
            'code': code,
            'name': name,
            'locale': locale,
            'name_locale': term.locale if term else item.get('locale', DEFAULT_LOCALE),
            'term_version': term.version if term else item.get('term_version'),
        })
    return entries


def localize_entries(db: Session, entries: list[dict], locale: str | None) -> list[dict]:
    """Translate frozen entries for an export. Historical snapshots are not modified."""
    if not locale:
        return [dict(e, export_name=e.get('name'), translated=False, translation_status='original')
                for e in entries]
    locale = normalize_locale(locale)
    output = []
    for entry in entries:
        code = entry.get('code')
        version = entry.get('term_version')
        translated = False
        status = 'original'
        name = entry.get('name')
        concept = concept_for_code(db, code) if code else None
        if concept and version:
            term = term_for_version(db, concept.id, locale, int(version))
            if term:
                name = term.name
                translated = term.locale == locale
                status = 'translated' if translated else 'locale_fallback'
            else:
                status = 'no_translation'
        elif concept:
            term = current_term(db, concept.id, locale)
            if term:
                name = term.name
                translated = term.locale == locale
                status = 'current_fallback' if translated else 'locale_fallback'
            else:
                status = 'no_translation'
        elif not code:
            status = 'no_code'
        output.append(dict(entry, export_name=name, translated=translated,
                           translation_status=status))
    return output


def quarantine(db: Session, *, source: str, study_id: int | None, raw_name: str,
               locale: str, reason_code: str, reason: str, details: dict | None = None,
               resolved_code: str | None = None) -> bool:
    """Insert a quarantine row once; re-runs never duplicate or rewrite it."""
    normalized = normalize_name(raw_name)
    payload = [source, study_id, normalized, reason_code]
    fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    existing = db.scalar(select(CategoryQuarantine).where(CategoryQuarantine.fingerprint == fingerprint))
    if existing:
        return False
    db.add(CategoryQuarantine(
        fingerprint=fingerprint, source=source, study_id=study_id, raw_name=raw_name.strip(),
        normalized_name=normalized, locale=locale, reason_code=reason_code, reason=reason,
        details=details or {}, status='open', resolved_code=resolved_code))
    db.flush()
    return True


def quarantine_rows(db: Session, include_resolved: bool = False) -> list[CategoryQuarantine]:
    query = select(CategoryQuarantine).order_by(CategoryQuarantine.id.desc())
    if not include_resolved:
        query = query.where(CategoryQuarantine.status == 'open')
    return list(db.scalars(query))


def resolve_quarantine(db: Session, quarantine_id: int, code: str, actor: str) -> CategoryQuarantine:
    item = db.get(CategoryQuarantine, quarantine_id)
    if not item:
        raise CategoryMappingError('unknown_quarantine', '隔离记录不存在')
    if item.status == 'resolved':
        if item.resolved_code == code:
            return item
        raise CategoryMappingError('already_resolved', '隔离记录已按其他编码解决')
    if not concept_for_code(db, code):
        raise CategoryMappingError('unknown_code', f'类别编码 {code} 不存在')
    item.status = 'resolved'
    item.resolved_code = code
    db.commit()
    return item


def decorate_result(db: Session, result: dict, scheme: list[dict]) -> dict:
    """Add code metadata to an old result without persisting any change.

    Names are matched against the study scheme first, then against aliases so
    that a result frozen before a rename still finds its stable code.
    """
    if not result:
        return result
    payload = dict(result)
    if not payload.get('category_entries'):
        names = payload.get('categories') or []
        by_name = {entry.get('name'): entry for entry in scheme}
        by_declared = {entry.get('declared_name'): entry for entry in scheme}
        entries = []
        for name in names:
            source = by_name.get(name) or by_declared.get(name)
            code = source.get('code') if source else None
            resolved = None
            if not code:
                found = resolve(db, name, DEFAULT_LOCALE)
                if found['status'] == 'resolved':
                    code = found['code']
                    resolved = found['code']
            entries.append({
                'code': code,
                'name': name,
                'locale': source.get('locale', DEFAULT_LOCALE) if source else DEFAULT_LOCALE,
                'term_version': source.get('term_version') if source else None,
                'legacy': True,
                'resolved_by_alias': bool(resolved),
            })
        payload['category_entries'] = entries
    if not payload.get('category_codes'):
        payload['category_codes'] = [entry.get('code') for entry in payload['category_entries']]
    return payload


def resolve_vote_category(db: Session, study: Any, raw: str, locale: str = DEFAULT_LOCALE) -> dict | None:
    """Map a vote edit (name, synonym, or code) onto the study's declared scheme."""
    scheme = study_scheme(study)
    for entry in scheme:
        if entry.get('code') and raw == entry['code']:
            return entry
    for entry in scheme:
        if raw == entry.get('name') or raw == entry.get('declared_name'):
            return entry
    found = resolve(db, raw, locale)
    if found['status'] == 'resolved':
        matches = [entry for entry in scheme if entry.get('code') == found['code']]
        if len(matches) == 1:
            return matches[0]
    return None


def scheme_codes(scheme: list[dict]) -> list[str | None]:
    return [entry.get('code') for entry in scheme]
