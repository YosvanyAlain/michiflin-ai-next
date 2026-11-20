"use strict";
(() => {
  const M=window.Michiflin,{el,fill,tr,button}=M,limit=12288;
  const view=()=>{
    const scope=M.files.scope(),panel=el("section",{class:"server-files"});
    const picker=el("input",{type:"file","aria-label":tr("server_file_choose")});
    const output=el("div",{class:"server-file-output","aria-live":"polite"}),list=el("div",{class:"server-file-list"});
    let rows=[],busy=false;
    const remote=(label,action,op,style="secondary")=>{
      const control=button(label,action,style);
      control.dataset.remote="true";control.dataset.requiredOperation=op;
      control.disabled=!M.transport.ready||!M.transport.service?.operations?.includes(op);
      return control;
    };
    const setBusy=value=>{
      busy=value;
      for(const control of panel.querySelectorAll("[data-remote]")){
        control.dataset.busy=String(value);
        control.disabled=value||!M.transport.ready||!M.transport.service?.operations?.includes(control.dataset.requiredOperation);
      }
    };
    const validRows=data=>{
      if(!Array.isArray(data?.files)||data.files.length>50||data.files.some(row=>!row||!/^[a-f0-9]{32}$/.test(row.id)||typeof row.name!=="string"||!Number.isSafeInteger(row.bytes)||row.bytes<0))throw new Error("result_invalid");
      return data.files;
    };
    const request=async(op,args,done)=>{
      if(busy||scope.disposed)return;
      setBusy(true);fill(output,el("p",{class:"caption",role:"status"},tr("pending")));
      try{
        const call=M.transport.call(op,args,{label:"server_files",silent:op==="files.read"});
        if(!call){fill(output);return;}
        const data=await call.promise;if(scope.disposed)return;
        fill(output);await done(data);
      }catch(error){if(!scope.disposed)fill(output,el("p",{class:"library-notice",role:"alert"},tr(error.message||"unknown_error")));}
      finally{if(!scope.disposed)setBusy(false);}
    };
    const read=row=>request("files.read",{id:row.id},async data=>{
      if(data.id!==row.id||typeof data.data!=="string"||data.data.length>16384||!Number.isSafeInteger(data.bytes)||data.bytes>limit||!/^[a-f0-9]{64}$/.test(data.sha256))throw new Error("result_invalid");
      const bytes=Uint8Array.from(atob(data.data),c=>c.charCodeAt(0));
      if(!crypto.subtle)throw new Error("file_hash_unavailable");
      const digest=Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",bytes)),v=>v.toString(16).padStart(2,"0")).join("");
      if(bytes.length!==data.bytes||digest!==data.sha256)throw new Error("artifact_hash_mismatch");
      if(scope.disposed)return;
      scope.cancel();
      output.append(el("p",{class:"caption"},data.name),scope.exportControl(new Blob([bytes],{type:"application/octet-stream"}),"michiflin-copy-"+row.id.slice(0,8)+".bin"),el("p",{class:"caption"},tr("server_file_export_notice")));
    });
    const confirmDelete=row=>{
      if(busy)return;
      const dialog=el("dialog",{class:"preferences-dialog server-delete-dialog"});
      dialog.append(el("h2",{},tr("server_delete_title")),el("p",{},row.name),el("p",{class:"caption"},tr("server_delete_notice")),button("cancel",()=>dialog.close(),"secondary"),button("server_delete_confirm",()=>{
        dialog.close();request("files.delete",{id:row.id},data=>{
          if(data.id!==row.id||data.deleted!==true)throw new Error("result_invalid");
          rows=rows.filter(value=>value.id!==row.id);render();fill(output,el("p",{class:"caption",role:"status"},tr("server_file_deleted")));
        });
      },"primary"));
      dialog.addEventListener("close",()=>dialog.remove(),{once:true});
      document.addEventListener("michiflin:navigate",()=>dialog.remove(),{once:true});
      document.body.append(dialog);dialog.showModal();
    };
    const render=()=>{
      fill(list);
      if(!rows.length)list.append(el("p",{class:"caption"},tr("server_files_empty")));
      for(const row of rows){
        const actions=el("div",{class:"server-file-actions"});
        if(row.state==="ready"){
          if(row.bytes<=limit)actions.append(remote("server_file_fetch",()=>read(row),"files.read"));
          actions.append(remote("server_file_verify",()=>request("files.checksum",{id:row.id},data=>{
            if(!/^[a-f0-9]{32}$/.test(data?.id))throw new Error("result_invalid");
            fill(output,el("p",{class:"caption",role:"status"},tr("server_file_verify_queued")),el("code",{},data.id),button("server_jobs",()=>{location.hash="server_jobs";},"secondary"));
          }),"files.checksum"),remote("delete",()=>confirmDelete(row),"files.delete","text"));
        }
        list.append(el("article",{class:"paper-panel server-file-row","data-file-id":row.id},el("h3",{},row.name),el("p",{class:"caption"},row.bytes+" B · "+tr("artifact_state_"+row.state)),row.error?el("p",{class:"caption"},tr(row.error)):null,el("details",{},el("summary",{},"SHA-256"),el("code",{},row.sha256)),actions));
      }
    };
    const upload=async()=>{
      const file=picker.files?.[0];if(!file||busy)return;
      if(file.size>limit){fill(output,el("p",{role:"alert",class:"library-notice"},tr("artifact_inline_limit")));return;}
      try{
        setBusy(true);const bytes=new Uint8Array(await file.arrayBuffer());if(scope.disposed)return;
        const encoded=btoa(Array.from(bytes,v=>String.fromCharCode(v)).join(""));setBusy(false);
        await request("files.put",{name:file.name,data:encoded},data=>{
          const row=validRows({files:[data]})[0];rows=[row,...rows.filter(value=>value.id!==row.id)].slice(0,50);render();picker.value="";
          fill(output,el("p",{class:"caption",role:"status"},tr(row.state==="ready"?"server_file_saved":"artifact_outcome_uncertain")));
        });
      }catch{if(!scope.disposed){setBusy(false);fill(output,el("p",{role:"alert",class:"library-notice"},tr("file_error")));}}
    };
    panel.append(el("div",{class:"tool-workbench"},el("p",{class:"caption"},tr("server_files_privacy")),M.input("server_file_choose",picker),el("div",{class:"server-file-actions"},remote("server_file_upload",upload,"files.put","primary"),remote("server_files_refresh",()=>request("files.list",{},data=>{rows=validRows(data);render();}),"files.list")),el("details",{class:"usage-details"},el("summary",{},tr("privacy_and_limits")),el("p",{class:"caption"},tr("server_files_limits")))),output,list);
    return panel;
  };
  M.serverfiles={view};
})();
