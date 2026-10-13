import io
import json
import zipfile
from datetime import datetime, timedelta, UTC
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from app.main import create_app
from app.models import Store, Ticket, CallLog, ServiceCounter, utc_now
from app.console_models import ConsoleAccount, ConsoleGrant, StoreProfile, TrafficEvent, TicketAttribution, ConsoleAudit
from app.console_reports import period_bounds
from app.retention import retention_cutoff, purge_old_activity, KST
from app.backup import make_store_backup_zip
from app.console_backup import make_full_backup, restore_full_backup
from app.services import issue_ticket, call_next, recall_last

KEYS={'common-GCGC-test','gcs-access-test','kiosk-manage-test','gcgc-wrong-case','GCGC-denied','samsung-branch-test','SAMSUNG-upper-test','samsung-denied'}

def config(url='sqlite:///:memory:'):
    return {'TESTING':True,'DATABASE_URL':url,'SEED_DEFAULT_STORES':False,
        'AUTH_CHECKER':lambda key:{'status':'approved','token':'test'} if key in KEYS-{'GCGC-denied','samsung-denied'} else {'status':'denied'}}

@pytest.fixture()
def env(tmp_path):
    app=create_app({**config(),'VIDEO_DIRECTORY':str(tmp_path/'videos')})
    with TestClient(app) as c:
        with app.state.SessionLocal() as db:
            db.add_all([Store(id=1,name='강남점',code='GN01'),Store(id=2,name='인천점',code='IC02'),Store(id=3,name='동해점',code='DH03')]);db.commit()
        yield app,c

def login(c,name='GC',password='common-GCGC-test'):
    r=c.post('/api/console/login',json={'mode':'integrated','name':name,'username':'하상욱','password':password})
    if r.status_code==200:
        me=c.get('/api/console/me');assert me.status_code==200
        c.headers['X-Console-CSRF']=me.json()['user']['csrf']
    return r

def unlock(c):
    assert c.post('/api/admin/manage/login',json={'key':'kiosk-manage-test'}).status_code==200
    assert c.post('/api/console/access/unlock',json={'key':'gcs-access-test'}).status_code==200

def create_account(c,name='서울담당',ids=None):
    r=c.post('/api/console/accounts',json={'name':name,'store_ids':ids or [1]})
    assert r.status_code==200,r.text
    return r.json()['id']

def pack(data):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:z.writestr('data.json',json.dumps(data))
    return output.getvalue()

@pytest.mark.parametrize('key,ok',[('common-GCGC-test',True),('gcgc-wrong-case',False),('GCGC-denied',False),('fake-GCGC-key',False)])
def test_uppercase_marker_and_external_approval_both_required(env,key,ok):
    _,c=env
    assert (login(c,password=key).status_code==200)==ok

def test_unregistered_name_cannot_login_and_fixed_id_required(env):
    _,c=env
    assert login(c,name='미등록').status_code==401
    assert c.post('/api/console/login',json={'mode':'integrated','name':'GC','username':'다른ID','password':'common-GCGC-test'}).status_code==401
    assert login(c).status_code==200
    assert c.get('/api/console/me').json()['user']['name']=='GC'

def test_kiosk_and_gcs_are_separate_gates(env):
    _,c=env;login(c)
    assert c.post('/api/console/access/unlock',json={'key':'gcs-access-test'}).status_code==401
    c.post('/api/admin/manage/login',json={'key':'kiosk-manage-test'})
    assert c.post('/api/console/access/unlock',json={'key':'common-GCGC-test'}).status_code==401
    assert c.post('/api/console/access/unlock',json={'key':'gcs-forged'}).status_code==401
    assert c.get('/api/console/access').status_code==403
    assert c.post('/api/console/access/unlock',json={'key':'gcs-access-test'}).status_code==200

def test_registered_name_sees_only_assigned_stores_and_teams(env):
    app,c=env;login(c);unlock(c);create_account(c,ids=[1])
    c.put('/api/console/store-teams',json={'store_ids':[1,2],'sales_team':'서울영업팀'})
    with app.state.SessionLocal() as db:
        issue_ticket(db,1,'simple_service');issue_ticket(db,2,'simple_service')
    assert login(c,'서울담당').status_code==200
    assert [s['id'] for s in c.get('/api/console/me').json()['stores']]==[1]
    assert c.get('/api/console/report?scope=store&store_id=2').status_code==403
    report=c.get('/api/console/report?scope=team&team=서울영업팀').json()
    assert report['summary']['issued']==1 and len(report['stores'])==1
    assert c.get('/api/console/report?scope=team&team=경인영업팀').status_code==403
    assert c.get('/api/console/backup').status_code==403
    assert c.get('/api/console/access').status_code==403

