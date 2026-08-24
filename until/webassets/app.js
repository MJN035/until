 document.addEventListener('submit',function(e){
   var f=e.target; if(f&&f.method&&f.method.toLowerCase()==='post'){
     var m={'/inbox':'과제 목록을 불러오는 중','/pick':'과제와 관련 자료를 읽는 중',
            '/collect':'과제와 관련 자료를 읽는 중','/draft':'요구사항을 초안에 반영하는 중','/finalize':'결정 내용을 반영하는 중',
            '/suggest':'결정에 참고할 선택지를 만드는 중','/review':'제출 전 항목을 확인하는 중'};
     // 액션별 예상 소요(초) — 진행 바·안내용 경험치(정확한 진행률은 알 수 없음).
     var est={'/inbox':15,'/pick':50,'/collect':50,'/draft':35,'/finalize':35,
              '/suggest':15,'/review':15};
     var a=(f.getAttribute('action')||''); var el=document.getElementById('ovmsg');
     if(el&&m[a])el.textContent=m[a];
     // 폼이 직접 지정한 맞춤 메시지가 있으면 우선(예: SSO 로그인 안내).
     var lm=f.getAttribute('data-loadmsg'); if(el&&lm)el.textContent=lm;
     var sub=document.getElementById('ovsub'); var sm=f.getAttribute('data-submsg');
     if(sub&&sm)sub.textContent=sm;
     document.getElementById('ov').classList.add('on');
     ovStart(parseInt(f.getAttribute('data-ovsec')||'',10)||est[a]||30);
   }
 });
 window.addEventListener('pageshow',function(){document.getElementById('ov').classList.remove('on');ovStop();});
 function fastmsg(f){f.dataset.loadmsg='가장 가까운 과제를 확인하는 중';f.dataset.submsg='과제 선택, 자료 확인, 초안 작성을 순서대로 진행합니다';f.dataset.ovsec='50';}
 // 빈칸 자동 채움 — 폼 action(/finalize)이 아니라 formaction(/suggest)으로 나가므로
 // 오버레이 문구를 버튼에서 직접 지정한다(클릭이 submit보다 먼저 실행된다).
 function fillmsg(f){f.dataset.loadmsg='내가 정한 답에 맞춰 나머지를 채우는 중';f.dataset.submsg='이번에 채운 답과 내 지난 내역·수업 자료·말투를 함께 봅니다';f.dataset.ovsec='20';}
 // 클립보드에서 토큰 붙여넣기 — 권한 불가 브라우저는 입력칸 포커스로 폴백.
 function pasteTok(btn){var t=document.getElementById('tok');if(!t)return;
   if(navigator.clipboard&&navigator.clipboard.readText){
     navigator.clipboard.readText().then(function(v){v=(v||'').trim();
       if(v){t.value=v;btn.textContent='붙여넣음';setTimeout(function(){btn.textContent='붙여넣기';},1500);}
       else{t.focus();btn.textContent='복사한 내용 없음';setTimeout(function(){btn.textContent='붙여넣기';},1500);}
     },function(){t.focus();btn.textContent='여기에 Ctrl+V';setTimeout(function(){btn.textContent='붙여넣기';},2000);});
   }else{t.focus();btn.textContent='여기에 Ctrl+V';setTimeout(function(){btn.textContent='붙여넣기';},2000);}}
 function copyDoc(id,btn,session){var t=document.getElementById(id).value;
   navigator.clipboard.writeText(t).then(function(){var o=btn.textContent;btn.textContent='복사됨';
     if(session)fetch('/telemetry/export',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'session='+encodeURIComponent(session),keepalive:true}).catch(function(){});
     setTimeout(function(){btn.textContent=o},1500);},function(){btn.textContent='복사 실패';});}
 function downloadDoc(id,name){var t=document.getElementById(id).value;
   var b=new Blob([t],{type:'text/markdown;charset=utf-8'});var a=document.createElement('a');
   a.href=URL.createObjectURL(b);a.download=name;document.body.appendChild(a);a.click();
   a.remove();URL.revokeObjectURL(a.href);}
 function _sysDark(){return window.matchMedia&&window.matchMedia('(prefers-color-scheme:dark)').matches;}
 function _curTheme(){var t=document.documentElement.getAttribute('data-theme');return t?t:(_sysDark()?'dark':'light');}
 function _label(){var b=document.getElementById('tg');if(b)b.textContent=(_curTheme()==='dark'?'LIGHT':'DARK');}
 function toggleTheme(){var nxt=_curTheme()==='dark'?'light':'dark';
   document.documentElement.setAttribute('data-theme',nxt);
   try{localStorage.setItem('until-theme',nxt);}catch(e){} _label();}
 (function(){try{var s=localStorage.getItem('until-theme');if(s)document.documentElement.setAttribute('data-theme',s);}catch(e){} _label();})();
 // 결정 칩 클릭 → 입력칸 채우기(타이핑 줄이기)
 function pick(id,el){var t=document.getElementById(id);if(!t)return;t.value=el.dataset.val||el.textContent;
   var p=el.parentElement;if(p)p.querySelectorAll('.chip').forEach(function(c){c.classList.remove('on');});
   el.classList.add('on');t.focus();
   try{t.dispatchEvent(new Event('input'));}catch(e){}}
 // Esc로 로딩 오버레이 닫기
 document.addEventListener('keydown',function(e){if(e.key==='Escape')document.getElementById('ov').classList.remove('on');});
 // 비밀번호(토큰) 입력에 표시/숨김 토글 자동 부착
 document.querySelectorAll('input[type=password]').forEach(function(inp){
   var b=document.createElement('button');b.type='button';b.className='eye';b.textContent='표시';
   b.setAttribute('aria-label','토큰 표시/숨김');
   b.onclick=function(){var pw=inp.type==='password';inp.type=pw?'text':'password';b.textContent=pw?'숨김':'표시';};
   inp.insertAdjacentElement('afterend',b);
 });
