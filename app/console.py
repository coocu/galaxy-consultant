"""지점·전체 관리자 콘솔 API. 기존 호출 관리자 인증과 별개로 권한을 검사한다."""
from __future__ import annotations
import re
from datetime import datetime
from fastapi import Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, delete, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .database import get_db
from .models import Store, utc_now
from .console_models import ConsoleAccount, ConsoleGrant, StoreProfile, ConsoleAudit, TrafficEvent
from . import console_auth as auth
from .console_reports import build_report, KST
from .console_backup import make_full_backup, restore_full_backup, MAX_ZIP_BYTES


class LoginBody(BaseModel):
    mode: str = Field(pattern='^(branch|integrated)$')
    username: str = Field(default='',max_length=80)
    password: str = Field(min_length=1,max_length=512)
    name: str = Field(default='',max_length=80)

class KeyBody(BaseModel):
    key: str = Field(min_length=1,max_length=512)

class AccountBody(BaseModel):
    name: str = Field(min_length=1,max_length=80)
    store_ids: list[int] = Field(default_factory=list,max_length=2000)
    is_active: bool = True

class TeamBody(BaseModel):
    store_ids: list[int] = Field(min_length=1,max_length=2000)
    sales_team: str = Field(max_length=80)

class TrafficBody(BaseModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{16,64}$')
    store_id: int = Field(gt=0)
    path: str = Field(pattern=r'^/(customer|display)$')
    source: str = Field(default='direct',max_length=120)


def audit(db,user,action,detail=''):
    db.add(ConsoleAudit(actor=user['name'],action=action,detail=detail))


def store_payload(store,profile=None):
    return {'id':store.id,'name':store.name,'code':store.code,'is_active':store.is_active,
            'sales_team':profile.sales_team if profile else ''}


def selected_store_ids(db,user,scope='all',store_id=None,team=''):
    ids=auth.allowed_stores(db,user)
    if scope=='store':
        if store_id not in ids: raise HTTPException(403,'이 지점을 조회할 권한이 없습니다')
        return [store_id]
    if scope=='team':
        query=select(Store.id).outerjoin(StoreProfile,Store.id==StoreProfile.store_id).where(Store.id.in_(ids))
        query=query.where(func.coalesce(StoreProfile.sales_team,'')==('' if team=='미지정' else team))
        selected=list(db.scalars(query))
        if not selected: raise HTTPException(403,'조회 가능한 영업팀이 아닙니다')
        return selected
    if scope!='all': raise HTTPException(400,'조회 범위를 선택하세요')
    return ids


def validate_stores(db,ids):
    selected=sorted(set(ids))
    existing=list(db.scalars(select(Store.id).where(Store.id.in_(selected))))
    if len(existing)!=len(selected): raise HTTPException(400,'삭제되거나 존재하지 않는 매장이 포함되어 있습니다')
    return selected


def register_console(app, config, templates, verify_key, require_manager):
    auth.install_auth(app,config)

    @app.middleware('http')
    async def console_headers(request,call_next):
        response=await call_next(request)
        if request.url.path.startswith(('/console','/api/console')):
            response.headers['Cache-Control']='no-store'
            response.headers['X-Content-Type-Options']='nosniff'
            response.headers['Referrer-Policy']='same-origin'
            if request.url.path=='/console':
                response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; media-src 'self' https: blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        return response

    @app.get('/console',response_class=HTMLResponse)
    def console_page(request:Request):
        return templates.TemplateResponse(request,'console.html',{'request':request})

    @app.post('/api/console/login')
    def login(body:LoginBody,request:Request,db:Session=Depends(get_db)):
        name=body.username.strip() if body.mode=='branch' else body.name.strip()
        identity=auth.throttle(request,f'{body.mode}:{name}')
        account=None
        root=(body.mode=='integrated' and name==auth.ROOT_NAME)
        password=body.password.strip()
        if body.mode=='branch':
            store=db.scalar(select(Store).where(Store.code==name.upper(),Store.is_active.is_(True)))
            if not store: raise HTTPException(401,'점코드를 확인해 주세요')
            if 'samsung' not in password.casefold(): raise HTTPException(401,'인증키를 입력해 주세요')
            verify_key(request.app,password)
            account=db.scalar(select(ConsoleAccount).where(ConsoleAccount.store_id==store.id,ConsoleAccount.role=='branch'))
            if account and not account.is_active: raise HTTPException(403,'사용할 수 없는 관리자입니다')
            if not account:
                account=ConsoleAccount(username=f'branch:{store.id}',display_name=store.name,role='branch',store_id=store.id,password_hash='')
                db.add(account);db.flush()
            account.display_name=store.name
        else:
            if 'GCGC' not in password:
                raise HTTPException(401,'인증키를 입력해 주세요')
            if not root:
                account=db.scalar(select(ConsoleAccount).where(ConsoleAccount.username==name,ConsoleAccount.role=='integrated'))
                if not account or not account.is_active:
                    raise HTTPException(401,'매장관리에 등록된 이름만 로그인할 수 있습니다')
            # 요청한 공통 인증키 방식: 기존 인증 서버의 승인 상태를 확인한다.
            verify_key(request.app,password)
        auth.login_succeeded(request,identity)
        response=JSONResponse({'ok':True})
        auth.issue_session(request,response,account.id if account else None,account.session_version if account else 0,root)
        audit(db,{'name':auth.ROOT_NAME if root else account.display_name},'login',body.mode)
        db.commit()
        return response

    @app.get('/api/console/me')
    def me(request:Request,db:Session=Depends(get_db)):
        user=auth.principal(request,db)
        ids=auth.allowed_stores(db,user)
        rows=db.execute(select(Store,StoreProfile).outerjoin(StoreProfile,Store.id==StoreProfile.store_id)
                        .where(Store.id.in_(ids)).order_by(Store.name)).all()
        from .retention import retention_cutoff
        from datetime import UTC
        return {'min_date':retention_cutoff().replace(tzinfo=UTC).astimezone(KST).date().isoformat(),'user':user,'stores':[store_payload(s,p) for s,p in rows],'gcs_unlocked':auth.has_gcs(request),
                'today':datetime.now(KST).date().isoformat()}

    @app.post('/api/console/logout')
    def logout(request:Request,db:Session=Depends(get_db)):
        auth.principal(request,db)
        with request.app.state.console_auth_lock:
            request.app.state.console_sessions.pop(request.cookies.get(auth.COOKIE,''),None)
            request.app.state.gcs_sessions.pop(request.cookies.get(auth.GCS_COOKIE,''),None)
        response=JSONResponse({'ok':True})
        response.delete_cookie(auth.COOKIE,path='/');response.delete_cookie(auth.GCS_COOKIE,path='/')
        # 같은 브라우저에서 다음 사용자가 매장관리 인증을 이어받지 않게 종료한다.
        with request.app.state.manage_session_lock:
            request.app.state.manage_sessions.pop(request.cookies.get('codenote_staff_call_manage_session',''),None)
        response.delete_cookie('codenote_staff_call_manage_session',path='/')
        return response

    @app.get('/api/console/report')
    def report(request:Request,period:str='day',value:str='',scope:str='all',store_id:int|None=None,team:str='',db:Session=Depends(get_db)):
        user=auth.principal(request,db)
        ids=selected_store_ids(db,user,scope,store_id,team)
        if not value: value=datetime.now(KST).strftime({'day':'%Y-%m-%d','month':'%Y-%m','year':'%Y'}.get(period,'%Y-%m-%d'))
        try: return build_report(db,ids,period,value)
        except ValueError as exc: raise HTTPException(400,str(exc)) from exc

    @app.post('/api/console/access/unlock')
    def unlock(body:KeyBody,request:Request,db:Session=Depends(get_db)):
        user=auth.principal(request,db)
        require_manager(request)
        identity=auth.throttle(request,'gcs-access')
        if 'gcs' not in body.key.casefold(): raise HTTPException(401,'인증키를 입력해 주세요')
        verify_key(request.app,body.key.strip())
        auth.login_succeeded(request,identity)
        response=JSONResponse({'ok':True})
        auth.grant_gcs(request,response,user)
        audit(db,user,'gcs_unlock');db.commit()
        return response

    @app.get('/api/console/access')
    def access(request:Request,db:Session=Depends(get_db)):
        auth.require_gcs(request,db)
        accounts=list(db.scalars(select(ConsoleAccount).order_by(ConsoleAccount.display_name)))
        rows=db.execute(select(Store,StoreProfile).outerjoin(StoreProfile,Store.id==StoreProfile.store_id).order_by(Store.name)).all()
        return {'root':{'name':auth.ROOT_NAME,'username':auth.ROOT_NAME,'fixed':True},
                'accounts':[{'id':a.id,'name':a.display_name,'username':a.username,'role':a.role,'store_id':a.store_id,
                    'is_active':a.is_active,'store_ids':[g.store_id for g in a.grants]} for a in accounts],
                'stores':[store_payload(s,p) for s,p in rows]}

    def save_account(body,request,db,account_id=None):
        user=auth.require_gcs(request,db)
        name=body.name.strip()
        if not name or (name == auth.ROOT_NAME or name.startswith('branch:')): raise HTTPException(400,'고정 관리자를 제외한 이름을 입력하세요')
        ids=validate_stores(db,body.store_ids)
        if not ids: raise HTTPException(400,'조회할 매장을 한 곳 이상 선택하세요')
        account=db.get(ConsoleAccount,account_id) if account_id else None
        if account_id and (not account or account.role!='integrated'): raise HTTPException(404,'전체 관리자를 찾을 수 없습니다')
        duplicate=db.scalar(select(ConsoleAccount).where(ConsoleAccount.username==name))
        if duplicate and duplicate.id!=account_id: raise HTTPException(400,'이미 등록된 이름입니다')
        if not account:
            account=ConsoleAccount(username=name,display_name=name,role='integrated',password_hash='' )
            db.add(account);db.flush()
        account.username=name;account.display_name=name;account.is_active=body.is_active
        account.session_version=(account.session_version or 0)+1
        db.execute(delete(ConsoleGrant).where(ConsoleGrant.account_id==account.id))
        db.add_all(ConsoleGrant(account_id=account.id,store_id=s) for s in ids)
        audit(db,user,'account_saved',f'{name}: {len(ids)}개 지점')
        db.commit()
        return {'ok':True,'id':account.id}

    @app.post('/api/console/accounts')
    def add_account(body:AccountBody,request:Request,db:Session=Depends(get_db)):
        return save_account(body,request,db)

    @app.put('/api/console/accounts/{account_id}')
    def edit_account(account_id:int,body:AccountBody,request:Request,db:Session=Depends(get_db)):
        return save_account(body,request,db,account_id)

    @app.delete('/api/console/accounts/{account_id}')
    def delete_account(account_id:int,request:Request,db:Session=Depends(get_db)):
        user=auth.require_gcs(request,db)
        account=db.get(ConsoleAccount,account_id)
        if not account or account.role!='integrated': raise HTTPException(404,'삭제할 전체 관리자가 없습니다')
        audit(db,user,'account_deleted',account.display_name)
        db.delete(account);db.commit()
        return {'ok':True}

    @app.put('/api/console/store-teams')
    def update_teams(body:TeamBody,request:Request,db:Session=Depends(get_db)):
        user=auth.require_gcs(request,db)
        ids=validate_stores(db,body.store_ids)
        for sid in ids:
            profile=db.get(StoreProfile,sid)
            if not profile: profile=StoreProfile(store_id=sid);db.add(profile)
            profile.sales_team=body.sales_team.strip()
        audit(db,user,'team_assigned',f'{body.sales_team.strip() or "미지정"}: {len(ids)}개 지점');db.commit()
        return {'ok':True}

    @app.get('/api/console/backup')
    def full_backup(request:Request,db:Session=Depends(get_db)):
        auth.require_gcs(request,db)
        try: content=make_full_backup(db,request.app.state.video_directory)
        except ValueError as exc: raise HTTPException(400,str(exc)) from exc
        name=datetime.now(KST).strftime('galaxy_consultant_full_%Y%m%d_%H%M%S.zip')
        return Response(content,media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="{name}"'})

    @app.post('/api/console/restore')
    def full_restore(request:Request,file:UploadFile=File(...),db:Session=Depends(get_db)):
        auth.require_gcs(request,db)
        if request.headers.get('x-restore-confirm')!='REPLACE': raise HTTPException(400,'현재 데이터 교체에 동의해야 합니다')
        content=file.file.read(MAX_ZIP_BYTES+1)
        # principal 조회에서 열린 읽기 트랜잭션을 정리하고 복원 전체를 한 트랜잭션으로 실행한다.
        db.rollback()
        from .backup import restore_from_zip_bytes
        try: result=restore_from_zip_bytes(db,content,request.app.state.video_directory)
        except ValueError as exc:
            db.rollback();raise HTTPException(400,str(exc)) from exc
        with request.app.state.console_auth_lock:
            request.app.state.console_sessions.clear();request.app.state.gcs_sessions.clear()
        with request.app.state.admin_session_lock: request.app.state.admin_sessions.clear()
        with request.app.state.manage_session_lock: request.app.state.manage_sessions.clear()
        return {'ok':True,'result':result,'message':'ZIP 데이터가 복원되었습니다. 다시 로그인하세요'}

    @app.get('/api/console/videos')
    def videos(request:Request,db:Session=Depends(get_db)):
        user=auth.principal(request,db)
        require_manager(request)
        from .video import video_files
        files=video_files(request.app.state.video_directory)
        return {'videos':[{'name':p.name,'size':p.stat().st_size} for p in files], 'can_edit':user['role']=='root'}

    @app.post('/api/console/video')
    def replace_videos(request:Request,files:list[UploadFile]=File(...),db:Session=Depends(get_db)):
        user=auth.principal(request,db)
        if user['role']!='root': raise HTTPException(403,'전 매장 공통 영상은 전체 권한 전체 관리자만 변경할 수 있습니다')
        require_manager(request)
        from .video import valid_filename, replace_video_folder, MAX_VIDEO_BYTES, MAX_VIDEO_TOTAL
        contents={};total=0
        for file in files:
            name=file.filename or ''
            if not valid_filename(name): raise HTTPException(400,'MP4 / WebM / M4V 영상 파일만 선택하세요')
            if name in contents: raise HTTPException(400,'파일명이 중복됩니다')
            content=file.file.read(MAX_VIDEO_BYTES+1);total+=len(content)
            if len(content)>MAX_VIDEO_BYTES or total>MAX_VIDEO_TOTAL: raise HTTPException(413,'전체 영상 합계를 100MB 이하로 올려 주세요')
            is_mp4=len(content)>12 and content[4:8]==b'ftyp'
            is_webm=content.startswith(b'\x1a\x45\xdf\xa3')
            if not(is_mp4 or is_webm): raise HTTPException(400,'영상 파일 내용을 확인할 수 없습니다')
            contents[name]=content
        with replace_video_folder(contents,request.app.state.video_directory):
            audit(db,user,'video_replaced',f'{len(contents)}개 영상');db.commit()
        return {'ok':True,'videos':len(contents)}

    @app.delete('/api/console/video')
    def clear_videos(request:Request,db:Session=Depends(get_db)):
        user=auth.principal(request,db)
        if user['role']!='root': raise HTTPException(403,'전 매장 공통 영상은 전체 권한 전체 관리자만 변경할 수 있습니다')
        require_manager(request)
        from .video import replace_video_folder
        with replace_video_folder({},request.app.state.video_directory):
            audit(db,user,'video_cleared');db.commit()
        return {'ok':True}

    @app.post('/api/traffic',status_code=201)
    def traffic(body:TrafficBody,db:Session=Depends(get_db)):
        store=db.get(Store,body.store_id)
        if not store or not store.is_active: raise HTTPException(404,'매장을 찾을 수 없습니다')
        if db.get(TrafficEvent,body.id): return {'ok':True}
        source=re.sub(r'[^\w .:/@+-]','',body.source)[:120] or 'direct'
        db.add(TrafficEvent(id=body.id,store_id=body.store_id,source=source,path=body.path))
        try: db.commit()
        except IntegrityError: db.rollback()
        return {'ok':True}