def test_account_reassignment_and_delete_revoke_existing_sessions(env):
    app,owner=env;login(owner);unlock(owner);aid=create_account(owner)
    limited=TestClient(app);assert login(limited,'서울담당').status_code==200
    assert owner.put(f'/api/console/accounts/{aid}',json={'name':'서울담당','store_ids':[2]}).status_code==200
    assert limited.get('/api/console/report').status_code==401
    assert login(limited,'서울담당').status_code==200
    assert [s['id'] for s in limited.get('/api/console/me').json()['stores']]==[2]
    assert owner.delete(f'/api/console/accounts/{aid}').status_code==200
    assert limited.get('/api/console/report').status_code==401
    assert login(limited,'서울담당').status_code==401
    assert owner.post('/api/console/accounts',json={'name':'GC','store_ids':[1]}).status_code==400

@pytest.mark.parametrize('key,ok',[('samsung-branch-test',True),('SAMSUNG-upper-test',True),('samsung-denied',False),('samsung-forged',False),('common-GCGC-test',False),('1234',False)])
def test_branch_key_and_selected_store_scope(env,key,ok):
    app,c=env
    response=c.post('/api/console/login',json={'mode':'branch','username':'GN01','password':key})
    assert (response.status_code==200)==ok
    if not ok:return
    me=c.get('/api/console/me').json();c.headers['X-Console-CSRF']=me['user']['csrf']
    assert [s['id'] for s in me['stores']]==[1]
    assert c.get('/api/console/report?scope=store&store_id=2').status_code==403
    assert c.get('/api/console/report').json()['stores'][0]['id']==1
    assert c.delete('/api/console/video').status_code==403
    assert c.post('/api/admin/manage/login',json={'key':'kiosk-manage-test'}).status_code==403
    assert c.post('/api/console/accounts',json={'name':'불가','store_ids':[1]}).status_code==403
    with app.state.SessionLocal() as db:
        account=db.scalar(select(ConsoleAccount).where(ConsoleAccount.store_id==1))
        assert account.password_hash==''

def test_login_rate_limit_and_csrf(env):
    _,c=env
    for i in range(5): assert c.post('/api/console/login',json={'mode':'branch','username':'GN01','password':'1234'}).status_code==401
    assert c.post('/api/console/login',json={'mode':'branch','username':'GN01','password':'1234'}).status_code==429
    login(c);unlock(c);c.headers.pop('X-Console-CSRF')
    assert c.post('/api/console/accounts',json={'name':'CSRF','store_ids':[1]}).status_code==403

def test_report_kst_boundary_first_call_weighted_wait_and_empty(env):
    app,c=env;login(c)
    now=utc_now();day=datetime.now(KST).date();start=datetime.combine(day,datetime.min.time(),KST).astimezone(UTC).replace(tzinfo=None)
    with app.state.SessionLocal() as db:
        for sid,at,delay,n,status in [(1,start-timedelta(seconds=1),20,1,'called'),(1,start,60,2,'called'),(2,start+timedelta(hours=3),180,1,'called'),(2,start+timedelta(hours=4),None,2,'waiting')]:
            db.add(Ticket(store_id=sid,service_type='simple_service',round_no=1,ticket_number=n,status=status,created_at=at,called_at=at+timedelta(seconds=delay) if delay else None))
        db.add(CallLog(store_id=1,service_type='simple_service',round_no=1,ticket_number=2,call_type='recall',created_at=start+timedelta(seconds=120)))
        db.commit()
    report=c.get(f'/api/console/report?period=day&value={day}').json()
    assert report['summary']['issued']==3 and report['summary']['called']==2
    assert report['summary']['average_wait_seconds']==120
    assert report['hourly'][0]['issued']==1 and report['hourly'][3]['issued']==1
    empty=c.get(f'/api/console/report?period=day&value={day-timedelta(days=2)}').json()
    assert empty['summary']['average_wait_seconds'] is None and empty['summary']['issued']==0
    assert len(empty['hourly'])==24
    assert c.get('/api/console/report?period=month&value=2026-99').status_code==400
    assert len(period_bounds('month','2024-02')[2])==29
    assert len(c.get('/api/console/report?period=year&value='+str(day.year)).json()['trend'])==12