// 로딩 오버레이 — 진행 바(예상치 기반) + 문구 로테이션 + 경과 시간(살아있음 표시)
 var _ovt=null;
 function ovStart(sec){sec=sec||30;
   var ph=['과제 본문을 읽는 중','첨부 자료를 확인하는 중','요구사항을 초안과 대조하는 중','직접 결정할 항목을 구분하는 중'];
   var el=document.getElementById('ovph'),tm=document.getElementById('ovtm'),
       bar=document.getElementById('ovbar'),i=0,k=0;
   if(el){el.textContent=ph[0];el.style.opacity=1;}
   if(bar)bar.style.width='4%';
   ovStop();_ovt=setInterval(function(){k++;
     // 예상 소요에 수렴하는 진행 바 — 예상보다 오래 걸려도 95%에서 대기(거짓 완료 없음).
     if(bar)bar.style.width=Math.min(95,Math.round(95*(1-Math.exp(-k/(sec*0.62)))))+'%';
     if(tm)tm.textContent=k+'s · 보통 ~'+sec+'초';
     if(k%3===0&&el){el.style.opacity=0;
       setTimeout(function(){i++;el.textContent=ph[i%ph.length];el.style.opacity=1;},300);}
   },1000);}
 function ovStop(){if(_ovt){clearInterval(_ovt);_ovt=null;}
   var tm=document.getElementById('ovtm');if(tm)tm.textContent='';
   var bar=document.getElementById('ovbar');if(bar)bar.style.width='0';}
 // 답 입력 로컬 보존 — 새로고침·이탈로 쓰던 답이 사라지지 않게(이 브라우저에만 저장,
 // 제출(finalize) 성공 제출 시 지움. 서버로는 폼 제출 외에 아무것도 안 나감.)
 (function(){
   var f=document.querySelector('form[action="/finalize"]');if(!f)return;
   var s=f.querySelector('input[name=session]');if(!s||!s.value)return;var sid=s.value;
   var ta=f.querySelectorAll('textarea[name^=answer_]');
   ta.forEach(function(t){var k='until-ans:'+sid+':'+t.name;
     try{var v=localStorage.getItem(k);if(v&&!t.value)t.value=v;}catch(e){}
     t.addEventListener('input',function(){try{
       if(t.value)localStorage.setItem(k,t.value);else localStorage.removeItem(k);}catch(e){}});
   });
   f.addEventListener('submit',function(){ta.forEach(function(t){
     try{localStorage.removeItem('until-ans:'+sid+':'+t.name);}catch(e){}});});
 })();
 // 과제 입력 글자 수 카운터(공백 제외 — 분량 요건 감각)
 document.querySelectorAll('textarea[name=assignment]').forEach(function(t){
   var c=document.createElement('div');c.className='cnt';
   t.insertAdjacentElement('afterend',c);
   var f=function(){var n=t.value.replace(/\s/g,'').length;
     c.textContent=n?n.toLocaleString()+'자 · 공백 제외':'';};
   t.addEventListener('input',f);f();
 });
