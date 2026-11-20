"use strict";
(() => {
  const M=window.Michiflin;
  const limit=20*1024*1024;
  const scope=()=>{
    const urls=new Set();let disposed=false,epoch=0;
    const cancel=()=>{epoch++;for(const value of urls)URL.revokeObjectURL(value);urls.clear();};
    document.addEventListener("michiflin:navigate",()=>{disposed=true;cancel();},{once:true});
    const current=()=>{const revision=++epoch;return ()=>!disposed&&revision===epoch;};
    const url=blob=>{if(disposed)throw new Error("file_error");const value=URL.createObjectURL(blob);urls.add(value);return value;};
    const exportControl=(blob,name,label="export_file")=>{
      if(!(blob instanceof Blob)||blob.size>limit||!/^michiflin-[a-z0-9-]+\.[a-z0-9]+$/.test(name))throw new Error("file_limit");
      if(typeof window.webxdc?.sendToChat==="function"){
        let pending=false;
        const control=M.button("prepare_file_draft",async()=>{
          if(pending||disposed)return;
          pending=true;control.disabled=true;
          try{await window.webxdc.sendToChat({file:{name,blob}});}
          catch{M.notify(M.tr("file_share_failed"));}
          finally{pending=false;if(!disposed)control.disabled=false;}
        },"primary","external");
        return control;
      }
      return M.el("a",{href:url(blob),download:name,class:"button primary"},M.tr(label));
    };
    return {cancel,current,url,exportControl,get disposed(){return disposed;}};
  };
  M.files={scope,limit};
})();
