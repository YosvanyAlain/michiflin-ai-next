"use strict";
(() => {
  const M=window.Michiflin,{el,fill,tr,input,button}=M;
  const fileLimit=20*1024*1024;
  const compound=(principal,monthly,rate,years)=>{
    if(![principal,monthly,rate,years].every(Number.isFinite)||principal<0||monthly<0||principal>1e12||monthly>1e9||rate<0||rate>100||years<1||years>80||!Number.isInteger(years))throw new Error("invalid_finance");
    const periodic=rate/1200,months=years*12;
    const value=periodic===0?principal+monthly*months:principal*(1+periodic)**months+monthly*((1+periodic)**months-1)/periodic;
    return {balance:value,contributions:principal+monthly*months,interest:value-principal-monthly*months};
  };
  const dateDays=(from,to)=>{
    const parse=value=>{if(!/^\d{4}-\d{2}-\d{2}$/.test(value))throw new Error("invalid_date");const date=new Date(value+"T00:00:00Z");if(!Number.isFinite(+date)||date.toISOString().slice(0,10)!==value)throw new Error("invalid_date");return +date;};
    return (parse(to)-parse(from))/86400000;
  };
  const encode=(value,mode)=>{
    if(typeof value!=="string"||value.length>100000)throw new Error("encoding_error");
    try{
      if(mode==="base64_encode")return btoa(Array.from(new TextEncoder().encode(value),byte=>String.fromCharCode(byte)).join(""));
      if(mode==="base64_decode")return new TextDecoder("utf-8",{fatal:true}).decode(Uint8Array.from(atob(value),char=>char.charCodeAt(0)));
      if(mode==="url_encode")return encodeURIComponent(value);
      if(mode==="url_decode")return decodeURIComponent(value);
      throw new Error("encoding_error");
    }catch{throw new Error("encoding_error");}
  };
  const dimensions=buffer=>{
    const bytes=new Uint8Array(buffer),view=new DataView(buffer);
    if(bytes.length>=24&&[137,80,78,71,13,10,26,10].every((value,index)=>bytes[index]===value))return [view.getUint32(16),view.getUint32(20)];
    if(bytes.length>=4&&bytes[0]===255&&bytes[1]===216){
      let offset=2;
      while(offset+4<=bytes.length){
        if(bytes[offset]!==255)throw new Error("invalid_image");
        const marker=bytes[offset+1];
        if(marker===217||marker===218)break;
        if(marker===255){offset++;continue;}
        const size=view.getUint16(offset+2);
        if(size<2||offset+2+size>bytes.length)throw new Error("invalid_image");
        if([192,193,194,195,197,198,199,201,202,203,205,206,207].includes(marker)&&size>=7)return [view.getUint16(offset+7),view.getUint16(offset+5)];
        offset+=2+size;
      }
    }
    throw new Error("invalid_image");
  };
  const validateImage=buffer=>{const [width,height]=dimensions(buffer);if(width<1||height<1||width>16000||height>16000||width*height>16000000)throw new Error("image_limit");return [width,height];};
  const view=id=>{
    const panel=el("section",{class:"tool-workbench"}),output=el("div",{class:"tool-output",role:"status",hidden:true});
    let resource=null,disposed=false;
    document.addEventListener("michiflin:navigate",()=>{disposed=true;if(resource)URL.revokeObjectURL(resource);},{once:true});
    const show=value=>{if(resource){URL.revokeObjectURL(resource);resource=null;}output.hidden=false;fill(output,el("pre",{},value),button("copy",()=>M.copy(String(value)),"text","copy"));};
    const safe=fn=>async()=>{try{await fn();}catch(error){M.notify(tr(error.message in window.MichiflinLocales.en?error.message:"file_error"));}};
    panel.append(el("p",{class:"privacy-note"},tr("local_file_privacy")));
    if(id==="finance"){
      const principal=el("input",{type:"number",min:0,value:1000}),monthly=el("input",{type:"number",min:0,value:50}),rate=el("input",{type:"number",min:0,max:100,value:4,step:"any"}),years=el("input",{type:"number",min:1,max:80,value:10});
      panel.append(el("div",{class:"field-row"},input("starting_balance",principal),input("monthly_deposit",monthly)),el("div",{class:"field-row"},input("annual_rate",rate),input("years",years)),el("p",{class:"caption"},tr("finance_notice")),button("calculate",safe(()=>{
        if([principal,monthly,rate,years].some(node=>!node.value.trim()))throw new Error("invalid_finance");
        const result=compound(Number(principal.value),Number(monthly.value),Number(rate.value),Number(years.value));
        show(Object.entries(result).map(([key,value])=>tr(key)+": "+new Intl.NumberFormat(M.locale(),{maximumFractionDigits:2}).format(value)).join("\n"));
      }),"primary","calculator"));
    }
    if(id==="date_math"){
      const today=new Date().toISOString().slice(0,10),from=el("input",{type:"date",value:today}),to=el("input",{type:"date",value:today});
      panel.append(el("div",{class:"field-row"},input("from",from),input("to",to)),el("p",{class:"caption"},tr("date_notice")),button("calculate",safe(()=>show(dateDays(from.value,to.value)+" "+tr("days"))),"primary","world_clock"));
    }
    if(id==="encoding"){
      const source=el("textarea",{rows:8,maxlength:100000}),mode=M.select(["base64_encode","base64_decode","url_encode","url_decode"].map(value=>[value,tr(value)]),"base64_encode");
      panel.append(input("input",source),input("operation",mode),button("convert",safe(()=>show(encode(source.value,mode.value))),"primary","converter"));
    }
    if(id==="file_inspector"||id==="image_lab"){
      const file=el("input",{type:"file",accept:id==="image_lab"?"image/jpeg,image/png":undefined});
      file.onchange=()=>{output.hidden=true;if(resource){URL.revokeObjectURL(resource);resource=null;}};
      const selected=()=>{const value=file.files?.[0];if(!value)throw new Error("choose_file");if(value.size>fileLimit)throw new Error("file_limit");return value;};
      panel.append(input("choose_file",file));
      if(id==="file_inspector")panel.append(button("inspect_file",safe(async()=>{
        const value=selected(),digest=await crypto.subtle.digest("SHA-256",await value.arrayBuffer());
        show(value.name+"\n"+tr("file_size")+": "+value.size+" B\n"+tr("file_type")+": "+(value.type||tr("unknown"))+"\nSHA-256\n"+Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,"0")).join(""));
      }),"primary","shield"));
      else{
        const edge=el("input",{type:"number",min:64,max:4096,value:1600}),quality=el("input",{type:"number",min:30,max:95,value:80});
        panel.append(el("div",{class:"field-row"},input("max_edge",edge),input("image_quality",quality)),el("p",{class:"caption"},tr("image_notice")),button("optimize_image",safe(async()=>{
          const value=selected(),size=Number(edge.value),compression=Number(quality.value);
          if(!Number.isInteger(size)||size<64||size>4096||!Number.isFinite(compression)||compression<30||compression>95)throw new Error("invalid_image_options");
          validateImage(await value.arrayBuffer());
          const temporary=URL.createObjectURL(value),image=new Image();
          try{
            image.src=temporary;await image.decode();
            if(image.naturalWidth*image.naturalHeight>16000000)throw new Error("image_limit");
            const factor=Math.min(1,size/Math.max(image.naturalWidth,image.naturalHeight)),canvas=document.createElement("canvas");
            canvas.width=Math.max(1,Math.round(image.naturalWidth*factor));canvas.height=Math.max(1,Math.round(image.naturalHeight*factor));
            const context=canvas.getContext("2d");if(!context)throw new Error("invalid_image");context.fillStyle="#fff";context.fillRect(0,0,canvas.width,canvas.height);context.drawImage(image,0,0,canvas.width,canvas.height);
            const blob=await new Promise(resolve=>canvas.toBlob(resolve,"image/jpeg",compression/100));
            if(disposed)return;
            if(!blob)throw new Error("invalid_image");
            if(resource)URL.revokeObjectURL(resource);resource=URL.createObjectURL(blob);output.hidden=false;
            fill(output,el("img",{src:resource,alt:tr("image_preview"),style:"max-width:100%;max-height:260px;object-fit:contain"}),el("p",{},canvas.width+" × "+canvas.height+" · "+blob.size+" B"),el("a",{href:resource,download:"michiflin-image.jpg",class:"button primary"},tr("save_image")));
          }finally{URL.revokeObjectURL(temporary);}
        }),"primary","utilities"));
      }
    }
    panel.append(output);return panel;
  };
  M.extras={view,compound,dateDays,encode,validateImage};
})();