def test_old_store_backup_does_not_replace_history_accounts_or_grants(env):
    app,c=env;login(c);unlock(c);aid=create_account(c)
    c.post('/api/tickets',json={'store_id':1,'service_type':'simple_service'})
    legacy={'version':2,'stores':[{'id':1,'name':'강남변경','code':'GN01','is_active':True}]}
    r=c.post('/api/admin/restore',files={'file':('old.zip',pack(legacy),'application/zip')});assert r.status_code==200,r.text
    assert r.json()['kind']=='stores'
    with app.state.SessionLocal() as db:
        assert db.get(Store,1).name=='강남변경'
        assert db.scalar(select(func.count(Ticket.id)))==1
        assert db.get(ConsoleAccount,aid) is not None
        assert db.scalar(select(func.count()).select_from(ConsoleGrant))==1

def test_all_new_backups_full_and_restore_preserves_accounts_and_history(env):
    app,c=env;login(c);unlock(c);create_account(c)
    branch=TestClient(app)
    assert branch.post('/api/console/login',json={'mode':'branch','username':'GN01','password':'samsung-branch-test'}).status_code==200
    c.post('/api/tickets',json={'store_id':1,'service_type':'simple_service','source':'서울영업팀'})
    c.post('/api/traffic',json={'id':'traffic_1234567890','store_id':1,'path':'/customer','source':'qr'})
    backup=c.get('/api/admin/backup');assert backup.status_code==200
    with zipfile.ZipFile(io.BytesIO(backup.content)) as z:
        data=json.loads(z.read('data.json'));assert data['version']==3
        assert len(data['tables']['tickets'])==1 and len(data['tables']['console_accounts'])==2
        assert 'samsung-branch-test' not in json.dumps(data) and 'common-GCGC-test' not in json.dumps(data)
    c.delete('/api/admin/stores/1')
    restored=c.post('/api/admin/restore',headers={'X-Restore-Confirm':'REPLACE'},files={'file':('full.zip',backup.content,'application/zip')})
    assert restored.status_code==200,restored.text
    assert c.get('/api/console/me').status_code==401
    assert c.post('/api/console/login',json={'mode':'branch','username':'GN01','password':'samsung-branch-test'}).status_code==200
    report=c.get('/api/console/report').json();assert report['summary']['issued']==1 and report['traffic_views']==1

def test_invalid_full_backup_rolls_back_without_partial_replacement(env):
    app,c=env;login(c);unlock(c)
    backup=c.get('/api/console/backup')
    with zipfile.ZipFile(io.BytesIO(backup.content)) as z:data=json.loads(z.read('data.json'))
    data['tables']['console_grants']=[{'account_id':999,'store_id':1}]
    response=c.post('/api/console/restore',headers={'X-Restore-Confirm':'REPLACE'},files={'file':('broken.zip',pack(data),'application/zip')})
    assert response.status_code==400
    assert len(c.get('/api/console/me').json()['stores'])==3

def test_retention_calendar_boundary_keeps_config_and_removes_oldest(env):
    app,c=env;login(c);unlock(c);create_account(c)
    now=datetime(2026,10,13,12,tzinfo=KST);cutoff=retention_cutoff(now)
    assert cutoff==datetime(2026,4,12,15)
    assert retention_cutoff(datetime(2024,8,31,tzinfo=KST))==datetime(2024,2,28,15)
    with app.state.SessionLocal() as db:
        for n,when in enumerate([cutoff-timedelta(days=20),cutoff-timedelta(seconds=1),cutoff],1):
            t=Ticket(store_id=1,service_type='simple_service',round_no=1,ticket_number=n,status='called',created_at=when,called_at=when+timedelta(seconds=60));db.add(t)
            db.add(TicketAttribution(ticket=t,source='direct'))
            db.add(TrafficEvent(id=f'v{n}',store_id=1,path='/customer',source='qr',created_at=when))
            db.add(CallLog(store_id=1,service_type='simple_service',round_no=1,call_type='normal',ticket_number=n,created_at=when))
        db.add(StoreProfile(store_id=1,sales_team='서울영업팀'))
        db.commit()
        result=purge_old_activity(db,now,batch_size=1)
        assert result['deleted']['tickets']==2
        assert db.scalar(select(func.count()).select_from(TicketAttribution))==1
        assert db.scalar(select(func.count(TrafficEvent.id)))==1
        assert db.scalar(select(func.count(Store.id)))==3
        assert db.scalar(select(func.count(ConsoleAccount.id)))==1
        assert db.scalar(select(func.count()).select_from(ConsoleGrant))==1
        assert db.get(StoreProfile,1).sales_team=='서울영업팀'

