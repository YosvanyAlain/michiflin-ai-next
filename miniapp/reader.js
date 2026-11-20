"use strict";
(() => {
  const M=window.Michiflin,{el,fill,tr,input,button}=M;
  let current=null;
  const latest=()=>M.transport.tasks.filter(task=>task.op==="web.read").at(-1);
  const view=()=>{
    const scope=M.files.scope(),panel=el("section",{class:"reader-workspace"});
    const url=el("input",{type:"url",maxlength:2048,placeholder:"https://…",autocomplete:"off",spellcheck:"false",value:latest()?.context?.url||""});
    const output=el("section",{class:"reader-results","aria-live":"polite"});
    const load=value=>{
      if(latest()?.status==="pending")return;
      try{const target=new URL(value);if(target.protocol!=="https:"||target.username||target.password||target.port&&target.port!=="443")throw new Error();}
      catch{M.notify(tr("invalid_web_url"));return;}
      url.value=value;
      if(M.transport.call("web.read",{url:value},{label:"reader",context:{url:value}}))render(latest());
    };
    const render=(task,page=0)=>{
      if(scope.disposed)return;
      scope.cancel();fill(output);
      if(!task)return;
      if(["pending","uncertain"].includes(task.status)){output.append(el("p",{class:"caption",role:"status"},tr(task.status==="pending"?"pending":"send_unconfirmed")));return;}
      if(task.status!=="done"){output.append(el("p",{class:"library-notice",role:"alert"},tr(task.error||"unknown_error")));return;}
      const data=task.data;
      if(data?.kind!=="plain_text_reader"||!Array.isArray(data.pages)||!data.pages.length||data.pages.some(item=>typeof item!=="string"))return;
      page=Math.max(0,Math.min(page,data.pages.length-1));
      const source=M.safeLink(data.url),prev=button("previous_page",()=>render(task,page-1),"secondary"),next=button("next_page",()=>render(task,page+1),"secondary");
      prev.disabled=page===0;next.disabled=page===data.pages.length-1;
      output.append(el("article",{class:"paper-panel reader-article"},el("h2",{},String(data.title||"")),source?el("a",{class:"reader-source",href:source,target:"_blank",rel:"noopener noreferrer"},data.url):null,el("p",{class:"caption"},tr("reader_fetched")+" · "+M.date(data.fetched_at)),el("pre",{class:"reader-text"},data.pages[page])));
      if(data.truncated)output.append(el("p",{class:"caption"},tr("reader_truncated")));
      output.append(el("div",{class:"reader-pagination"},prev,el("span",{role:"status"},tr("reader_page")+" "+(page+1)+" / "+data.pages.length),next));
      if(data.links?.length){
        const links=el("details",{class:"reader-links"},el("summary",{},tr("reader_links")));
        for(const link of data.links){const safe=M.safeLink(link.url);if(safe?.startsWith("https:"))links.append(el("button",{type:"button",class:"reader-link",onclick:()=>load(safe)},String(link.title||safe)));}
        output.append(links);
      }
      const text=String(data.title||"")+"\n"+String(data.url||"")+"\n\n"+data.pages.join("\n\n");
      output.append(scope.exportControl(new Blob([text],{type:"text/plain;charset=utf-8"}),"michiflin-reading.txt"),el("details",{class:"usage-details"},el("summary",{},tr("privacy_and_limits")),el("p",{class:"caption"},tr("file_export_notice"))));
    };
    const submit=button("reader_open",()=>load(url.value.trim()),"primary","reader");submit.dataset.remote="true";submit.disabled=!M.transport.ready;
    panel.append(el("form",{class:"reader-form",onsubmit:event=>{event.preventDefault();load(url.value.trim());}},input("reader_url",url),submit),el("p",{class:"caption"},tr("reader_privacy")),output);
    current={scope,render};render(latest());return panel;
  };
  document.addEventListener("michiflin:result",event=>{if(event.detail.op==="web.read"&&current&&!current.scope.disposed)current.render(latest());});
  document.addEventListener("michiflin:transport",()=>{if(latest()?.status==="uncertain"&&current&&!current.scope.disposed)current.render(latest());});
  M.reader={view};
})();
