"use strict";
(() => {
  const M=window.Michiflin;
  const pending=new Map();
  let tasks=M.storage.read("activity",[]);
  if (!Array.isArray(tasks)) tasks=[];
  tasks=tasks.filter(task=>task&&typeof task.id==="string").slice(-60);
  for(const task of tasks)if(task.status==="pending"){task.status="uncertain";task.error="send_unconfirmed";}
  let ready=false,session="",helloInFlight=false,cursor=M.storage.read("cursor",0);
  if(!Number.isSafeInteger(cursor)||cursor<0)cursor=0;
  const pieces=new Map();
  const savedFloors=M.storage.read("floors",{});
  const floors=Object.fromEntries(Object.entries(savedFloors&&typeof savedFloors==="object"?savedFloors:{}).filter(([id,value])=>/^[a-zA-Z0-9_-]{12,80}$/.test(id)&&Number.isSafeInteger(value)&&value>=0));
  const signal=()=>document.dispatchEvent(new CustomEvent("michiflin:transport"));
  const persist=()=>M.storage.write("activity",tasks.slice(-60).map(task=>{
    const copy={...task};
    if (copy.data && JSON.stringify(copy.data).length>160000) {delete copy.data;copy.large=true;}
    return copy;
  }));
  const identifier=()=>Array.from(crypto.getRandomValues(new Uint8Array(18)),value=>value.toString(16).padStart(2,"0")).join("");
  const call=(op,args={},options={})=>{
    if (!window.webxdc || (op!=="hello" && !ready)) {M.notify(M.tr("open_in_chat"));return null;}
    const id=identifier();
    const request={type:"michiflin.request",protocol:1,id,op,args,locale:M.locale(),session};
    if (new TextEncoder().encode(JSON.stringify(request)).length>32000) {M.notify(M.tr("request_too_large"));return null;}
    const task={id,op,at:Date.now(),status:"pending",label:options.label||op,context:options.context||null};
    if (!options.silent) {tasks.push(task);if (!persist()) {tasks.pop();M.notify(M.tr("storage_unavailable"));return null;}}
    let resolve,reject;
    const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});
    promise.catch(()=>{});
    const unconfirmed=()=>{
      const waiting=pending.get(id);if(!waiting)return;
      clearTimeout(waiting.timer);task.status="uncertain";task.error="send_unconfirmed";
      persist();pending.delete(id);reject(new Error("send_unconfirmed"));signal();
    };
    const timer=setTimeout(unconfirmed,op==="hello"?30000:180000);
    pending.set(id,{resolve,reject,task,timer,silent:!!options.silent});
    try {Promise.resolve(window.webxdc.sendUpdate({payload:request},"")).catch(unconfirmed);}
    catch {unconfirmed();}
    signal();
    return {id,promise};
  };
  const settle=(body)=>{
    if (!body || typeof body.id!=="string") return;
    const waiting=pending.get(body.id),task=waiting?.task||tasks.find(item=>item.id===body.id);
    if (!task || (!waiting && !["pending","uncertain"].includes(task.status))) return;
    task.status=body.ok ? "done":"error";task.finished=Date.now();
    if (body.ok) task.data=body.data;
    else {task.error=body.error||"unknown_error";if (body.error==="session_changed") {ready=false;session="";setTimeout(connect,0);}}
    delete floors[body.id];M.storage.write("floors",floors);
    if(waiting)clearTimeout(waiting.timer);
    persist();pending.delete(body.id);
    if (waiting) {if(body.ok)waiting.resolve(body.data);else waiting.reject(new Error(task.error));}
    signal();
    document.dispatchEvent(new CustomEvent("michiflin:result",{detail:task}));
  };
  const receive=(update)=>{
    if (!update || !Number.isSafeInteger(update.serial) || update.serial<1) return;
    const frame=update.payload;
    cursor=Math.max(cursor,update.serial);M.storage.write("cursor",cursor);
    if (!frame || frame.protocol!==1) return;
    if (frame.type==="michiflin.response") settle(frame.body);
    if (frame.type==="michiflin.chunk") {
      const {id,index,total,text}=frame;
      if (typeof id!=="string" || (!pending.has(id)&&!tasks.some(task=>task.id===id&&["pending","uncertain"].includes(task.status))) || !Number.isInteger(total)||total<1||total>23||!Number.isInteger(index)||index<0||index>=total||typeof text!=="string"||text.length>45000) return;
      if (!(id in floors)) {floors[id]=update.serial-1;M.storage.write("floors",floors);}
      const value=pieces.get(id)||{total,parts:new Map()};
      if(value.total!==total)return;
      if(value.parts.has(index)&&value.parts.get(index)!==text)return;
      value.parts.set(index,text);
      if(Array.from(value.parts.values()).reduce((sum,part)=>sum+part.length,0)>1000000){pieces.delete(id);return;}
      pieces.set(id,value);
      if(value.parts.size===total){
        try{const body=JSON.parse(Array.from({length:total},(_,part)=>value.parts.get(part)).join(""));if(body.id===id)settle(body);}catch{M.notify(M.tr("response_incomplete"));}
        pieces.delete(id);
      }
    }
  };
  const connect=()=>{
    if(helloInFlight||!window.webxdc)return;
    ready=false;helloInFlight=true;signal();
    const request=call("hello",{}, {silent:true});
    if(!request){helloInFlight=false;return;}
    request.promise.then(data=>{
      if(typeof data?.session!=="string"||!data.session)throw new Error("invalid_handshake");
      session=data.session;ready=true;M.storage.write("service",{...data,at:Date.now()});
    }).catch(()=>{ready=false;}).finally(()=>{helloInFlight=false;signal();});
  };
  const start=async()=>{
    if(typeof window.webxdc?.setUpdateListener!=="function"){signal();return;}
    try {await window.webxdc.setUpdateListener(receive,Math.min(cursor,...Object.values(floors)));connect();}
    catch {M.notify(M.tr("connection_unavailable"));}
  };
  M.transport={start,call,connect,get ready(){return ready;},get tasks(){return tasks;},get service(){return M.storage.read("service",null);}};
})();