def test_restored_old_activity_is_not_reintroduced(env):
    app,c=env;login(c);unlock(c)
    with app.state.SessionLocal() as db:
        db.add(Ticket(store_id=1,service_type='simple_service',round_no=1,ticket_number=1,status='called',created_at=datetime(2000,1,1)));db.commit()
    backup=c.get('/api/console/backup').content
    r=c.post('/api/console/restore',headers={'X-Restore-Confirm':'REPLACE'},files={'file':('full.zip',backup,'application/zip')})
    assert r.status_code==200
    with app.state.SessionLocal() as db:assert db.scalar(select(func.count(Ticket.id)))==0

def test_traffic_is_idempotent_and_stored_per_store(env):
    app,c=env;login(c)
    body={'id':'a1234567890abcdef','store_id':1,'path':'/customer','source':'qr'}
    assert c.post('/api/traffic',json=body).status_code==201
    assert c.post('/api/traffic',json=body).status_code==201
    assert c.get('/api/console/report').json()['traffic_views']==1
    assert c.post('/api/traffic',json={**body,'id':'b1234567890abcdef','store_id':999}).status_code==404

def test_real_sqlite_restart_preserves_credentials(tmp_path):
    url='sqlite:///'+str(tmp_path/'data.db');app=create_app(config(url))
    with TestClient(app) as c:
        with app.state.SessionLocal() as db:db.add(Store(name='재시작점',code='RE01'));db.commit()
        login(c);unlock(c)
        assert c.post('/api/console/login',json={'mode':'branch','username':'RE01','password':'samsung-branch-test'}).status_code==200
        c.post('/api/tickets',json={'store_id':1,'service_type':'simple_service'})
    app.state.engine.dispose()
    upgraded=create_app(config(url))
    with TestClient(upgraded) as c:
        assert c.post('/api/console/login',json={'mode':'branch','username':'RE01','password':'samsung-branch-test'}).status_code==200
        assert c.get('/api/console/report').json()['summary']['issued']==1


def test_video_folder_playlist_upload_range_delete_and_backup(env):
    app,c=env;login(c);unlock(c)
    assert c.get('/video').status_code==200
    assert c.get('/api/video-playlist').json()['videos']==[]
    mp4=b'\x00\x00\x00\x18ftypmp42'+b'\x00'*100
    response=c.post('/api/console/video',files=[('files',('02_second.mp4',mp4,'video/mp4')),('files',('01_first.mp4',mp4,'video/mp4'))])
    assert response.status_code==200,response.text
    playlist=c.get('/api/video-playlist').json()['videos']
    assert [v['name'] for v in playlist]==['01_first.mp4','02_second.mp4']
    media=c.get(playlist[0]['url'],headers={'Range':'bytes=0-7'})
    assert media.status_code==206 and len(media.content)==8
    backup=c.get('/api/console/backup').content
    with zipfile.ZipFile(io.BytesIO(backup)) as z:
        data=json.loads(z.read('data.json'));assert len(data['video_files'])==2
    assert c.delete('/api/console/video').status_code==200
    assert c.get('/api/video-playlist').json()['videos']==[]
    r=c.post('/api/console/restore',headers={'X-Restore-Confirm':'REPLACE'},files={'file':('backup.zip',backup,'application/zip')})
    assert r.status_code==200,r.text
    assert len(c.get('/api/video-playlist').json()['videos'])==2


def test_video_restricted_manager_cannot_change_company_wide_video(env):
    app,c=env;login(c);unlock(c);create_account(c)
    login(c,'서울담당')
    assert c.delete('/api/console/video').status_code==403
    assert c.post('/api/console/video',files={'files':('a.mp4',b'\x00\x00\x00\x18ftypmp42'+b'0'*50,'video/mp4')}).status_code==403


def test_restore_invalid_video_does_not_mutate_db_or_files(env):
    app,c=env;login(c);unlock(c)
    directory=app.state.video_directory;directory.mkdir();(directory/'current.mp4').write_bytes(b'current')
    original=c.get('/api/console/backup').content
    with zipfile.ZipFile(io.BytesIO(original)) as z:data=json.loads(z.read('data.json'))
    data['video_files']=[{'name':'../escape.mp4','archive_path':'videos/../escape.mp4'}]
    r=c.post('/api/console/restore',headers={'X-Restore-Confirm':'REPLACE'},files={'file':('bad.zip',pack(data),'application/zip')})
    assert r.status_code==400
    assert (directory/'current.mp4').read_bytes()==b'current'
    assert len(c.get('/api/console/me').json()['stores'])==3


