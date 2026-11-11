'use strict';
const $ = id => document.getElementById(id);
let state = null, dirty = false, formRevision = null, editing = null, editRevision = null;
let busy = 0, polling = false, lastDevices = '', lastLogs = '', toastTimer;
const labels = {running:'Ativa',waiting:'Aguardando TV',paused:'Pausada',stopped:'Parada',unavailable:'Motor indisponível',restarting:'Reconectando',config_error:'Verificar configuração'};
const descriptions = {
  running:'O container oficial está em execução, independente do painel. Com o YouTube aberto na TV vinculada, os pulos acontecem automaticamente.',
  waiting:'Adicione uma TV para iniciar a proteção automaticamente.',
  paused:'A proteção está pausada. Retome quando quiser voltar a pular os trechos.',
  restarting:'O motor está iniciando ou tentando se recuperar. Os registros abaixo mostram a atividade.',
  config_error:'A configuração precisa de correção. Importe um backup válido para continuar.',
  stopped:'O container oficial está parado. Toque em Retomar proteção para iniciá-lo.',
  unavailable:'O painel está disponível, mas não conseguiu consultar o container oficial. Uma atualização do Watchtower pode causar essa indisponibilidade temporária.'
};

function node(tag, cls, content) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (content !== undefined) el.textContent = content;
  return el;
}
function toast(message, error=false) {
  $('toast').textContent=message; $('toast').classList.toggle('error',error); $('toast').hidden=false;
  clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').hidden=true,error?8000:4500);
}
async function api(path, data) {
  const options={cache:'no-store'};
  if(data!==undefined){options.method='POST';options.headers={'Content-Type':'application/json','X-Sal0-Request':'1'};options.body=JSON.stringify(data);}
  const controller = new AbortController();
  const timer=setTimeout(()=>controller.abort(),45000);
  options.signal=controller.signal;
  try {
    const res=await fetch(path.replace(/^\//,''),options);
    let body;
    try{body=await res.json();}catch{throw new Error('Resposta inválida do servidor. Confira a conexão.');}
    if(!res.ok){const err=new Error(body.error||'Não foi possível concluir.');err.status=res.status;throw err;}
    return body;
  } catch(err) {
    if(err.name==='AbortError')throw new Error('A resposta demorou mais que o esperado. Aguarde e atualize o painel.');
    throw err;
  } finally {clearTimeout(timer);}
}
async function pending(button, label, action, errorTarget) {
  if(button.disabled)return;
  const original=button.textContent;
  button.disabled=true;button.textContent=label;busy++;
  if(errorTarget)$(errorTarget).hidden=true;
  try{await action();}
  catch(err){if(errorTarget){$(errorTarget).textContent=err.message;$(errorTarget).hidden=false;}else toast(err.message,true);}
  finally{busy--;button.textContent=button===$('toggle-service')&&state?(state.engine.enabled?'Pausar proteção':'Retomar proteção'):original;button.disabled=false;if(button===$('save-settings'))button.disabled=!dirty;}
}
function duration(seconds) {
  const h=Math.floor(seconds/3600),m=Math.floor(seconds%3600/60),s=seconds%60;
  return [h,m,s].map(x=>String(x).padStart(2,'0')).join(':');
}
function setBadge(id,s){const e=$(id);e.textContent=labels[s]||s;e.className='badge '+s;}
function markDirty(){dirty=true;$('save-settings').disabled=false;$('save-state').textContent='Alterações não salvas';$('save-state').classList.add('dirty');updateSelected();}
function updateSelected(){$('selected-label').textContent=document.querySelectorAll('[data-category]:checked').length+' selecionadas';}
function populateSettings(s) {
  const c=s.settings;
  const wrap=$('categories');wrap.replaceChildren();
  for(const category of s.categories){
    const label=node('label','category'),input=node('input');
    input.type='checkbox';input.dataset.category=category.id;input.checked=c.skip_categories.includes(category.id);
    const info=node('span');info.append(node('strong','',category.name),node('small','',category.description));
    label.append(input,info);wrap.append(label);
  }
  for(const key of ['mute_ads','skip_ads','auto_play','skip_count_tracking','use_proxy'])$(key).checked=c[key];
  for(const key of ['minimum_skip_length','join_name','sponsorblock_api_url'])$(key).value=c[key];
  $('apikey').value='';$('clear_api_key').checked=false;
  $('apikey').placeholder=c.has_api_key?'Chave salva — deixe em branco para manter':'Opcional';
  $('key-hint').textContent=c.has_api_key?'A chave salva é mantida se este campo ficar em branco.':'Necessária apenas para permitir canais.';
  $('channel_whitelist').value=c.channel_whitelist.map(x=>x.id+' | '+x.name).join('\n');
  dirty=false;formRevision=s.revision;$('save-state').textContent='Tudo salvo';$('save-state').classList.remove('dirty');$('save-settings').disabled=true;updateSelected();
}
function editDevice(d){
  editing=d;editRevision=state.revision;$('edit-name').value=d.name;$('edit-offset').value=d.offset;$('edit-error').hidden=true;
  $('edit-dialog').showModal();$('edit-name').focus();
}
function confirmAction(title,message,label,action){
  $('confirm-title').textContent=title;$('confirm-message').textContent=message;$('confirm-action').textContent=label;$('confirm-error').hidden=true;
  $('confirm-action').onclick=()=>pending($('confirm-action'),'Aguarde…',async()=>{await action();$('confirm-dialog').close();},'confirm-error');
  $('confirm-dialog').showModal();
}
function renderDevices(s){
  const key=JSON.stringify(s.devices);
  if(key===lastDevices)return;lastDevices=key;
  const wrap=$('device-list');wrap.replaceChildren();$('empty-devices').hidden=s.devices.length>0;
  for(const d of s.devices){
    const card=node('article','card device-card'),icon=node('div','device-icon'),img=node('img');img.src='icon.svg';img.alt='';icon.append(img);
    const info=node('div','device-text');info.append(node('h3','',d.name),node('p','','Vinculada ao YouTube'+(d.offset?' · '+d.offset+' ms':'')));
    const actions=node('div','device-actions'),edit=node('button','text-button','Editar'),remove=node('button','text-button','×');
    edit.type='button';remove.type='button';edit.setAttribute('aria-label','Editar '+d.name);remove.setAttribute('aria-label','Remover '+d.name);remove.title='Remover dispositivo';
    edit.onclick=()=>editDevice(d);
    remove.onclick=()=>{const revision=state.revision;confirmAction('Remover dispositivo?',d.name+' deixará de receber a proteção deste painel. Você poderá vinculá-la novamente pelo código da TV.','Remover TV',async()=>{const s=await api('/api/devices/remove',{id:d.id,revision});accept(s);toast('Dispositivo removido.');});};
    actions.append(edit,remove);card.append(icon,info,actions);wrap.append(card);
  }
}
function translateLog(message){
  if(message.includes('Connected to device'))return 'YouTube conectado em um dispositivo.';
  if(message.includes('Playing video')){const n=message.match(/with (\d+) segments/);return 'Reprodução detectada'+(n?' · '+n[1]+' trechos encontrados.':'.');}
  if(message.includes('Skipping segment:'))return 'Comando para pular um trecho enviado.';
  if(message.includes('Ad can be skipped'))return 'Anúncio liberou o botão de pular.';
  if(message.includes('Ad has started, muting'))return 'Anúncio detectado. Silenciando.';
  if(message.includes('unmuting'))return 'Som da reprodução restaurado.';
  if(message.includes('Getting segments for next video'))return 'Consultando os trechos do próximo vídeo.';
  return message;
}
function renderLogs(entries){
  const key=JSON.stringify(entries);if(lastLogs===key)return;lastLogs=key;
  const openIds=new Set([...$('activity-list').querySelectorAll('details[open]')].map(x=>x.dataset.id));
  const wrap=$('activity-list');wrap.replaceChildren();$('empty-activity').hidden=entries.length>0;
  for(const event of [...entries].reverse().slice(0,100)){
    const item=node('div','activity-item'),date=new Date(event.time*1000),stamp=node('time','',date.toLocaleTimeString('pt-BR'));
    stamp.dateTime=date.toISOString();stamp.title=date.toLocaleString('pt-BR');
    const body=node('div','activity-message'),message=translateLog(event.message);body.append(node('div','',message));
    if(message!==event.message){const details=node('details'),summary=node('summary','','Registro original');details.dataset.id=event.id;details.open=openIds.has(String(event.id));details.append(summary,node('pre','',event.message));body.append(details);}
    item.append(stamp,body);wrap.append(item);
  }
}
function accept(s,{settings=false}={}){
  const initial=state===null;state=s;$('offline').hidden=true;$('connection').textContent='Conectado';$('connection').classList.remove('offline');
  $('version').textContent=s.version;$('device-count').textContent=s.devices.length;$('category-count').textContent=s.settings.skip_categories.length;
  $('config-error').textContent=s.config_error||'';$('config-error').hidden=!s.config_error;
  const e=s.engine;setBadge('engine-badge',e.state);setBadge('activity-badge',e.state);
  $('protection-label').textContent=labels[e.state]||e.state;
  $('uptime').textContent=e.running?'Em execução há '+duration(e.uptime):e.state==='paused'?'Retome pela aba Atividade':e.state==='waiting'?'Pronta para vincular':'Consulte a aba Atividade';
  $('service-description').textContent=descriptions[e.state]||'';$('toggle-service').textContent=e.enabled?'Pausar proteção':'Retomar proteção';
  $('engine-error').textContent=e.last_error||'';$('engine-error').hidden=!e.last_error;
  $('toggle-service').disabled=e.state==='unavailable';$('restart-service').disabled=e.state==='unavailable';
  renderDevices(s);renderLogs(e.logs);
  if(initial||settings||(!dirty&&formRevision!==s.revision))populateSettings(s);
}
async function refresh(){
  if(polling||busy)return;polling=true;
  try{accept(await api('/api/state'));}
  catch(err){$('offline').hidden=false;$('connection').textContent='Sem conexão';$('connection').classList.add('offline');}
  finally{polling=false;}
}
function tab(name){
  for(const button of document.querySelectorAll('[data-tab]')){const active=button.dataset.tab===name;button.classList.toggle('active',active);button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;$('view-'+button.dataset.tab).hidden=!active;}
}
function openPair(){if(!state){toast('Aguarde a conexão com o servidor.',true);return;}$('pair-error').hidden=true;$('pair-dialog').showModal();$('pair-code').focus();}
for(const id of ['add-device','empty-add'])$(id).onclick=openPair;
for(const b of document.querySelectorAll('[data-tab]')){b.onclick=()=>tab(b.dataset.tab);b.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const tabs=[...document.querySelectorAll('[data-tab]')],i=tabs.indexOf(b);const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(i+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tab(tabs[next].dataset.tab);tabs[next].focus();};}
for(const b of document.querySelectorAll('.close-dialog'))b.onclick=()=>{if(busy===0)b.closest('dialog').close();};
for(const dialog of document.querySelectorAll('dialog'))dialog.addEventListener('cancel',e=>{if(busy)e.preventDefault();});
$('pair-code').addEventListener('input',()=>{const code=$('pair-code').value.replace(/\D/g,'').slice(0,12);$('pair-code').value=code.match(/.{1,3}/g)?.join(' ')||'';});
$('pair-form').addEventListener('submit',event=>{event.preventDefault();pending($('pair-submit'),'Vinculando…',async()=>{
  accept(await api('/api/pair',{code:$('pair-code').value,name:$('pair-name').value}));$('pair-dialog').close();$('pair-form').reset();toast(state.engine.enabled?'TV vinculada. A proteção será iniciada automaticamente.':'TV vinculada. A proteção continua pausada.');
},'pair-error');});
$('edit-form').addEventListener('submit',event=>{event.preventDefault();pending($('edit-submit'),'Salvando…',async()=>{
  accept(await api('/api/devices/update',{id:editing.id,name:$('edit-name').value,offset:Number($('edit-offset').value),revision:editRevision}));$('edit-dialog').close();toast('Dispositivo atualizado.');
},'edit-error');});
$('settings-form').addEventListener('input',markDirty);
$('settings-form').addEventListener('change',markDirty);
$('settings-form').addEventListener('invalid',event=>{const details=event.target.closest('details');if(details)details.open=true;},true);
$('settings-form').addEventListener('submit',event=>{event.preventDefault();pending($('save-settings'),'Aplicando…',async()=>{
  const patch={skip_categories:[...document.querySelectorAll('[data-category]:checked')].map(x=>x.dataset.category),minimum_skip_length:Number($('minimum_skip_length').value),join_name:$('join_name').value,sponsorblock_api_url:$('sponsorblock_api_url').value,apikey:$('apikey').value,clear_api_key:$('clear_api_key').checked};
  for(const key of ['mute_ads','skip_ads','auto_play','skip_count_tracking','use_proxy'])patch[key]=$(key).checked;
  patch.channel_whitelist=$('channel_whitelist').value.split('\n').filter(x=>x.trim()).map(line=>{const at=line.indexOf('|');const id=(at<0?line:line.slice(0,at)).trim();return{id,name:at<0?id:line.slice(at+1).trim()||id};});
  const result=await api('/api/settings',{settings:patch,revision:formRevision});accept(result,{settings:true});toast('Configurações salvas e aplicadas.');
});});
$('toggle-service').onclick=()=>pending($('toggle-service'),'Aguarde…',async()=>{if(!state)return;const enabled=state.engine.enabled;accept(await api('/api/service',{action:enabled?'pause':'resume'}));toast(enabled?'Proteção pausada.':'Proteção retomada.');});
$('restart-service').onclick=()=>pending($('restart-service'),'Reiniciando…',async()=>{accept(await api('/api/service',{action:'restart'}));toast('Reinício solicitado.');});
for(const b of document.querySelectorAll('.import-trigger'))b.onclick=()=>$('import-file').click();
$('import-file').onchange=async()=>{
  const file=$('import-file').files[0];$('import-file').value='';if(!file||!state)return;
  try{if(file.size>1024*1024)throw new Error('O arquivo deve ter até 1 MB.');const cfg=JSON.parse(await file.text());const revision=state.revision;
    confirmAction('Importar configuração?','As TVs e opções atuais serão substituídas pelas deste arquivo. Uma cópia da configuração anterior será guardada automaticamente.','Importar e aplicar',async()=>{accept(await api('/api/import',{config:cfg,revision}),{settings:true});toast('Configuração importada.');});
  }catch(err){toast(err instanceof SyntaxError?'O arquivo não contém um JSON válido.':err.message,true);}
};
$('refresh').onclick=()=>pending($('refresh'),'Atualizando…',async()=>accept(await api('/api/state')));
try{document.body.classList.toggle('light',localStorage.getItem('sal0-theme')==='light');}catch{}
$('theme').onclick=()=>{const light=document.body.classList.toggle('light');try{localStorage.setItem('sal0-theme',light?'light':'dark');}catch{}};
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
refresh();setInterval(()=>{if(!document.hidden)refresh();},5000);
