"""전 매장 공통 영상 폴더와 /video 플레이어."""
from __future__ import annotations
import os
import shutil
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse

VIDEO_DIR = Path(__file__).resolve().parent / 'static' / 'video'
VIDEO_LOCK = threading.RLock()
VIDEO_EXTENSIONS = {'.mp4','.webm','.m4v'}
MAX_VIDEO_BYTES = 100*1024*1024
MAX_VIDEO_TOTAL = 100*1024*1024


def video_files(directory=VIDEO_DIR):
    path=Path(directory)
    if not path.exists(): return []
    return sorted([p for p in path.iterdir() if p.is_file() and not p.is_symlink() and p.suffix.lower() in VIDEO_EXTENSIONS],key=lambda p:p.name.casefold())


def valid_filename(name):
    return isinstance(name,str) and name==Path(name).name and not name.startswith('.') and '/' not in name and '\\' not in name and len(name)<=160 and Path(name).suffix.lower() in VIDEO_EXTENSIONS


@contextmanager
def replace_video_folder(files, directory=VIDEO_DIR):
    """동일 디스크에서 폴더 교체. 호출자가 DB 복원 실패 시 폴더도 되돌린다."""
    directory=Path(directory);directory.parent.mkdir(parents=True,exist_ok=True)
    with VIDEO_LOCK:
        stage=Path(tempfile.mkdtemp(prefix='.video-new-',dir=directory.parent))
        old=Path(tempfile.mkdtemp(prefix='.video-old-',dir=directory.parent));old.rmdir()
        try:
            for name,content in files.items():
                if not valid_filename(name): raise ValueError('올바르지 않은 영상 파일명입니다')
                (stage/name).write_bytes(content)
            existed=directory.exists()
            if existed: directory.rename(old)
            try:
                stage.rename(directory)
                yield
            except BaseException:
                if directory.exists(): shutil.rmtree(directory)
                if existed and old.exists(): old.rename(directory)
                raise
        finally:
            if stage.exists(): shutil.rmtree(stage)
            if old.exists(): shutil.rmtree(old)


def register_video(app,templates,config):
    directory=Path(config.get('VIDEO_DIRECTORY') or VIDEO_DIR)
    app.state.video_directory=directory

    @app.get('/video')
    def video_page(request:Request):
        response=templates.TemplateResponse(request,'video.html',{'request':request})
        response.headers['Cache-Control']='no-store'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; media-src 'self'; object-src 'none'; base-uri 'none'"
        return response

    @app.get('/api/video-playlist')
    def playlist():
        with VIDEO_LOCK:
            files=video_files(directory)
            data=[{'name':p.name,'url':'/video-files/'+quote(p.name)+'?v='+str(p.stat().st_mtime_ns),'version':str(p.stat().st_mtime_ns)} for p in files]
        from fastapi.responses import JSONResponse
        return JSONResponse({'videos':data},headers={'Cache-Control':'no-store'})

    @app.get('/video-files/{filename}')
    def video_file(filename:str):
        if not valid_filename(filename): raise HTTPException(404)
        path=directory/filename
        if not path.is_file() or path.is_symlink(): raise HTTPException(404)
        return FileResponse(path,media_type='video/webm' if path.suffix.lower()=='.webm' else 'video/mp4',headers={'Cache-Control':'no-cache','X-Content-Type-Options':'nosniff'})
