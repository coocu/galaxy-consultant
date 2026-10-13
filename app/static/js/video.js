(() => {
  'use strict';
  const player=document.getElementById('player'),start=document.getElementById('start');
  let playlist=[],index=0,signature='',timer=null,failures=0;
  function blank(){player.pause();player.removeAttribute('src');player.load();start.hidden=true;}
  function play(){
    if(!playlist.length){blank();return;}
    index%=playlist.length;player.src=playlist[index].url;player.loop=playlist.length===1;player.muted=true;
    player.play().then(()=>{start.hidden=true;}).catch(()=>{start.hidden=false;});
  }
  player.addEventListener('ended',()=>{failures=0;index=(index+1)%Math.max(1,playlist.length);play();});
  player.addEventListener('playing',()=>{failures=0;start.hidden=true;});
  player.addEventListener('error',()=>{start.hidden=true;clearTimeout(timer);if(!playlist.length)return;failures++;if(failures>=playlist.length){blank();timer=setTimeout(()=>{failures=0;play();},30000);}else{index=(index+1)%playlist.length;timer=setTimeout(play,1000);}});
  start.addEventListener('click',()=>{player.play().then(()=>{start.hidden=true;}).catch(()=>{});});
  async function refresh(){try{const response=await fetch('/api/video-playlist',{cache:'no-store'});if(!response.ok)return;const data=await response.json();const next=JSON.stringify(data.videos);if(next!==signature){signature=next;playlist=data.videos;index=0;failures=0;clearTimeout(timer);play();}}catch{}}
  refresh();const poll=setInterval(refresh,30000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});window.addEventListener('beforeunload',()=>{clearInterval(poll);clearTimeout(timer);});
})();