def test_only_owner_can_open_management_even_with_valid_management_key(env):
    app,c=env
    assert c.post('/api/admin/manage/login',json={'key':'kiosk-manage-test'}).status_code==401
    login(c);unlock(c);create_account(c)
    assert login(c,'서울담당').status_code==200
    assert c.post('/api/admin/manage/login',json={'key':'kiosk-manage-test'}).status_code==403
    assert c.post('/api/admin/stores',json={'name':'불가','code':'NO'}).status_code==403
    assert c.post('/api/console/access/unlock',json={'key':'gcs-access-test'}).status_code==403
    assert c.get('/api/console/videos').status_code==403


def test_report_rejects_expired_dates_and_clips_partial_month(env):
    app,c=env;login(c)
    cutoff=retention_cutoff();cutoff_day=cutoff.replace(tzinfo=UTC).astimezone(KST).date()
    today=datetime.now(KST).date()
    assert c.get(f'/api/console/report?value={cutoff_day-timedelta(days=1)}').status_code==400
    assert c.get(f'/api/console/report?value={today+timedelta(days=1)}').status_code==400
    assert c.get('/api/console/me').json()['min_date']==str(cutoff_day)
    with app.state.SessionLocal() as db:
        for n,at in enumerate([cutoff-timedelta(seconds=1),cutoff],1):
            db.add(Ticket(store_id=1,service_type='simple_service',round_no=1,ticket_number=n,status='waiting',created_at=at))
        db.commit()
    report=c.get(f'/api/console/report?period=month&value={str(cutoff_day)[:7]}')
    assert report.status_code==200 and report.json()['summary']['issued']==1


def test_authentication_errors_do_not_disclose_key_markers(env):
    _,c=env
    assert login(c,password='gcgc-wrong-case').json()['detail']=='인증키를 입력해 주세요'
    login(c)
    assert c.post('/api/admin/manage/login',json={'key':'common-GCGC-test'}).json()['detail']=='인증키를 입력해 주세요'
    c.post('/api/admin/manage/login',json={'key':'kiosk-manage-test'})
    assert c.post('/api/console/access/unlock',json={'key':'common-GCGC-test'}).json()['detail']=='인증키를 입력해 주세요'


def test_old_database_upgrades_without_losing_store_or_ticket(tmp_path):
    from sqlalchemy import create_engine, inspect
    from app.database import Base
    url='sqlite:///'+str(tmp_path/'old.db');engine=create_engine(url)
    Base.metadata.create_all(engine,tables=[t for t in Base.metadata.sorted_tables if not t.name.startswith('console_')])
    with engine.begin() as conn:
        conn.execute(Store.__table__.insert().values(id=42,name='이전매장',code='OLD42'))
        conn.execute(Ticket.__table__.insert().values(store_id=42,service_type='simple_service',round_no=1,ticket_number=1,status='called',created_at=utc_now()-timedelta(minutes=2),called_at=utc_now()))
    engine.dispose()
    app=create_app({**config(url),'VIDEO_DIRECTORY':str(tmp_path/'videos')})
    with TestClient(app) as c:
        assert login(c).status_code==200
        r=c.get('/api/console/report').json()
        assert r['summary']['issued']==1 and r['stores'][0]['code']=='OLD42'
        assert 'console_accounts' in inspect(app.state.engine).get_table_names()
        assert 'ix_tickets_store_created' in {i['name'] for i in inspect(app.state.engine).get_indexes('tickets')}


def test_legacy_restore_invalid_is_atomic_and_code_swaps_work(env):
    app,c=env;login(c);unlock(c)
    bad={'version':2,'stores':[{'id':1,'name':'바뀌면안됨','code':'GN01'},{'id':2,'name':'잘못된값','code':'GN01'}]}
    assert c.post('/api/admin/restore',files={'file':('old.zip',pack(bad),'application/zip')}).status_code==400
    with app.state.SessionLocal() as db:assert db.get(Store,1).name=='강남점'
    swap={'version':2,'stores':[{'id':1,'name':'강남점','code':'IC02'},{'id':2,'name':'인천점','code':'GN01'}]}
    assert c.post('/api/admin/restore',files={'file':('old.zip',pack(swap),'application/zip')}).status_code==200
    with app.state.SessionLocal() as db:
        assert db.get(Store,1).code=='IC02' and db.get(Store,2).code=='GN01'


@pytest.mark.parametrize('name',['gc','Gc','gC',''])
def test_fixed_owner_name_requires_uppercase_gc(env,name):
    _,c=env
    assert login(c,name=name).status_code==401
    assert login(c,name='GC').status_code==200
    assert c.get('/api/console/me').json()['user']['role']=='root'
