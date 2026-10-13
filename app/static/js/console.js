/* Galaxy Consultant 운영 콘솔: 지점 권한은 모든 API에서 서버가 재검증한다. */
(() => {
  'use strict';
  const root = document.getElementById('consoleApp'), modalRoot = document.getElementById('modalRoot');
  const state = {user:null, stores:[], today:'', mode:'branch', page:'categories', scope:'all', storeId:null, team:'', period:'day', value:'', report:null, storeSort:'name',
    minDate:'', calendarMonth:'', calendarYear:0, modal:null, manageStores:[], access:null, accountId:null, selected:new Set(), modalSearch:'', pickerSearch:'', uploaded:null, busy:false, request:0};
  const esc = value => String(value ?? '').replace(/[&<>"']/g, x => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
  const num = value => Number(value || 0).toLocaleString('ko-KR');
  const wait = seconds => {if(seconds==null)return '—';const total=Math.round(seconds);return total<60?`${total}초`:`${Math.floor(total/60)}분 ${total%60}초`;};
  const icons = {grid:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',store:'<path d="M3 10l2-7h14l2 7M4 10v11h16V10M2 10h20M9 21v-7h6v7"/>',team:'<circle cx="9" cy="7" r="3"/><path d="M3 21v-3a6 6 0 0112 0v3M16 4a3 3 0 010 6M17 14a5 5 0 014 5v2"/>',arrow:'<path d="M5 12h14m-5-5l5 5-5 5"/>',search:'<circle cx="10" cy="10" r="6"/><path d="M15 15l5 5"/>',refresh:'<path d="M20 7a9 9 0 10.5 9M20 2v6h-6"/>',clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v6l4 2"/>',ticket:'<path d="M3 5h18v5a2 2 0 000 4v5H3v-5a2 2 0 000-4zM9 5v14"/>',check:'<path d="M4 12l5 5L20 6"/>',video:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M10 8l6 4-6 4z"/>',download:'<path d="M12 3v12m-5-5l5 5 5-5M4 16v5h16v-5"/>',back:'<path d="M19 12H5m5-5l-5 5 5 5"/>',lock:'<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 018 0v3"/>',chart:'<path d="M4 3v18h18M8 16v-5m5 5V6m5 10v-7"/>'};
  const icon = name => `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name]||icons.grid}</svg>`;
  const btn = (action,label,cls='',extra='') => `<button type="button" class="btn ${cls}" data-action="${action}" ${extra}>${label}</button>`;
  function toast(message) { const t=document.getElementById('toast'); t.textContent=message;t.classList.add('show');clearTimeout(toast.timer);toast.timer=setTimeout(()=>t.classList.remove('show'),4200); }
  async function api(path,options={}) {
    const headers = {...options.headers}; if(state.user) headers['X-Console-CSRF']=state.user.csrf;
    if(options.json!==undefined){headers['Content-Type']='application/json';options.body=JSON.stringify(options.json);delete options.json;}
    const response=await fetch(path,{credentials:'same-origin',...options,headers});
    if(!response.ok){const data=await response.json().catch(()=>null);const error=new Error(typeof data?.detail==='string'?data.detail:'요청을 처리하지 못했습니다');error.status=response.status;throw error;}
    return response;
  }
  async function json(path,options) {return (await api(path,options)).json();}
  function header(logged=true) {
    return `<header class="topbar"><a class="brand" href="/console"><span class="brand-mark"><i></i><i></i></span><span>Galaxy Consultant<small>STORE OPERATIONS</small></span></a>
      ${logged?'<div class="top-search"><span>매장의 흐름을 한눈에</span> · 운영 관리 콘솔</div>':''}
      <div class="top-actions">${logged?`${state.user.role==='root'?btn('openManage',icon('store')+'<span>매장관리</span>','icon-only'):''}<div class="user-chip"><span class="avatar">${esc(state.user.name[0])}</span><span><strong>${esc(state.user.name)}</strong><small>${state.user.role==='branch'?'관리자':'전체 관리자'}</small></span></div>${btn('logout','로그아웃','flat')}`:`<a href="/" class="btn">키오스크로</a>`}</div></header>`;
  }
  function loginChoice(){
    state.user=null;
    root.innerHTML=header(false)+`<main class="main login-choice"><div class="page-heading"><div><div class="eyebrow">GALAXY CONSULTANT</div><h1>로그인 선택</h1></div></div><div class="login-choice-grid">${[
      ['loginBranch','store','관리자 로그인'],['loginIntegrated','team','전체 관리자']
    ].map(([a,i,t])=>`<button class="card scope-card" data-action="${a}"><div class="scope-icon">${icon(i)}</div><h2>${t}</h2><div class="arrow">${icon('arrow')}</div></button>`).join('')}</div><p id="loginError" class="login-error" role="alert"></p></main>`;
  }
  function login(error='') {
    const integrated=state.mode==='integrated',code=new URLSearchParams(location.search).get('code')||'';
    root.innerHTML=header(false)+`<main class="login-layout"><section class="login-intro"><div class="eyebrow">STORE OPERATIONS CONSOLE</div><h1>매장 운영의 흐름을<br>한눈에 확인하세요.</h1><div class="login-visual" aria-hidden="true"><div class="visual-top"><i></i><i></i><i></i></div><div class="visual-bars">${[28,45,39,72,62,88,70,94,83].map(n=>`<span style="height:${n}%"></span>`).join('')}</div></div></section>
      <section class="card login-card"><div class="row spread"><h2 class="login-title">${integrated?'전체 관리자':'관리자 로그인'}</h2>${btn('loginChoice','뒤로','small')}</div>
      <form id="loginForm" style="margin-top:24px">${integrated?`<div class="form-field"><label class="field-label" for="loginName">이름</label><input class="field full" id="loginName" name="name" placeholder="이름을 입력해 주세요" maxlength="80" autocomplete="off" required></div>`:`<div class="form-field"><label class="field-label" for="loginId">점코드</label><input class="field full" id="loginId" name="username" value="${esc(code)}" placeholder="점코드를 입력해 주세요" maxlength="40" autocomplete="username" required></div>`}
      <div class="form-field"><label class="field-label" for="loginPassword">${integrated?'인증번호':'인증키'}</label><input class="field full" type="password" id="loginPassword" name="password" maxlength="512" autocomplete="current-password" placeholder="${integrated?'인증번호':'인증키'}를 입력해 주세요" required></div><p class="login-error" id="loginError" role="alert">${esc(error)}</p><button class="btn primary large" type="submit">로그인 ${icon('arrow')}</button></form></section></main>`;
  }
  function scopeLabel(){ if(state.scope==='store')return state.stores.find(s=>s.id===state.storeId)?.name||'지점'; if(state.scope==='team')return state.team;return state.user.role==='root'?'전사':'담당 매장 전체'; }
  function shell(content){root.innerHTML=header()+`<nav class="navline"><button class="active" data-action="home">${icon('grid')} 운영 대시보드</button><span class="crumb">${state.page==='report'?esc(scopeLabel()):state.page==='teams'?'영업팀 선택':state.page==='stores'?'지점 선택':'조회 범위 선택'}</span></nav><main class="main">${content}</main>`;}
  function categories(){
    state.page='categories';
    shell(`<div class="page-heading"><div><div class="eyebrow">OVERVIEW</div><h1>어떤 현황을 확인할까요?</h1><p class="desc">조회할 범위를 선택해 매장 운영 리포트를 확인하세요.</p></div><span class="badge gray">조회 가능 매장 ${num(state.stores.length)}개</span></div><div class="scope-grid">${[
      ['all','grid',state.user.role==='root'?'전사':'담당 매장 전체','조회 가능한 모든 매장의 운영 현황을 확인합니다.'],['teams','team','영업팀별','영업팀을 선택해 소속 매장의 흐름을 확인합니다.'],['stores','store','지점별','지점명이나 점코드로 매장을 검색합니다.']].map(([a,i,t,d])=>`<button class="card scope-card" data-action="chooseScope" data-value="${a}"><div class="scope-icon">${icon(i)}</div><h2>${t}</h2><p class="desc">${d}</p><div class="arrow">현황 보기 ${icon('arrow')}</div></button>`).join('')}</div><div class="notice section-gap">${state.user.role==='root'?'전사 · 영업팀 · 개별 지점의 리포트를 확인할 수 있습니다.':'매장관리에서 지정한 지점만 조회할 수 있습니다. 필요한 지점이 보이지 않으면 담당자에게 문의하세요.'}</div>`);
  }
  function picker(kind){
    state.page=kind;state.pickerSearch='';
    shell(`<div class="page-heading"><div><div class="eyebrow">SELECT ${kind==='teams'?'TEAM':'STORE'}</div><h1>${kind==='teams'?'영업팀 선택':'지점 선택'}</h1><p class="desc">조회할 ${kind==='teams'?'영업팀':'지점'}을 선택하세요.</p></div>${btn('home',icon('back')+'조회 범위')}</div><div class="searchbox" style="margin-bottom:22px">${icon('search')}<input id="pickerSearch" class="field" placeholder="${kind==='teams'?'영업팀명':'지점명 또는 점코드'} 검색" aria-label="검색"></div><div class="picker" id="pickerList"></div>`);renderPickerList();
  }
  function renderPickerList(){
    const q=state.pickerSearch.toLocaleLowerCase(),target=document.getElementById('pickerList');if(!target)return;
    if(state.page==='teams'){
      const teams=new Map();state.stores.forEach(s=>{const t=s.sales_team||'미지정';teams.set(t,(teams.get(t)||0)+1)});
      target.innerHTML=[...teams].sort((a,b)=>a[0].localeCompare(b[0],'ko')).filter(([t])=>t.toLowerCase().includes(q)).map(([t,n])=>`<button class="card pick-store" data-action="teamReport" data-value="${esc(t)}"><div><strong>${esc(t)}</strong><p>조회 가능 매장 ${n}개</p></div>${icon('arrow')}</button>`).join('')||empty('조회 가능한 영업팀이 없습니다');
    }else target.innerHTML=state.stores.filter(s=>`${s.name} ${s.code||''}`.toLowerCase().includes(q)).map(s=>`<button class="card pick-store" data-action="storeReport" data-id="${s.id}"><div><strong>${esc(s.name)}</strong><p>${esc(s.code||'점코드 미등록')} · ${esc(s.sales_team||'미지정')}</p></div>${icon('arrow')}</button>`).join('')||empty('검색 결과가 없습니다');
  }
  function empty(message){return `<div class="empty"><span class="empty-icon">${icon('chart')}</span>${esc(message)}</div>`;}
  function chart(values,labels,kind='bar'){
    const w=window.innerWidth<=680?320:660,h=230,l=40,r=14,t=15,b=32,iw=w-l-r,ih=h-t-b,max=Math.max(4,...values),step=iw/Math.max(1,values.length);
    let svg=`<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(values.map((n,i)=>labels[i]+': '+n+'건').join(', '))}">`;
    for(let i=0;i<=4;i++){const y=t+ih-i*ih/4;svg+=`<line class="grid" x1="${l}" y1="${y}" x2="${w-r}" y2="${y}"/><text x="${l-9}" y="${y+4}" text-anchor="end">${Math.round(max*i/4)}</text>`;}
    const points=values.map((v,i)=>[l+step*(i+.5),t+ih-v/max*ih]);
    if(kind==='line'&&points.length){const p=points.map(a=>a.join(',')).join(' ');svg+=`<polygon points="${l+step/2},${t+ih} ${p} ${points.at(-1)[0]},${t+ih}" fill="#edf4ff"/><polyline points="${p}" fill="none" stroke="#397fea" stroke-width="2.4"/>`;}
    values.forEach((v,i)=>{const[x,y]=points[i],width=Math.max(3,Math.min(24,step*.6));if(kind==='bar')svg+=`<rect x="${x-width/2}" y="${y}" width="${width}" height="${Math.max(0,t+ih-y)}" rx="2" fill="${v===Math.max(...values)&&v>0?'#397fea':'#9ec1f6'}"><title>${esc(labels[i])}: ${num(v)}건</title></rect>`;else svg+=`<circle cx="${x}" cy="${y}" r="${v?3:1.5}" fill="#397fea"><title>${esc(labels[i])}: ${num(v)}건</title></circle>`;if(i===0||i===values.length-1||i%Math.ceil(values.length/9)===0)svg+=`<text x="${x}" y="${h-9}" text-anchor="middle">${esc(labels[i])}</text>`;});
    return svg+'</svg>';
  }
  function renderReport(){
    const d=state.report,summary=d?.summary||{};
    const reportStores=[...(d?.stores||[])].sort((a,b)=>(state.storeSort==='issued'?Number(b.issued||0)-Number(a.issued||0):0)||String(a.name||'').localeCompare(String(b.name||''),'ko')||String(a.code||'').localeCompare(String(b.code||''),'ko')||Number(a.id)-Number(b.id));
    shell(`<div class="page-heading"><div><div class="eyebrow">OPERATIONS REPORT</div><h1>${esc(scopeLabel())} 리포트</h1><p class="desc">발급 현황과 고객 대기시간을 한눈에 확인하세요.</p></div><div class="row">${state.user.role!=='branch'?btn('home',icon('back')+'<span>범위 변경</span>','icon-only'):''}${btn('refresh',icon('refresh')+'<span>새로고침</span>','icon-only')}</div></div>
      <section class="card filterbar"><div class="tabs" role="tablist" aria-label="리포트 기간">${[['day','일별'],['month','월별'],['year','연도별']].map(([p,n])=>`<button class="tab ${state.period===p?'active':''}" role="tab" aria-selected="${state.period===p}" data-action="period" data-value="${p}">${n}</button>`).join('')}</div><div class="row"><span class="muted">조회 기간</span><button class="btn date-trigger" id="reportDate" data-action="openCalendar" aria-haspopup="dialog">${esc(state.value)} ${icon('grid')}</button></div></section>
      ${!d?'<div class="loading" role="status">리포트를 불러오고 있습니다…</div>':`<section class="stats">${[
        ['번호표 발급',num(summary.issued),'건','ticket','간단서비스 + 구매상담'],['호출 완료',num(summary.called),'건','check','재호출 중복 제외'],['평균 대기시간',wait(summary.average_wait_seconds),'','clock','발급 시각부터 최초 호출까지'],['페이지 조회',num(d.traffic_views),'회','chart','고객 화면 접속 기준']].map(([t,v,u,i,n])=>`<article class="card stat"><div class="stat-head"><span>${t}</span><span class="stat-icon ${i==='check'?'green':''}">${icon(i)}</span></div><div class="stat-value">${v}<small>${u}</small></div><div class="stat-foot">${n}</div></article>`).join('')}</section>
      <section class="dashboard-grid"><article class="card"><div class="card-head"><div><h2>${state.period==='day'?'시간별 발급 흐름':state.period==='month'?'일자별 발급 현황':'월별 발급 현황'}</h2><p class="hint">${esc(state.value)} · 한국 시간 기준</p></div><span class="legend"><i class="dot"></i> 발급 건수</span></div>${chart(d.trend.map(v=>v.issued),d.trend.map(v=>v.label+(state.period==='day'?'시':state.period==='month'?'일':'월')),'line')}</article>
      <article class="card"><div class="card-head"><div><h2>시간대별 방문객 수</h2><p class="hint">번호표 발급 건수 기준 · 기간 내 시간대 합계</p></div><span class="legend"><i class="dot"></i> 발급</span></div>${chart(d.hourly.map(v=>v.issued),d.hourly.map(v=>String(v.hour).padStart(2,'0')))}</article></section>
      <section class="dashboard-grid"><article class="card"><div class="card-head"><div><h2>지점별 운영 현황</h2><p class="hint">현재 조회 범위의 ${d.stores.length}개 지점</p></div><span class="badge gray">${esc(state.value)}</span></div><div class="row wrap" role="group" aria-label="지점 정렬" style="margin-bottom:12px">${[['name','가나다순'],['issued','번호표 발급순']].map(([value,label])=>btn('sortStores',label,'small'+(state.storeSort===value?' primary':''),`data-value="${value}" aria-pressed="${state.storeSort===value}"${value==='issued'?' title="발급 건수가 많은 지점부터 표시"':''}`)).join('')}</div><div class="table-wrap"><table><thead><tr><th>지점명 / 점코드</th><th>영업팀</th><th class="num">발급</th><th class="num">호출</th><th class="num">평균 대기</th></tr></thead><tbody>${reportStores.map(s=>`<tr><td><button data-action="storeReport" data-id="${s.id}" style="text-align:left;padding:0"><strong>${esc(s.name)}</strong><div class="hint">${esc(s.code||'미등록')}</div></button></td><td>${esc(s.sales_team)}</td><td class="num">${num(s.issued)}</td><td class="num">${num(s.called)}</td><td class="num">${wait(s.average_wait_seconds)}</td></tr>`).join('')}</tbody></table>${!d.stores.length?empty('조회 가능한 지점이 없습니다'):''}</div></article>
      <article class="card"><div class="card-head"><div><h2>업무별 발급 · 접속 경로</h2><p class="hint">간단서비스와 구매상담의 이용 현황</p></div>${icon('chart')}</div><div class="row spread" style="padding:8px 0 16px;border-bottom:1px solid var(--line);margin-bottom:15px"><span class="muted">간단서비스 <strong style="color:var(--blue);margin-left:6px">${num(summary.simple)}건</strong></span><span class="muted">구매상담 <strong style="color:var(--green);margin-left:6px">${num(summary.purchase)}건</strong></span></div>${d.traffic_sources.length?d.traffic_sources.slice(0,8).map(s=>`<div class="source-row"><span class="source-label" title="${esc(s.source)}">${esc(s.source==='direct'?'직접 접속':s.source)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.max(2,s.views/d.traffic_views*100)}%"></div></div><span style="text-align:right">${num(s.views)}</span></div>`).join(''):empty('이 기간에 수집된 접속 경로가 없습니다')}</article></section>
      `}`);
  }
  const isoDate=d=>d.toISOString().slice(0,10);
  function shiftMonth(month,delta){const [y,m]=month.split('-').map(Number);return isoDate(new Date(Date.UTC(y,m-1+delta,1))).slice(0,7);}
  function periodAllowed(value){const n=state.period==='day'?10:state.period==='month'?7:4;return value>=state.minDate.slice(0,n)&&value<=state.today.slice(0,n);}
  function renderCalendar(){
    state.modal='calendar';const daily=state.period==='day',monthly=state.period==='month';
    const cell=(value,label)=>`<button type="button" class="calendar-cell ${value===state.value?'selected':''} ${value===state.today.slice(0,value.length)?'today':''}" data-action="chooseDate" data-value="${value}" ${periodAllowed(value)?'':'disabled title="조회할 수 없는 기간"'}>${label}</button>`;
    let cells='',heading='',prev=false,next=false;
    if(daily){
      const [year,month]=state.calendarMonth.split('-').map(Number),first=new Date(Date.UTC(year,month-1,1)),count=new Date(Date.UTC(year,month,0)).getUTCDate();
      heading=`${year}년 ${month}월`;prev=state.calendarMonth>state.minDate.slice(0,7);next=state.calendarMonth<state.today.slice(0,7);
      cells=['일','월','화','수','목','금','토'].map(d=>`<span class="calendar-weekday">${d}</span>`).join('')+'<span></span>'.repeat(first.getUTCDay());
      for(let d=1;d<=count;d++)cells+=cell(`${state.calendarMonth}-${String(d).padStart(2,'0')}`,d);
    }else if(monthly){
      heading=state.calendarYear+'년';prev=state.calendarYear>Number(state.minDate.slice(0,4));next=state.calendarYear<Number(state.today.slice(0,4));
      for(let m=1;m<=12;m++)cells+=cell(`${state.calendarYear}-${String(m).padStart(2,'0')}`,m+'월');
    }else{
      heading='연도 선택';for(let y=Number(state.minDate.slice(0,4));y<=Number(state.today.slice(0,4));y++)cells+=cell(String(y),y+'년');
    }
    modalFrame('기간 선택',`<div class="calendar-nav">${daily||monthly?btn('calendarPrev','‹','',prev?'aria-label="이전"':'disabled aria-label="이전"'):'<span></span>'}<strong>${heading}</strong>${daily||monthly?btn('calendarNext','›','',next?'aria-label="다음"':'disabled aria-label="다음"'):'<span></span>'}</div><div class="calendar-grid ${daily?'days':'months'}">${cells}</div>`,true);
    requestAnimationFrame(()=>modalRoot.querySelector('.calendar-cell.selected:not(:disabled),.calendar-cell:not(:disabled)')?.focus());
  }
  async function loadReport(){
    state.page='report';state.report=null;renderReport();const serial=++state.request;
    const query=new URLSearchParams({scope:state.scope,period:state.period,value:state.value});if(state.storeId)query.set('store_id',state.storeId);if(state.team)query.set('team',state.team);
    try{const data=await json('/api/console/report?'+query);if(serial!==state.request||state.page!=='report')return;state.report=data;renderReport();}catch(e){if(serial===state.request){const target=root.querySelector('.loading');if(target)target.textContent=e.message;}throw e;}
  }
  async function refreshMe(){const data=await json('/api/console/me');Object.assign(state,{user:data.user,stores:data.stores,today:data.today,minDate:data.min_date});if(!state.value)state.value=data.today;}
  async function enter(){await refreshMe();if(state.user.role==='branch'){state.scope='store';state.storeId=state.user.store_id;await loadReport();}else categories();}
  async function manageList(){state.manageStores=(await json('/api/admin/stores')).stores;}
  function modalFrame(title,body,narrow=false,back=''){
    modalRoot.innerHTML=`<div class="modal-backdrop"><section class="modal ${narrow?'narrow':''}" role="dialog" aria-modal="true" aria-labelledby="modalTitle"><div class="modal-head"><h2 id="modalTitle">${esc(title)}</h2><div class="row">${back?btn(back,'뒤로','small'):''}<button type="button" class="close" data-action="closeModal" aria-label="팝업 닫기">×</button></div></div>${body}<p id="modalError" class="inline-error" role="alert"></p></section></div>`;
    document.body.style.overflow='hidden';requestAnimationFrame(()=>modalRoot.querySelector('input:not([type=file]),button')?.focus());
  }
  function closeModal(){state.modal=null;modalRoot.innerHTML='';document.body.style.overflow='';state.uploaded=null;}
  function manageAuth(){state.modal='manageAuth';modalFrame('매장 관리',`<p class="desc">인증키를 입력해 주세요</p><form id="manageAuthForm" class="row" style="margin-top:18px"><input id="manageKey" class="field full" type="password" placeholder="인증키를 입력해 주세요" maxlength="512" autocomplete="off" required><button class="btn primary">확인</button></form>`,true);}
  function manageMenu(){
    state.modal='manageMenu';
    modalFrame('매장관리',`<p class="desc">관리할 항목을 선택하세요.</p><div class="management-menu">${[
      ['openCodes','store','매장 점코드 관리','매장 등록 · 점코드 수정 · 백업과 복원'],
      ['openAccess','team','매장 관리자 관리','관리자 이름 · 조회 매장 · 영업팀'],
      ['openVideo','video','GC 동영상 관리','전 매장 공통 영상 선택 · 저장']
    ].map(([a,i,t,d])=>`<button class="management-choice" data-action="${a}"><span class="scope-icon">${icon(i)}</span><span><strong>${t}</strong><small>${d}</small></span>${icon('arrow')}</button>`).join('')}</div>`,true);
  }
  function renderManage(){
    state.modal='manage';
    const q=state.modalSearch.toLowerCase(),stores=state.manageStores.filter(s=>`${s.name} ${s.code||''}`.toLowerCase().includes(q));
    modalFrame('매장 점코드 관리',`<div class="notice">매장 이름과 점코드를 관리합니다. 전체 관리자 이름과 조회 권한은 ‘매장 관리자 관리’에서 설정하세요.</div><form id="createStoreForm" class="manage-create"><input class="field" id="newStoreName" placeholder="새 매장명" maxlength="120" aria-label="새 매장명" required><input class="field" id="newStoreCode" placeholder="점코드" maxlength="40" aria-label="점코드" required><button class="btn primary">매장 생성</button></form><form id="manageSearchForm" class="row" style="margin-bottom:14px"><input id="manageSearch" class="field full" value="${esc(state.modalSearch)}" placeholder="매장명 또는 점코드 검색"><button class="btn">검색</button></form><div class="manage-list">${stores.map(s=>`<div class="manage-store"><div><strong>${esc(s.name)}</strong><small>점코드 ${esc(s.code||'미등록')} · ${s.is_active?'사용중':'비활성'}</small></div><div class="row">${btn('editStore','수정','small',`data-id="${s.id}"`)}${btn('toggleStore',s.is_active?'비활성':'활성화','small',`data-id="${s.id}"`)}${btn('deleteStore','삭제','danger small',`data-id="${s.id}"`)}</div></div>`).join('')||empty('매장이 없습니다')}</div><div class="manage-footer">${btn('storeBackup',icon('download')+'전체 데이터 ZIP')}<label class="btn" for="storeRestoreFile">ZIP 복원</label><input type="file" id="storeRestoreFile" accept=".zip" hidden></div><p class="hint">이전 매장 ZIP은 매장 정보만, 새 전체 ZIP은 모든 운영 데이터를 복원합니다. 전체 백업·복원은 추가 인증 후 사용할 수 있습니다.</p>`,false,'backManage');
  }
  async function openAccess(){
    try{state.access=await json('/api/console/access');state.accountId=null;state.selected=new Set();renderAccess();}
    catch(e){if(e.status!==403)throw e;state.modal='gcsAuth';modalFrame('매장 관리자 관리 인증',`<p class="desc">인증키를 입력해 주세요</p><form id="gcsAuthForm" style="margin-top:18px"><input class="field full" id="gcsKey" type="password" placeholder="인증키를 입력해 주세요" maxlength="512" autocomplete="off" required><button class="btn primary large" style="margin-top:15px">인증하고 열기</button></form>`,true,'backManage');}
  }
  function checkList(search=''){
    const q=search.toLowerCase();
    return state.access.stores.filter(s=>`${s.name} ${s.code||''} ${s.sales_team||''}`.toLowerCase().includes(q)).map(s=>`<label class="store-check"><input type="checkbox" data-store-check="${s.id}" ${state.selected.has(s.id)?'checked':''}><span>${esc(s.name)}<small>${esc(s.code||'점코드 미등록')} · ${esc(s.sales_team||'영업팀 미지정')}</small></span></label>`).join('')||'<p class="hint">검색 결과가 없습니다.</p>';
  }
  function renderAccess(){
    state.modal='access';const account=state.access.accounts.find(a=>a.id===state.accountId);
    const integrated=state.access.accounts.filter(a=>a.role==='integrated');
    modalFrame('매장 관리자 관리',`<div class="access-grid"><aside><div class="account-item"><strong>${esc(state.access.root.name)}</strong><small>고정 전체 관리자 · 전체 권한 · 삭제 불가</small></div>${btn('newAccount','+ 이름 등록','primary', 'style="width:100%;margin:5px 0 14px"')}<div class="account-list">${integrated.map(a=>`<button class="account-item ${a.id===state.accountId?'selected':''}" data-action="selectAccount" data-id="${a.id}"><strong>${esc(a.name)}</strong><small>지정 매장 ${a.store_ids.length}개 · ${a.is_active?'사용중':'비활성'}</small></button>`).join('')||'<p class="hint">추가로 등록된 이름이 없습니다.</p>'}</div></aside><div><form id="accountForm"><label class="field-label" for="accountName">전체 관리자 이름</label><div class="row"><input id="accountName" class="field full" value="${esc(account?.name||'')}" placeholder="등록할 이름" maxlength="80" required>${account?btn('deleteAccount','이름 삭제','danger'):''}</div><div class="row spread" style="margin-top:20px"><label class="field-label" for="accessStoreSearch">조회 가능한 매장</label><span class="muted" id="selectedCount">${state.selected.size}개 선택</span></div><input class="field full" id="accessStoreSearch" placeholder="지점명 · 점코드 · 영업팀 검색"><div class="selection-tools"><div class="row">${btn('selectAll','전체 체크','small')}${btn('selectNone','선택 해제','small')}</div><span class="hint">검색과 관계없이 전체 매장 기준</span></div><div class="store-checks" id="storeChecks">${checkList()}</div><div class="row spread" style="margin-top:15px"><label class="check-row"><input type="checkbox" id="accountActive" ${account?.is_active!==false?'checked':''}> 로그인 허용</label><button class="btn primary" type="submit">이름·조회 권한 저장</button></div></form>
      <div class="modal-section"><h3>선택한 매장 설정</h3><p class="hint">위에서 체크한 매장에 적용합니다.</p><form id="teamForm" class="row" style="margin-top:12px"><input id="teamName" class="field full" placeholder="예: 서울영업팀 / 경인영업팀" maxlength="80" aria-label="영업팀명"><button class="btn">영업팀 지정</button></form></div></div></div>
      <div class="modal-section"><h3>전체 운영 데이터 백업·복원</h3><p class="hint">매장, 번호표·호출 기록, 접속 정보, 관리자 권한, 업로드한 영상을 함께 저장합니다. 이전 매장 ZIP도 복원할 수 있습니다.</p><div class="row wrap" style="margin-top:13px">${btn('fullBackup',icon('download')+'전체 데이터 ZIP 백업','primary')}<label class="btn danger" for="fullRestoreFile">전체 데이터 ZIP 복원</label><input type="file" id="fullRestoreFile" accept=".zip" hidden></div><p class="hint">복원하면 전체 ZIP은 현재 데이터를 백업 시점으로 교체하고 로그아웃합니다. 6개월보다 오래된 누적 기록은 복원 후에도 삭제됩니다. 영상 폴더의 파일도 전체 ZIP에 포함됩니다.</p></div>`,false,'backManage');
  }
  async function openVideo(){
    state.modal='video';const data=await json('/api/console/videos');
    modalFrame('GC 동영상 관리',`<div class="notice">영상 전용 화면 <a href="/video" target="_blank" rel="noopener" style="color:var(--blue);font-weight:700">/video ↗</a><br>파일 이름순으로 무한 반복합니다. 영상이 없으면 아무것도 표시하지 않습니다.</div><div class="manage-list">${data.videos.map(v=>`<div class="manage-store"><strong>${esc(v.name)}</strong><small>${(v.size/1024/1024).toFixed(1)}MB</small></div>`).join('')||empty('등록된 영상이 없습니다')}</div>${data.can_edit?`<form id="videoForm" style="margin-top:22px"><label class="field-label" for="videoFiles">새 영상 목록으로 교체</label><input class="field full" id="videoFiles" type="file" accept=".mp4,.webm,.m4v" multiple required><p class="hint">MP4 / WebM / M4V · 전체 합계 100MB 이하<br>USB에서도 선택할 수 있으며, 저장 완료 후 USB를 빼도 재생됩니다.<br>여러 영상을 선택할 수 있습니다. 01_, 02_로 파일명을 시작하면 순서를 정하기 편합니다.</p><p class="hint" id="videoUploadStatus"></p><div class="row spread" style="margin-top:20px">${btn('removeVideo','영상 전체 삭제','danger')}<button class="btn primary" type="submit">선택한 영상 저장</button></div></form>`:'<p class="hint">공통 영상 교체는 전체 권한 전체 관리자가 할 수 있습니다.</p>'}`,false,'backManage');
  }
  async function download(path,fallback){const response=await api(path);const blob=await response.blob();saveBlob(blob,response.headers.get('content-disposition')?.match(/filename="([^"]+)"/)?.[1]||fallback);}
  function saveBlob(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  async function act(action,target){
    if(action==='sortStores'){
      if(!['name','issued'].includes(target.dataset.value)||state.page!=='report'||!state.report)return;
      state.storeSort=target.dataset.value;renderReport();
      root.querySelector(`[data-action="sortStores"][data-value="${state.storeSort}"]`)?.focus({preventScroll:true});return;
    }
    if(action==='loginChoice'){loginChoice();return;}
    if(action==='loginBranch'||action==='loginIntegrated'){state.mode=action==='loginBranch'?'branch':'integrated';login();return;}
    if(action==='closeModal'){closeModal();return;}
    if(action==='logout'){await json('/api/console/logout',{method:'POST'});state.user=null;closeModal();loginChoice();return;}
    if(action==='home'){++state.request;state.user.role==='branch'?await loadReport():categories();return;}
    if(action==='chooseScope'){if(target.dataset.value==='all'){state.scope='all';state.storeId=null;state.team='';await loadReport();}else picker(target.dataset.value);return;}
    if(action==='teamReport'){state.scope='team';state.team=target.dataset.value;state.storeId=null;await loadReport();return;}
    if(action==='storeReport'){state.scope='store';state.storeId=Number(target.dataset.id);state.team='';await loadReport();return;}
    if(action==='refresh'){await refreshMe();await loadReport();return;}
    if(action==='period'){state.period=target.dataset.value;const previous=state.report?.value||state.today;const base=(previous.length===4?previous+'-01-01':previous.length===7?previous+'-01':previous);state.value=base.slice(0,state.period==='day'?10:state.period==='month'?7:4);if(!periodAllowed(state.value))state.value=state.today.slice(0,state.period==='day'?10:state.period==='month'?7:4);await loadReport();return;}
    if(action==='openCalendar'){await refreshMe();state.calendarMonth=state.value.length>=7?state.value.slice(0,7):state.today.slice(0,7);state.calendarYear=Number(state.value.slice(0,4));renderCalendar();return;}
    if(action==='calendarPrev'||action==='calendarNext'){const delta=action==='calendarPrev'?-1:1;if(state.period==='day')state.calendarMonth=shiftMonth(state.calendarMonth,delta);else state.calendarYear+=delta;renderCalendar();return;}
    if(action==='chooseDate'){if(!periodAllowed(target.dataset.value)){toast('최근 6개월 이내의 기간만 조회할 수 있습니다');return;}state.value=target.dataset.value;closeModal();await loadReport();return;}
    if(action==='openManage'){if(state.user.role!=='root')return;state.modalSearch='';manageAuth();return;}
    if(action==='backManage'){manageMenu();return;}
    if(action==='openCodes'){await manageList();renderManage();return;}
    if(action==='openAccess'){await openAccess();return;}
    if(action==='editStore'){const s=state.manageStores.find(s=>s.id===Number(target.dataset.id));const name=prompt('수정할 매장명',s.name);if(name===null)return;const code=prompt('수정할 점코드',s.code||'');if(code===null)return;await json('/api/admin/stores/'+s.id,{method:'PUT',json:{name:name.trim(),code:code.trim()}});await manageList();renderManage();await refreshMe();return;}
    if(action==='toggleStore'){const s=state.manageStores.find(s=>s.id===Number(target.dataset.id));if(!confirm(`${s.name}을 ${s.is_active?'비활성화':'활성화'}할까요?`))return;await json('/api/admin/stores/'+s.id,{method:'PUT',json:{is_active:!s.is_active}});await manageList();renderManage();return;}
    if(action==='deleteStore'){const s=state.manageStores.find(s=>s.id===Number(target.dataset.id));if(!confirm(`${s.name}을 삭제할까요?\n이 매장의 번호표·호출·트래픽 기록도 삭제됩니다. 먼저 전체 ZIP을 백업하세요.`))return;await json('/api/admin/stores/'+s.id,{method:'DELETE'});await manageList();renderManage();await refreshMe();return;}
    if(action==='storeBackup'){try{await download('/api/admin/backup','galaxy_consultant_full.zip');toast('전체 데이터 ZIP을 다운로드했습니다');}catch(e){if(e.status!==403)throw e;await openAccess();toast('인증 후 전체 데이터 ZIP 백업을 누르세요');}return;}
    if(action==='fullBackup'){toast('전체 데이터를 백업하고 있습니다…');await download('/api/console/backup','galaxy_consultant_full.zip');toast('전체 데이터 ZIP을 다운로드했습니다');return;}
    if(action==='newAccount'){state.accountId=null;state.selected=new Set();renderAccess();return;}
    if(action==='selectAccount'){state.accountId=Number(target.dataset.id);const a=state.access.accounts.find(a=>a.id===state.accountId);state.selected=new Set(a.store_ids);renderAccess();return;}
    if(action==='deleteAccount'){const a=state.access.accounts.find(a=>a.id===state.accountId);if(!a||!confirm(`${a.name}을 삭제할까요?\n기존 로그인도 더 이상 사용할 수 없습니다.`))return;await json('/api/console/accounts/'+a.id,{method:'DELETE'});state.access=await json('/api/console/access');state.accountId=null;state.selected=new Set();renderAccess();return;}
    if(action==='selectAll'||action==='selectNone'){state.selected=action==='selectAll'?new Set(state.access.stores.map(s=>s.id)):new Set();document.getElementById('storeChecks').innerHTML=checkList(document.getElementById('accessStoreSearch')?.value||'');document.getElementById('selectedCount').textContent=state.selected.size+'개 선택';return;}
    if(action==='openVideo'){await openVideo();return;}
    if(action==='removeVideo'){if(!confirm('전 매장 공통 영상을 모두 삭제할까요? /video는 빈 화면이 됩니다.'))return;await json('/api/console/video',{method:'DELETE'});await openVideo();toast('영상 목록을 비웠습니다');return;}
  }
  async function submit(form){
    if(form.id==='loginForm'){
      const integrated=state.mode==='integrated';await json('/api/console/login',{method:'POST',json:{mode:state.mode,...(integrated?{name:document.getElementById('loginName').value.trim()}:{username:document.getElementById('loginId').value.trim()}),password:document.getElementById('loginPassword').value}});await enter();return;
    }
    if(form.id==='manageAuthForm'){await json('/api/admin/manage/login',{method:'POST',json:{key:document.getElementById('manageKey').value.trim()}});manageMenu();return;}
    if(form.id==='gcsAuthForm'){await json('/api/console/access/unlock',{method:'POST',json:{key:document.getElementById('gcsKey').value.trim()}});await openAccess();return;}
    if(form.id==='manageSearchForm'){state.modalSearch=document.getElementById('manageSearch').value.trim();renderManage();return;}
    if(form.id==='createStoreForm'){await json('/api/admin/stores',{method:'POST',json:{name:document.getElementById('newStoreName').value.trim(),code:document.getElementById('newStoreCode').value.trim()}});await manageList();renderManage();await refreshMe();return;}
    if(form.id==='accountForm'){
      const body={name:document.getElementById('accountName').value.trim(),store_ids:[...state.selected],is_active:document.getElementById('accountActive').checked};
      const saved=await json('/api/console/accounts'+(state.accountId?'/'+state.accountId:''),{method:state.accountId?'PUT':'POST',json:body});state.accountId=saved.id;state.access=await json('/api/console/access');renderAccess();toast('이름과 조회 권한을 저장했습니다');return;
    }
    if(form.id==='teamForm'){if(!state.selected.size)throw new Error('영업팀을 지정할 매장을 선택하세요');const name=document.getElementById('teamName').value.trim();if(!name&&!confirm('선택한 매장의 영업팀 지정을 해제할까요?'))return;await json('/api/console/store-teams',{method:'PUT',json:{store_ids:[...state.selected],sales_team:name}});const input=document.getElementById('accountName').value;state.access=await json('/api/console/access');await refreshMe();renderAccess();document.getElementById('accountName').value=input;toast('영업팀을 지정했습니다');return;}
    if(form.id==='videoForm'){
      const files=[...document.getElementById('videoFiles').files];if(!files.length)throw new Error('영상 파일을 선택하세요');if(files.reduce((n,f)=>n+f.size,0)>100*1024*1024)throw new Error('영상 합계는 100MB 이하로 올려 주세요');
      if(!confirm('전 매장 공통 영상 목록을 선택한 파일로 교체할까요? 기존 영상은 교체됩니다.'))return;
      document.getElementById('videoUploadStatus').textContent='영상 목록을 교체하고 있습니다…';const body=new FormData();files.forEach(f=>body.append('files',f));
      await json('/api/console/video',{method:'POST',body});await openVideo();toast('공통 영상 목록을 교체했습니다');return;
    }
  }
  async function run(task,element){
    if(state.busy)return;state.busy=true;const button=element?.tagName==='BUTTON'?element:element?.querySelector('button[type=submit],button:not([type])');if(button)button.disabled=true;
    const err=document.getElementById(state.modal?'modalError':'loginError');if(err)err.textContent='';
    try{await task();}catch(e){const target=document.getElementById(state.modal?'modalError':'loginError');if(target)target.textContent=e.message;else toast(e.message);if(e.status===401&&state.user&&!state.modal){state.user=null;login('로그인이 만료되었습니다. 다시 로그인하세요.');}}
    finally{state.busy=false;if(button?.isConnected)button.disabled=false;}
  }
  document.addEventListener('click',event=>{const target=event.target.closest('[data-action]');if(target)run(()=>act(target.dataset.action,target),target);});
  document.addEventListener('submit',event=>{event.preventDefault();run(()=>submit(event.target),event.target);});
  document.addEventListener('input',event=>{if(event.target.id==='pickerSearch'){state.pickerSearch=event.target.value;renderPickerList();}if(event.target.id==='accessStoreSearch')document.getElementById('storeChecks').innerHTML=checkList(document.getElementById('accessStoreSearch')?.value||'');});
  document.addEventListener('change',event=>{
    const t=event.target;if(t.dataset.storeCheck){const id=Number(t.dataset.storeCheck);t.checked?state.selected.add(id):state.selected.delete(id);document.getElementById('selectedCount').textContent=state.selected.size+'개 선택';}
    if(t.id==='storeRestoreFile'||t.id==='fullRestoreFile')run(async()=>{
      const file=t.files[0];if(!file)return;const full=t.id==='fullRestoreFile';if(file.size>200*1024*1024)throw new Error('ZIP 파일 크기가 너무 큽니다');
      if(!confirm(full?'전체 운영 데이터를 이 ZIP의 백업 시점으로 교체할까요?\n현재 데이터를 먼저 백업하세요. 복원 후 다시 로그인해야 합니다.':'ZIP을 복원할까요?\n이전 매장 ZIP은 매장 정보만, 전체 ZIP은 현재 운영 데이터를 백업 시점으로 교체합니다. 먼저 현재 데이터를 백업하세요.')){t.value='';return;}
      const body=new FormData();body.append('file',file);toast('ZIP 데이터를 복원하고 있습니다…');let restored;try{restored=await json(full?'/api/console/restore':'/api/admin/restore',{method:'POST',body,headers:{'X-Restore-Confirm':'REPLACE'}});}catch(e){if(e.status===403&&!full){await openAccess();toast('인증 후 전체 데이터 ZIP 복원에서 같은 파일을 선택하세요');return;}throw e;}
      if(full||restored.kind==='full'){state.user=null;closeModal();login();toast('전체 데이터를 복원했습니다. 다시 로그인하세요.');}else{await manageList();renderManage();await refreshMe();toast('매장 목록을 복원했습니다');}
    },t);
  });
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&state.modal&&!state.busy){closeModal();return;}
    if(event.key==='Tab'&&state.modal){const nodes=[...modalRoot.querySelectorAll('button,input,select,a[href]')].filter(n=>!n.disabled&&!n.hidden&&n.getClientRects().length);const first=nodes[0],last=nodes.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}}
  });
  let narrow=window.innerWidth<=680;window.addEventListener('resize',()=>{const next=window.innerWidth<=680;if(next!==narrow){narrow=next;if(state.user&&state.page==='report'&&state.report)renderReport();}});
  loginChoice();
})();
