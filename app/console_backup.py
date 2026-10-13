"""전체 운영 데이터 ZIP. 새 외부 DB 없이 기존 데이터베이스를 백업한다."""
from __future__ import annotations
import io
import json
import zipfile
from datetime import datetime, UTC
from sqlalchemy import Boolean, DateTime, Integer, LargeBinary, String, Text, delete, insert, select, text
from sqlalchemy.exc import IntegrityError, StatementError
from .database import Base
from .video import VIDEO_DIR, VIDEO_LOCK, video_files, valid_filename, replace_video_folder, MAX_VIDEO_TOTAL

VERSION = 3
MAX_ZIP_BYTES = 200 * 1024 * 1024
MAX_JSON_BYTES = 80 * 1024 * 1024
MAX_EXPANDED_BYTES = 400 * 1024 * 1024
MAX_ROWS = 2_000_000


def make_full_backup(db, video_directory=VIDEO_DIR):
    with VIDEO_LOCK:
        return _make_full_backup(db, video_directory)


def _make_full_backup(db, video_directory):
    # SQLite의 SELECT도 같은 스냅샷을 보도록 명시적으로 읽기 트랜잭션을 연다.
    if db.bind.dialect.name == 'sqlite' and not db.connection().connection.driver_connection.in_transaction:
        db.execute(text('BEGIN'))
    payload = {'version': VERSION, 'kind':'full-operations', 'exported_at':datetime.now(UTC).isoformat(), 'tables':{}, 'video_files':[]}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for table in Base.metadata.sorted_tables:
            rows = []
            for row in db.execute(select(table)).mappings():
                item = {}
                for col in table.columns:
                    value = row[col.name]
                    if isinstance(value, datetime):
                        value = value.isoformat()
                    if isinstance(col.type, LargeBinary) and value is not None:
                        member = f'media/{row["id"]}.bin'
                        archive.writestr(member, value)
                        value = {'archive_path':member}
                    item[col.name] = value
                rows.append(item)
            payload['tables'][table.name] = rows
        files = video_files(video_directory)
        if sum(p.stat().st_size for p in files) > MAX_VIDEO_TOTAL:
            raise ValueError('영상 폴더 합계가 100MB를 초과합니다. 크기를 줄인 뒤 백업하세요')
        for path in files:
            member = 'videos/' + path.name
            archive.write(path, member)
            payload['video_files'].append({'name':path.name,'archive_path':member})
        encoded = json.dumps(payload, ensure_ascii=False, separators=(',',':')).encode('utf-8')
        if len(encoded) > MAX_JSON_BYTES or sum(len(rows) for rows in payload['tables'].values()) > MAX_ROWS:
            raise ValueError('백업 데이터가 허용 크기를 초과합니다. 운영 DB 백업으로 보관하세요')
        archive.writestr('data.json', encoded)
        archive.writestr('README.txt', 'Galaxy Consultant 전체 운영 백업 v3\n매장·번호표·호출·트래픽·관리자·권한·영상·알림 등록 데이터가 포함됩니다.\n비밀번호 검증값과 기기 토큰이 있으므로 외부에 공유하지 마세요.\n로그인 세션, 환경변수와 외부 인증 서버의 키는 포함하지 않습니다.\n복원은 현재 데이터를 백업 시점으로 교체합니다. 영상 폴더도 포함됩니다. 6개월 이전 누적 기록은 복원 후 정리됩니다.\n')
    db.rollback()  # 읽기 트랜잭션만 종료
    return output.getvalue()


def _load_archive(content):
    if len(content) > MAX_ZIP_BYTES:
        raise ValueError('전체 ZIP은 200MB 이하만 복원할 수 있습니다')
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
        names = archive.namelist()
        if len(set(names)) != len(names) or 'data.json' not in names:
            raise ValueError('중복 파일 또는 data.json이 없는 ZIP입니다')
        if archive.getinfo('data.json').file_size > MAX_JSON_BYTES or sum(i.file_size for i in archive.infolist()) > MAX_EXPANDED_BYTES:
            raise ValueError('압축 해제 데이터가 허용 크기를 초과합니다')
        data = json.loads(archive.read('data.json'))
    except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError, RuntimeError) as exc:
        raise ValueError('올바른 백업 ZIP이 아닙니다') from exc
    return archive, data


