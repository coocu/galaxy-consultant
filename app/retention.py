"""누적 이력만 한국 시간 기준 최근 6개월 유지. 매장·계정·권한·영상은 제외."""
from __future__ import annotations
import asyncio
import calendar
import logging
from datetime import datetime, timedelta, time, UTC
from zoneinfo import ZoneInfo
from sqlalchemy import select, delete
from .database import Base

KST = ZoneInfo('Asia/Seoul')
ACTIVITY_TABLES = ('console_ticket_attribution', 'tickets', 'call_logs', 'console_traffic_events',
                   'console_audit', 'manager_mobile_deliveries')


def retention_cutoff(now=None):
    current=(now or datetime.now(KST))
    if current.tzinfo is None: current=current.replace(tzinfo=KST)
    current=current.astimezone(KST)
    month_index=current.year*12+current.month-1-6
    year,month=divmod(month_index,12);month+=1
    day=min(current.day,calendar.monthrange(year,month)[1])
    return datetime(year,month,day,tzinfo=KST).astimezone(UTC).replace(tzinfo=None)


def purge_old_activity(db,now=None,*,commit=True,batch_size=1000):
    cutoff=retention_cutoff(now)
    result={}
    # FK의 ON DELETE CASCADE로 번호표 출처도 함께 제거된다.
    for name in ACTIVITY_TABLES:
        if name=='console_ticket_attribution' or name not in Base.metadata.tables: continue
        table=Base.metadata.tables[name];total=0
        while True:
            ids=list(db.scalars(select(table.c.id).where(table.c.created_at<cutoff)
                                .order_by(table.c.created_at,table.c.id).limit(batch_size)))
            if not ids: break
            if name=='tickets':
                attrs=Base.metadata.tables.get('console_ticket_attribution')
                if attrs is not None: db.execute(delete(attrs).where(attrs.c.ticket_id.in_(ids)))
            db.execute(delete(table).where(table.c.id.in_(ids)))
            total+=len(ids)
            if commit: db.commit()
        result[name]=total
    if commit: db.commit()
    return {'cutoff_utc':cutoff.isoformat()+'Z','deleted':result}


async def retention_loop(app):
    while True:
        try:
            def run():
                with app.state.SessionLocal() as db: return purge_old_activity(db)
            result=await asyncio.to_thread(run)
            if any(result['deleted'].values()): logging.getLogger('uvicorn.error').info('6-month activity cleanup: %s',result)
        except Exception:
            logging.getLogger('uvicorn.error').exception('Activity cleanup failed; retrying in one hour')
            await asyncio.sleep(3600);continue
        now=datetime.now(KST)
        tomorrow=datetime.combine(now.date()+timedelta(days=1),time(0,5),KST)
        await asyncio.sleep(max(60,(tomorrow-now).total_seconds()))
