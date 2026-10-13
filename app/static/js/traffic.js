/* 고객/호출 화면을 실제로 연 횟수만 기록. 화면 갱신으로 중복 집계하지 않는다. */
(() => {
  const root=document.getElementById('app'),sent=new Set();let scheduled=false;
  function record(){scheduled=false;if(typeof appState==='undefined'||!['/customer','/display'].includes(appState.route))return;
    const s=appState.route==='/customer'?appState.selectedCustomerStore:appState.selectedDisplayStore;if(!s)return;
    const key=appState.route+':'+s.id;if(sent.has(key))return;sent.add(key);
    const params=new URLSearchParams(location.search);let source=params.get('utm_source')||params.get('source')||'direct';
    if(source==='direct'){try{const ref=new URL(document.referrer);if(ref.origin!==location.origin)source=ref.hostname;}catch{}}
    const id=crypto.randomUUID?crypto.randomUUID().replaceAll('-',''):'visit_'+Date.now()+'_'+Math.random().toString(36).slice(2);
    fetch('/api/traffic',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,store_id:s.id,path:appState.route,source:source.slice(0,120)})}).catch(()=>{});
  }
  const schedule=()=>{if(!scheduled){scheduled=true;requestAnimationFrame(record);}};
  if(root)new MutationObserver(schedule).observe(root,{childList:true,subtree:true});schedule();
})();
