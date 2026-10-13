"""한국 시간 기준 리포트. 발급일 기준 집계, 재호출은 중복 집계하지 않는다."""
from __future__ import annotations
import calendar
from datetime import date, datetime, timedelta, UTC
from zoneinfo import ZoneInfo
from sqlalchemy import select, func, case, extract, text
from sqlalchemy.orm import Session
from .models import Ticket, Store
from .retention import retention_cutoff
from .console_models import StoreProfile, TrafficEvent, TicketAttribution

KST = ZoneInfo('Asia/Seoul')


def period_bounds(period: str, value: str):
    try:
        if period == 'day':
            start = date.fromisoformat(value)
            end = start + timedelta(days=1)
            labels = [f'{h:02d}' for h in range(24)]
        elif period == 'month':
            start = date.fromisoformat(value + '-01')
            end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
            labels = [f'{d:02d}' for d in range(1, calendar.monthrange(start.year, start.month)[1]+1)]
        elif period == 'year':
            start = date(int(value), 1, 1)
            end = date(start.year+1, 1, 1)
            labels = [f'{m:02d}' for m in range(1, 13)]
        else:
            raise ValueError()
        if not 2000 <= start.year <= 2100:
            raise ValueError()
    except (ValueError, OverflowError):
        raise ValueError('조회 기간을 올바르게 선택하세요 (2000~2100년)')
    utc = lambda d: datetime.combine(d, datetime.min.time(), KST).astimezone(UTC).replace(tzinfo=None)
    return utc(start), utc(end), labels


def _bucket(db, column, kind):
    if db.bind.dialect.name == 'sqlite':
        return func.strftime({'hour':'%H','day':'%d','month':'%m'}[kind], column, '+9 hours')
    return func.to_char(column + text("INTERVAL '9 hours'"), {'hour':'HH24','day':'DD','month':'MM'}[kind])


def build_report(db: Session, store_ids: list[int], period: str, value: str) -> dict:
    start, end, labels = period_bounds(period, value)
    now = datetime.now(KST)
    cutoff = retention_cutoff(now)
    tomorrow = datetime.combine(now.date()+timedelta(days=1),datetime.min.time(),KST).astimezone(UTC).replace(tzinfo=None)
    if end <= cutoff or start >= tomorrow:
        raise ValueError('최근 6개월 이내의 기간만 조회할 수 있습니다')
    start, end = max(start, cutoff), min(end, tomorrow)
    filters = [Ticket.store_id.in_(store_ids), Ticket.created_at >= start, Ticket.created_at < end]
    if db.bind.dialect.name == 'sqlite':
        seconds = (func.julianday(Ticket.called_at)-func.julianday(Ticket.created_at))*86400
    else:
        seconds = extract('epoch', Ticket.called_at-Ticket.created_at)
    valid_wait = case((Ticket.called_at >= Ticket.created_at, seconds), else_=None)
    columns = [func.count(Ticket.id), func.count(valid_wait), func.avg(valid_wait),
               func.sum(case((Ticket.service_type=='simple_service', 1), else_=0)),
               func.sum(case((Ticket.service_type=='purchase_consult', 1), else_=0))]
    summary = db.execute(select(*columns).where(*filters)).one()
    def metrics(row):
        return {'issued':int(row[0] or 0), 'called':int(row[1] or 0),
                'average_wait_seconds':round(float(row[2]), 1) if row[2] is not None else None,
                'simple':int(row[3] or 0), 'purchase':int(row[4] or 0)}
    bucket = _bucket(db, Ticket.created_at, {'day':'hour','month':'day','year':'month'}[period])
    trend = {r[0]: metrics(r[1:]) for r in db.execute(select(bucket, *columns).where(*filters).group_by(bucket))}
    hour = _bucket(db, Ticket.created_at, 'hour')
    hourly = dict(db.execute(select(hour, func.count(Ticket.id)).where(*filters).group_by(hour)).all())
    per_store = {r[0]:metrics(r[1:]) for r in db.execute(select(Ticket.store_id, *columns).where(*filters).group_by(Ticket.store_id))}
    stores = db.execute(select(Store, StoreProfile.sales_team).outerjoin(StoreProfile, Store.id==StoreProfile.store_id)
                        .where(Store.id.in_(store_ids)).order_by(Store.name)).all()
    traffic_filters = [TrafficEvent.store_id.in_(store_ids), TrafficEvent.created_at>=start, TrafficEvent.created_at<end]
    traffic = [{'source':s, 'views':n} for s,n in db.execute(select(TrafficEvent.source,func.count())
               .where(*traffic_filters).group_by(TrafficEvent.source).order_by(func.count().desc()))]
    source = func.coalesce(TicketAttribution.source, 'legacy')
    ticket_sources = [{'source':s,'issued':n} for s,n in db.execute(select(source,func.count(Ticket.id))
                     .outerjoin(TicketAttribution, Ticket.id==TicketAttribution.ticket_id).where(*filters)
                     .group_by(source).order_by(func.count(Ticket.id).desc()))]
    earliest = db.scalar(select(func.min(Ticket.created_at)).where(Ticket.store_id.in_(store_ids)))
    return {'period':period, 'value':value, 'timezone':'Asia/Seoul', 'summary':metrics(summary),
            'traffic_views':sum(v['views'] for v in traffic), 'traffic_sources':traffic, 'ticket_sources':ticket_sources,
            'first_ticket_at':earliest.isoformat()+'Z' if earliest else None,
            'trend':[{'label':label,**trend.get(label,metrics([0,0,None,0,0]))} for label in labels],
            'hourly':[{'hour':h,'issued':int(hourly.get(f'{h:02d}',0))} for h in range(24)],
            'stores':[{'id':s.id,'name':s.name,'code':s.code,'sales_team':team or '미지정',
                      **per_store.get(s.id,metrics([0,0,None,0,0]))} for s,team in stores]}