def restore_full_backup(db, content, video_directory=VIDEO_DIR):
    archive, data = _load_archive(content)
    with archive:
        if not isinstance(data, dict) or data.get('version') != VERSION or data.get('kind') != 'full-operations':
            raise ValueError('전체 운영 백업 v3 파일을 선택하세요. 이전 매장 ZIP은 매장관리의 매장 ZIP 복원을 사용하세요')
        tables = Base.metadata.sorted_tables
        raw = data.get('tables')
        if not isinstance(raw, dict) or set(raw) != {t.name for t in tables}:
            raise ValueError('백업의 테이블 구성이 현재 프로그램과 다릅니다')
        if any(not isinstance(v, list) for v in raw.values()) or sum(len(v) for v in raw.values()) > MAX_ROWS:
            raise ValueError('백업 데이터 행 구성이 올바르지 않거나 너무 큽니다')
        video_contents = {}
        raw_videos = data.get('video_files', [])
        if not isinstance(raw_videos, list): raise ValueError('영상 목록 형식이 올바르지 않습니다')
        total_video_bytes = 0
        for video in raw_videos:
            if not isinstance(video, dict) or not valid_filename(video.get('name')):
                raise ValueError('백업 영상 이름이 올바르지 않습니다')
            name = video['name']; member = 'videos/' + name
            if video.get('archive_path') != member or name in video_contents or member not in archive.namelist():
                raise ValueError('백업 영상이 누락되거나 중복되었습니다')
            total_video_bytes += archive.getinfo(member).file_size
            if total_video_bytes > MAX_VIDEO_TOTAL: raise ValueError('영상 합계가 100MB를 초과합니다')
            video_contents[name] = archive.read(member)
        cleaned = {}
        for table in tables:
            rows = []
            for row in raw[table.name]:
                if not isinstance(row, dict) or set(row) != set(table.columns.keys()):
                    raise ValueError(f'{table.name}: 필수 항목이 일치하지 않습니다')
                item = {}
                for col in table.columns:
                    value = row[col.name]
                    if value is None:
                        if not col.nullable:
                            raise ValueError(f'{table.name}: {col.name} 값이 없습니다')
                    elif isinstance(col.type, Boolean):
                        if not isinstance(value, bool): raise ValueError('잘못된 참/거짓 값입니다')
                    elif isinstance(col.type, Integer):
                        if isinstance(value, bool) or not isinstance(value, int): raise ValueError('잘못된 숫자 값입니다')
                    elif isinstance(col.type, DateTime):
                        try:
                            value = datetime.fromisoformat(value)
                            if value.tzinfo: value = value.astimezone(UTC).replace(tzinfo=None)
                        except (TypeError, ValueError): raise ValueError('잘못된 날짜 값입니다')
                    elif isinstance(col.type, LargeBinary):
                        expected = f'media/{row["id"]}.bin'
                        if value != {'archive_path':expected} or expected not in archive.namelist():
                            raise ValueError('백업 영상 파일이 누락되었습니다')
                        if archive.getinfo(expected).file_size > 50*1024*1024:
                            raise ValueError('영상 파일 크기가 50MB를 초과합니다')
                        value = archive.read(expected)
                    elif isinstance(col.type, (String, Text)):
                        if not isinstance(value, str) or (col.type.length and len(value)>col.type.length):
                            raise ValueError(f'{table.name}: 잘못된 문자열 값입니다')
                    item[col.name] = value
                rows.append(item)
            cleaned[table.name] = rows
    # 키/역할 범위도 검증해 인증 구조가 깨진 백업은 쓰기 전에 거절한다.
    accounts = cleaned['console_accounts']
    for row in accounts:
        if row['role'] not in {'branch','integrated'}:
            raise ValueError('관리자 계정 정보가 올바르지 않습니다')
        if (row['role']=='branch') != (row['store_id'] is not None):
            raise ValueError('지점 관리자 연결 정보가 올바르지 않습니다')
    try:
        with replace_video_folder(video_contents, video_directory):
            # 외래 키에 따라 역순 삭제/정순 삽입. 오류 시 전체 롤백한다.
            for table in reversed(tables):
                db.execute(delete(table))
            for table in tables:
                rows = cleaned[table.name]
                for offset in range(0,len(rows),500):
                    db.execute(insert(table),rows[offset:offset+500])
            if db.bind.dialect.name == 'postgresql':
                for table in tables:
                    if 'id' in table.c and isinstance(table.c.id.type,Integer):
                        db.execute(text(f"SELECT setval(pg_get_serial_sequence('{table.name}','id'), COALESCE((SELECT MAX(id) FROM {table.name}),1), (SELECT COUNT(*)>0 FROM {table.name}))"))
            # 과거에 대기 중이던 알림을 복원 직후 재발송하지 않는다.
            if 'manager_mobile_deliveries' in Base.metadata.tables:
                table = Base.metadata.tables['manager_mobile_deliveries']
                db.execute(table.update().where(table.c.status.in_(['pending','sending'])).values(status='expired'))
            from .retention import purge_old_activity
            purge_old_activity(db, commit=False)
            db.commit()
    except (IntegrityError, StatementError) as exc:
        db.rollback()
        raise ValueError('중복 키 또는 연결이 끊긴 데이터가 있어 복원하지 않았습니다') from exc
    return {name:len(rows) for name,rows in cleaned.items()}
