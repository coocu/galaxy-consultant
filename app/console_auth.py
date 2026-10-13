"""콘솔 로그인, 이름별 권한, 일시적 GCS 재인증."""
from __future__ import annotations
import hashlib
import hmac
import secrets
import threading
import time
from fastapi import HTTPException, Request
from sqlalchemy import select
from .console_models import ConsoleAccount, ConsoleGrant
from .models import Store

ROOT_NAME = 'GC'
COOKIE = 'codenote_console'
GCS_COOKIE = 'codenote_gcs_access'
SESSION_SECONDS = 8*60*60
GCS_SECONDS = 10*60


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),210_000).hex()
    return f'pbkdf2_sha256$210000${salt}${digest}'


def verify_password(password, encoded):
    try:
        scheme, iterations, salt, digest = encoded.split('$')
        rounds = int(iterations)
        if scheme != 'pbkdf2_sha256' or not 100_000 <= rounds <= 600_000: return False
        candidate = hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),rounds).hex()
        return hmac.compare_digest(candidate,digest)
    except (ValueError, TypeError):
        return False


def install_auth(app, config):
    app.state.console_sessions = {}
    app.state.gcs_sessions = {}
    app.state.console_auth_lock = threading.RLock()
    app.state.console_attempts = {}


def _cleanup(mapping):
    now = time.time()
    for key in [k for k,v in mapping.items() if v['expires']<=now]: mapping.pop(key,None)


def throttle(request, identity):
    now = time.time()
    peer = request.client.host if request.client else 'unknown'
    keys = [(f'name:{identity.casefold()}',5),(f'ip:{peer}',40)]
    with request.app.state.console_auth_lock:
        records=request.app.state.console_attempts
        for k in list(records):
            records[k] = [t for t in records[k] if now-t<900]
            if not records[k]: records.pop(k)
        for key,limit in keys:
            if len(records.get(key,[]))>=limit:
                raise HTTPException(429,'인증 시도가 여러 번 실패했습니다. 15분 후 다시 시도하세요', headers={'Retry-After':'900'})
        # 진행 중인 시도도 세어 동시 요청에 의한 우회를 차단한다.
        for key,_ in keys: records.setdefault(key,[]).append(now)
    return keys[0][0], keys[1][0], now


def login_succeeded(request, attempt):
    identity_key, ip_key, timestamp = attempt
    with request.app.state.console_auth_lock:
        records = request.app.state.console_attempts
        records.pop(identity_key,None)
        records[ip_key] = [t for t in records.get(ip_key,[]) if t != timestamp]
        if not records[ip_key]: records.pop(ip_key,None)


def issue_session(request, response, account_id=None, version=0, root=False):
    token=secrets.token_urlsafe(32)
    data={'account_id':account_id,'version':version,'root':root,'csrf':secrets.token_urlsafe(24),'expires':time.time()+SESSION_SECONDS}
    with request.app.state.console_auth_lock:
        _cleanup(request.app.state.console_sessions)
        old=request.cookies.get(COOKIE)
        request.app.state.console_sessions.pop(old,None)
        request.app.state.console_sessions[token]=data
        request.app.state.gcs_sessions.pop(request.cookies.get(GCS_COOKIE),None)
    _cookie(response,request,COOKIE,token,SESSION_SECONDS)
    response.delete_cookie(GCS_COOKIE,path='/')
    with request.app.state.manage_session_lock:
        request.app.state.manage_sessions.pop(request.cookies.get('codenote_staff_call_manage_session',''),None)
    response.delete_cookie('codenote_staff_call_manage_session',path='/')
    return data


def _cookie(response,request,name,token,max_age):
    response.set_cookie(name,token,max_age=max_age,httponly=True,samesite='strict',path='/',
                        secure=(not request.app.state.settings.testing and request.headers.get('x-forwarded-proto',request.url.scheme)=='https'))


def principal(request: Request, db):
    with request.app.state.console_auth_lock:
        _cleanup(request.app.state.console_sessions)
        data=request.app.state.console_sessions.get(request.cookies.get(COOKIE,''))
    if not data: raise HTTPException(401,'관리자 로그인이 필요합니다')
    if request.method not in {'GET','HEAD','OPTIONS'} and not hmac.compare_digest(request.headers.get('x-console-csrf',''),data['csrf']):
        raise HTTPException(403,'화면을 새로고침한 뒤 다시 시도하세요')
    if data['root']:
        return {'role':'root','name':ROOT_NAME,'username':ROOT_NAME,'account_id':None,'csrf':data['csrf']}
    account=db.get(ConsoleAccount,data['account_id'])
    if not account or not account.is_active or account.session_version!=data['version']:
        raise HTTPException(401,'관리자 권한이 변경되었습니다. 다시 로그인하세요')
    if account.role=='branch':
        store=db.get(Store,account.store_id)
        if not store or not store.is_active: raise HTTPException(403,'사용할 수 없는 지점입니다')
    return {'role':account.role,'name':account.display_name,'username':account.username,
            'account_id':account.id,'store_id':account.store_id,'csrf':data['csrf']}


def allowed_stores(db, user):
    query=select(Store.id)
    if user['role']=='branch': query=query.where(Store.id==user['store_id'])
    elif user['role']=='integrated': query=query.where(Store.id.in_(select(ConsoleGrant.store_id).where(ConsoleGrant.account_id==user['account_id'])))
    return list(db.scalars(query))


def grant_gcs(request,response,user):
    token=secrets.token_urlsafe(32)
    with request.app.state.console_auth_lock:
        _cleanup(request.app.state.gcs_sessions)
        request.app.state.gcs_sessions[token]={'session':request.cookies.get(COOKIE),'expires':time.time()+GCS_SECONDS}
    _cookie(response,request,GCS_COOKIE,token,GCS_SECONDS)


def has_gcs(request):
    with request.app.state.console_auth_lock:
        _cleanup(request.app.state.gcs_sessions)
        data=request.app.state.gcs_sessions.get(request.cookies.get(GCS_COOKIE,''))
        return bool(data and data['session']==request.cookies.get(COOKIE))


def require_gcs(request,db):
    user=principal(request,db)
    if user['role'] != 'root': raise HTTPException(403,'매장관리는 전체 권한 관리자만 사용할 수 있습니다')
    if not has_gcs(request): raise HTTPException(403,'인증키를 입력해 주세요')
    return user
