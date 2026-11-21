"use strict";
(() => {
  const M=window.Michiflin,{el,fill,tr,button}=M;
  const states=new Set(["queued","running","pausing","paused","cancelling","completed","failed","cancelled","expired","uncertain"]);
  const view=()=>{
    const scope=M.files.scope(),panel=el("section",{class:"server-jobs"});
    const output=el("div",{class:"job-output","aria-live":"polite"}),list=el("div",{class:"job-list"});
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
    const valid=row=>{
      if(!row||!/^[a-f0-9]{32}$/.test(row.id)||typeof row.kind!=="string"||row.kind.length>80||!states.has(row.state)||typeof row.worker_stopped!=="boolean"||!Number.isSafeInteger(row.current)||!Number.isSafeInteger(row.total)||row.current<0||row.current>row.total||row.total>1e12||(row.publishing!==undefined&&typeof row.publishing!=="boolean")||(row.error!=null&&(typeof row.error!=="string"||row.error.length>64)))throw new Error("result_invalid");
      return row;
    };
    const request=async(op,args,done)=>{
      if(busy||scope.disposed)return;
      setBusy(true);fill(output,el("p",{class:"caption",role:"status"},tr("pending")));
      try{
        const call=M.transport.call(op,args,{label:"server_jobs",silent:op!=="jobs.control"});
        if(!call){fill(output);return;}
        const data=await call.promise;if(scope.disposed)return;
        fill(output);done(data);
      }catch(error){if(!scope.disposed)fill(output,el("p",{class:"library-notice",role:"alert"},tr(error.message||"unknown_error")));}
      finally{if(!scope.disposed)setBusy(false);}
    };
    const update=row=>{valid(row);rows=rows.map(value=>value.id===row.id?row:value);render();};
    const control=(row,action)=>request("jobs.control",{id:row.id,action},data=>{
      if(data.id!==row.id)throw new Error("result_invalid");
      update(data);fill(output,el("p",{class:"caption",role:"status"},tr("job_control_recorded")));
    });
    const cancel=row=>{
      if(busy)return;
      const dialog=el("dialog",{class:"preferences-dialog job-cancel-dialog"});
      dialog.append(el("h2",{},tr("job_cancel_title")),el("p",{class:"caption"},tr("job_cancel_notice")),button("cancel",()=>dialog.close(),"secondary"),button("job_cancel_confirm",()=>{dialog.close();control(row,"cancel");},"primary"));
      dialog.addEventListener("close",()=>dialog.remove(),{once:true});
      document.addEventListener("michiflin:navigate",()=>dialog.remove(),{once:true});
      document.body.append(dialog);dialog.showModal();
    };
    const render=()=>{
      fill(list);
      if(!rows.length)list.append(el("p",{class:"caption"},tr("server_jobs_empty")));
      for(const row of rows){
        const actions=el("div",{class:"server-file-actions"});
        const publishing=row.publishing&&["running","pausing","cancelling"].includes(row.state);
        if(!row.publishing&&["queued","running"].includes(row.state))actions.append(remote("job_pause",()=>control(row,"pause"),"jobs.control"));
        if(!row.publishing&&row.state==="paused"&&row.worker_stopped)actions.append(remote("job_resume",()=>control(row,"resume"),"jobs.control"));
        if(!row.publishing&&["queued","running","pausing","paused"].includes(row.state))actions.append(remote("job_cancel",()=>cancel(row),"jobs.control","text"));
        actions.append(remote("job_inspect",()=>request("jobs.status",{id:row.id},data=>{
          if(data.id!==row.id)throw new Error("result_invalid");update(data);
        }),"jobs.status","text"));
        const result=row.kind==="files.checksum.v1"&&row.state==="completed"?row.result:null;
        if(result&&(!Number.isSafeInteger(result.bytes)||result.bytes<0||!/^[a-f0-9]{64}$/.test(result.sha256)))throw new Error("result_invalid");
        list.append(el("article",{class:"paper-panel job-row","data-job-id":row.id,"data-job-state":row.state},el("h3",{},row.kind==="files.checksum.v1"?tr("server_file_verify"):row.kind),el("p",{class:"caption"},tr(publishing?"job_state_publishing":"job_state_"+row.state)+" · "+tr(row.worker_stopped?"job_worker_stopped":"job_worker_owned")),el("code",{class:"job-id"},row.id),row.total?el("progress",{max:row.total,value:row.current,"aria-label":tr("job_progress")}):null,row.error?el("p",{class:"caption"},tr(row.error)):null,row.state==="uncertain"?el("p",{class:"caption"},tr("job_uncertain_notice")):null,result?el("details",{open:true},el("summary",{},"SHA-256 · "+result.bytes+" B"),el("code",{},result.sha256)):null,actions));
      }
    };
    panel.append(el("div",{class:"tool-workbench"},el("p",{class:"caption"},tr("server_jobs_notice")),el("div",{class:"server-file-actions"},remote("server_jobs_refresh",()=>request("jobs.list",{},data=>{
      if(!Array.isArray(data?.jobs)||data.jobs.length>50)throw new Error("result_invalid");
      rows=data.jobs.map(valid);render();
    }),"jobs.list"),button("server_files",()=>{location.hash="server_files";},"text"))),output,list);
    return panel;
  };
  M.jobs={view};
})();
