"use strict";
(() => {
  const M=window.Michiflin,{el,fill,tr,input,button}=M;
  let current=null;
  const latest=()=>M.transport.tasks.filter(task=>task.op==="media.inspect").at(-1);
  const view=()=>{
    const panel=el("section",{class:"reader-workspace"});
    const url=el("input",{type:"url",maxlength:2048,placeholder:"https://youtu.be/…",autocomplete:"off",spellcheck:"false",value:latest()?.context?.url||""});
    const output=el("section",{class:"media-preview","aria-live":"polite"});
    let disposed=false;document.addEventListener("michiflin:navigate",()=>{disposed=true;},{once:true});
    const render=()=>{
      if(disposed)return;
      const task=latest();fill(output);if(!task)return;
      if(["pending","uncertain"].includes(task.status)){output.append(el("p",{class:"caption",role:"status"},tr(task.status==="pending"?"pending":"send_unconfirmed")));return;}
      if(task.status!=="done"){output.append(el("p",{class:"library-notice",role:"alert"},tr(task.error||"unknown_error")));return;}
      const data=task.data;if(data?.kind!=="youtube_metadata")return;
      const safe=M.safeLink(data.url);
      output.append(el("article",{class:"paper-panel reader-article"},el("h2",{},String(data.title||"")),el("p",{},tr("media_channel")+": "+String(data.author||"")),el("p",{class:"caption"},String(data.source||"")+" · "+M.date(data.fetched_at)),el("p",{class:"library-notice"},tr("media_preview_only")),safe?el("a",{class:"button secondary",href:safe,target:"_blank",rel:"noopener noreferrer"},tr("media_open_source")):null));
    };
    const load=()=>{
      if(latest()?.status==="pending")return;
      const value=url.value.trim();
      try{const parsed=new URL(value);if(parsed.protocol!=="https:"||parsed.username||parsed.password)throw new Error();}
      catch{M.notify(tr("invalid_youtube_url"));return;}
      if(M.transport.call("media.inspect",{url:value},{label:"youtube",context:{url:value}}))render();
    };
    const submit=button("media_lookup",load,"primary","youtube");submit.dataset.remote="true";submit.disabled=!M.transport.ready;
    panel.append(el("form",{class:"reader-form",onsubmit:event=>{event.preventDefault();load();}},input("media_url",url),submit),el("p",{class:"caption"},tr("media_privacy")),output);
    current={render,get disposed(){return disposed;}};render();return panel;
  };
  document.addEventListener("michiflin:result",event=>{if(event.detail.op==="media.inspect"&&current&&!current.disposed)current.render();});
  document.addEventListener("michiflin:transport",()=>{if(latest()?.status==="uncertain"&&current&&!current.disposed)current.render();});
  M.media={view};
})();
