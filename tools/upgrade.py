"""Previewed project metadata upgrades; preserve customer content and audit history."""
import base64
import json
import re
import uuid
from tools.storage import commit, confined, consistent_read, digest, json_bytes, lock


def version(value):
    match = re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?", value or "")
    if not match:
        raise ValueError("starter_version이 지원하는 SemVer 형식이 아닙니다.")
    pre = match[4]
    if pre and any(not x or (x.isdigit() and len(x) > 1 and x.startswith('0')) for x in pre.split('.')):
        raise ValueError('잘못된 SemVer prerelease입니다.')
    identifiers = tuple((0, int(x)) if x.isdigit() else (1, x) for x in pre.split('.')) if pre else ()
    return (*map(int, match.group(1, 2, 3)), pre is None, identifiers)


def target_version():
    from tools import starter
    return json.loads((starter.ROOT / 'plugin.json').read_text(encoding='utf-8'))['version']


def managed_hashes(root):
    names = {'project.json', 'HANDOFF.md', 'proposal/package.json', 'templates/selection.json',
             'templates/manifest.json', 'templates/output-checks.json', 'inputs/manifest.json'}
    names.update(p.relative_to(root).as_posix() for p in (root / 'registers').glob('*.csv'))
    return {n: digest(confined(root, n).read_bytes()) for n in sorted(names) if confined(root, n).is_file()}


@consistent_read
def preview(folder):
    from tools import lifecycle as engine, starter
    root, data = starter.load_project(folder)
    target = target_version()
    source = data.get('starter_version')
    if version(source) > version(target):
        raise ValueError('새 버전 프로젝트를 오래된 엔진으로 낮출 수 없습니다.')
    events = engine.events(root)
    return {'kind': 'project_upgrade', 'project_id': data['project_id'], 'source_version': source,
            'target_version': target, 'expected_revision': events[-1]['revision'] if events else None,
            'current_sha256': managed_hashes(root), 'unchanged': source == target,
            'changes': [] if source == target else [{'path': 'project.json', 'fields': ['starter_version', 'updated_at']}],
            'backup_scope': ['project.json'], 'notice': '단계·프로필·ID·승인·문서·원본은 유지합니다. 새 양식 복사나 전체 프로젝트 백업은 수행하지 않습니다.'}


def apply(folder, plan, actor='agent'):
    from tools import lifecycle as engine, starter
    root, _ = starter.load_project(folder)
    with lock(root):
        fresh = preview(root)
        if plan != fresh:
            raise ValueError('이행 미리보기 이후 상태가 바뀌었습니다. 새 계획을 확인하세요.')
        if fresh['unchanged']:
            return {'unchanged': True, 'version': fresh['target_version'], 'revision': fresh['expected_revision']}
        before = confined(root, 'project.json').read_bytes()
        data = json.loads(before)
        data.update(starter_version=fresh['target_version'], updated_at=starter.now())
        after = json_bytes(data)
        identifier = 'UPG-' + uuid.uuid4().hex
        path = f'state/upgrades/{identifier}.json'
        record = json_bytes({'schema_version': 1, 'id': identifier, 'project_id': data['project_id'],
            'source_version': fresh['source_version'], 'target_version': fresh['target_version'],
            'before_project': base64.b64encode(before).decode(), 'before_sha256': digest(before), 'after_sha256': digest(after)})
        changes = {'project.json': after, path: record}
        revision = engine.add_event(root, changes, 'upgrade', actor,
            {'upgrade_id': identifier, 'backup_ref': path, 'backup_sha256': digest(record),
             'source_version': fresh['source_version'], 'target_version': fresh['target_version']})
        commit(root, changes)
        return {'unchanged': False, 'revision': revision, 'version': fresh['target_version'],
                'upgrade_id': identifier, 'backup_ref': path, 'backup_scope': ['project.json'], 'notice': fresh['notice']}


def record(root, identifier):
    from tools import lifecycle as engine
    if not isinstance(identifier, str) or not re.fullmatch(r'UPG-[a-f0-9]{32}', identifier):
        raise ValueError('유효한 upgrade_id가 필요합니다.')
    raw = confined(root, f'state/upgrades/{identifier}.json').read_bytes()
    data = json.loads(raw)
    events = engine.events(root)
    event = next((e for e in events if e['action'] == 'upgrade' and e['details'].get('upgrade_id') == identifier), None)
    if not event or event['details'].get('backup_sha256') != digest(raw) or data.get('id') != identifier or data.get('schema_version') != 1:
        raise ValueError('이행 백업이 실제 기록과 일치하지 않습니다.')
    before = base64.b64decode(data['before_project'], validate=True)
    if digest(before) != data['before_sha256']:
        raise ValueError('이행 이전 파일의 해시가 일치하지 않습니다.')
    return data, raw, before, event, events


@consistent_read
def restore_preview(folder, identifier):
    from tools import starter
    root, data = starter.load_project(folder)
    backup, raw, _, event, events = record(root, identifier)
    current = digest(confined(root, 'project.json').read_bytes())
    conflicts = []
    if current != backup['after_sha256']:
        conflicts.append('project.json')
    if events[-1]['revision'] != event['revision']:
        conflicts.append('later_events')
    if data['project_id'] != backup['project_id']:
        raise ValueError('다른 프로젝트의 이행 백업입니다.')
    return {'kind': 'project_upgrade_restore', 'upgrade_id': identifier, 'expected_revision': events[-1]['revision'],
            'backup_sha256': digest(raw), 'current_project_sha256': current, 'conflicts': conflicts}


def restore(folder, plan, actor='agent'):
    from tools import lifecycle as engine, starter
    root, _ = starter.load_project(folder)
    if not isinstance(plan, dict) or 'upgrade_id' not in plan:
        raise ValueError('upgrade-restore-preview의 실제 계획이 필요합니다.')
    with lock(root):
        fresh = restore_preview(root, plan['upgrade_id'])
        if plan != fresh or fresh['conflicts']:
            raise ValueError('이행 이후 수정·이력이 있어 되돌리기를 중단합니다.')
        backup, _, before, _, _ = record(root, plan['upgrade_id'])
        data = json.loads(before)
        data['updated_at'] = starter.now()
        changes = {'project.json': json_bytes(data)}
        revision = engine.add_event(root, changes, 'upgrade-restore', actor, {'upgrade_id': plan['upgrade_id'], 'restored_version': backup['source_version']})
        commit(root, changes)
        return {'revision': revision, 'version': backup['source_version'], 'notice': '관리 버전만 복원했습니다. 이행·복원 이력은 보존됩니다.'}
