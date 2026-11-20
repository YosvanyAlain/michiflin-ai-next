"use strict";
(() => {
  const M=window.Michiflin,{el,fill,tr,input,button}=M;
  const csvLimit=1000000;
  const parseCsv=(source,delimiter=",")=>{
    if(typeof source!=="string"||source.length>csvLimit||![",",";","\t"].includes(delimiter))throw new Error("csv_limit");
    source=source.replace(/^\uFEFF/,"");if(!source)return [];
    const rows=[];let row=[],cell="",quoted=false,closed=false;
    const pushCell=()=>{if(cell.length>10000||row.length>=100)throw new Error("csv_limit");row.push(cell);cell="";closed=false;};
    const pushRow=()=>{pushCell();if(rows.length>=2000)throw new Error("csv_limit");rows.push(row);row=[];};
    for(let index=0;index<source.length;index++){
      const char=source[index];
      if(quoted){
        if(char==='"'){if(source[index+1]==='"'){cell+='"';index++;}else{quoted=false;closed=true;}}
        else cell+=char;
      }else if(char===delimiter)pushCell();
      else if(char==="\r"||char==="\n"){pushRow();if(char==="\r"&&source[index+1]==="\n")index++;}
      else if(char==='"'){if(cell||closed)throw new Error("csv_invalid");quoted=true;}
      else{if(closed)throw new Error("csv_invalid");cell+=char;}
      if(cell.length>10000)throw new Error("csv_limit");
    }
    if(quoted)throw new Error("csv_invalid");
    if(cell||row.length||closed||!["\r","\n"].includes(source.at(-1)))pushRow();
    return rows;
  };
  const writeCsv=(rows,delimiter=",",protect=true)=>{
    if(!Array.isArray(rows)||rows.length>2000||![",",";","\t"].includes(delimiter))throw new Error("csv_invalid");
    let size=0;
    const output=rows.map(row=>{
      if(!Array.isArray(row)||row.length<1||row.length>100)throw new Error("csv_invalid");
      return row.map(value=>{
        if(value!==null&&!["string","number","boolean"].includes(typeof value)||typeof value==="number"&&!Number.isFinite(value))throw new Error("csv_invalid");
        let text=value===null?"":String(value);
        if(text.length>10000)throw new Error("csv_limit");
        if(protect&&(/^[\s\u0000-\u001F]*[=+\-@]/.test(text)||/^[\t\r\n]/.test(text)))text="'"+text;
        const escaped=text===""||text.includes(delimiter)||/["\r\n]/.test(text)?'"'+text.replaceAll('"','""')+'"':text;
        size+=escaped.length+1;if(size>csvLimit)throw new Error("csv_limit");return escaped;
      }).join(delimiter);
    }).join("\r\n");
    if(output.length>csvLimit)throw new Error("csv_limit");return output;
  };
  const inspectZip=buffer=>{
    if(!(buffer instanceof ArrayBuffer)||buffer.byteLength<22||buffer.byteLength>M.files.limit)throw new Error("zip_invalid");
    const view=new DataView(buffer),bytes=new Uint8Array(buffer);let end=-1;
    for(let index=bytes.length-22;index>=Math.max(0,bytes.length-65557);index--){
      if(view.getUint32(index,true)===0x06054B50&&index+22+view.getUint16(index+20,true)===bytes.length){end=index;break;}
    }
    if(end<0)throw new Error("zip_invalid");
    const count=view.getUint16(end+10,true),size=view.getUint32(end+12,true),start=view.getUint32(end+16,true);
    if(view.getUint16(end+4,true)||view.getUint16(end+6,true)||view.getUint16(end+8,true)!==count||count===65535||start===0xFFFFFFFF||size===0xFFFFFFFF)throw new Error("zip_unsupported");
    if(count>1000)throw new Error("zip_limit");
    if(start+size!==end)throw new Error("zip_invalid");
    const entries=[],names=new Set(),ranges=[];let cursor=start,total=0;
    for(let index=0;index<count;index++){
      if(cursor+46>end||view.getUint32(cursor,true)!==0x02014B50)throw new Error("zip_invalid");
      const flags=view.getUint16(cursor+8,true),method=view.getUint16(cursor+10,true),compressed=view.getUint32(cursor+20,true),uncompressed=view.getUint32(cursor+24,true);
      const nameSize=view.getUint16(cursor+28,true),extraSize=view.getUint16(cursor+30,true),commentSize=view.getUint16(cursor+32,true),local=view.getUint32(cursor+42,true);
      if(nameSize<1||nameSize>2048||cursor+46+nameSize+extraSize+commentSize>end||local===0xFFFFFFFF||compressed===0xFFFFFFFF||uncompressed===0xFFFFFFFF||view.getUint16(cursor+34,true))throw new Error("zip_unsupported");
      let name;
      try{name=new TextDecoder("utf-8",{fatal:!!(flags&2048)}).decode(bytes.subarray(cursor+46,cursor+46+nameSize));}
      catch{throw new Error("zip_invalid");}
      const warnings=[];
      if(!(flags&2048)&&bytes.subarray(cursor+46,cursor+46+nameSize).some(value=>value>=128))warnings.push("zip_legacy_name");
      const normalized=name.normalize("NFC").replaceAll("\\","/").toLowerCase();
      if(/^[\/\\]|^[a-z]:/i.test(name)||/[\u0000-\u001F\u007F\u202A-\u202E\u2066-\u2069]/.test(name)||name.includes("\\")||normalized.split("/").includes(".."))warnings.push("zip_unsafe_path");
      if(names.has(normalized))warnings.push("zip_duplicate");names.add(normalized);
      if(flags&65)warnings.push("zip_encrypted");
      if((view.getUint32(cursor+38,true)>>>16&0xF000)===0xA000)warnings.push("zip_symlink");
      if(![0,8].includes(method))warnings.push("zip_compression");
      if(uncompressed>100*1024*1024||uncompressed>Math.max(1,compressed)*200)warnings.push("zip_expansion");
      if(local+30>start||view.getUint32(local,true)!==0x04034B50)throw new Error("zip_invalid");
      const localName=view.getUint16(local+26,true),localExtra=view.getUint16(local+28,true),data=local+30+localName+localExtra;
      if(data+compressed>start||localName!==nameSize||view.getUint16(local+6,true)!==flags||view.getUint16(local+8,true)!==method)throw new Error("zip_invalid");
      if(view.getUint32(local+18,true)===0xFFFFFFFF||view.getUint32(local+22,true)===0xFFFFFFFF)throw new Error("zip_unsupported");
      if(!(flags&8)&&(view.getUint32(local+14,true)!==view.getUint32(cursor+16,true)||view.getUint32(local+18,true)!==compressed||view.getUint32(local+22,true)!==uncompressed))throw new Error("zip_invalid");
      if(!bytes.subarray(local+30,local+30+localName).every((value,offset)=>value===bytes[cursor+46+offset]))throw new Error("zip_invalid");
      ranges.push([local,data+compressed]);total+=uncompressed;
      entries.push({name,compressed_bytes:compressed,uncompressed_bytes:uncompressed,warnings});cursor+=46+nameSize+extraSize+commentSize;
    }
    if(cursor!==end)throw new Error("zip_invalid");
    ranges.sort((a,b)=>a[0]-b[0]);if(ranges.some((range,index)=>index&&range[0]<ranges[index-1][1]))throw new Error("zip_invalid");
    return {entries,declared_bytes:total,expansion_warning:total>200*1024*1024,metadata_only:true};
  };
  const readWav=buffer=>{
    if(!(buffer instanceof ArrayBuffer)||buffer.byteLength<44||buffer.byteLength>M.files.limit)throw new Error("wav_invalid");
    const view=new DataView(buffer),text=(offset,size)=>String.fromCharCode(...new Uint8Array(buffer,offset,size));
    if(text(0,4)!=="RIFF"||text(8,4)!=="WAVE"||view.getUint32(4,true)+8!==buffer.byteLength)throw new Error("wav_invalid");
    let format=null,data=null,cursor=12,chunks=0;
    while(cursor+8<=buffer.byteLength){
      if(++chunks>1000)throw new Error("wav_invalid");
      const id=text(cursor,4),size=view.getUint32(cursor+4,true),start=cursor+8;
      if(start+size>buffer.byteLength)throw new Error("wav_invalid");
      if(id==="fmt "){
        if(format||size<16)throw new Error("wav_invalid");
        format={code:view.getUint16(start,true),channels:view.getUint16(start+2,true),rate:view.getUint32(start+4,true),byte_rate:view.getUint32(start+8,true),align:view.getUint16(start+12,true),bits:view.getUint16(start+14,true)};
      }
      if(id==="data"){if(data)throw new Error("wav_invalid");data={offset:start,size};}
      cursor=start+size+(size%2);
    }
    if(cursor!==buffer.byteLength||!format||!data)throw new Error("wav_invalid");
    if(format.code!==1||format.bits!==16||![1,2].includes(format.channels)||format.rate<8000||format.rate>96000)throw new Error("wav_unsupported");
    if(format.align!==format.channels*2||format.byte_rate!==format.rate*format.align||!data.size||data.size%format.align)throw new Error("wav_invalid");
    const frames=data.size/format.align,duration=frames/format.rate;
    if(duration>120)throw new Error("wav_limit");
    return {...format,...data,frames,duration};
  };
  const trimWav=(buffer,from,to,normalize=false,fade=10)=>{
    const info=readWav(buffer);
    if(![from,to,fade].every(Number.isFinite)||from<0||to>info.duration||to<=from||fade<0||fade>1000)throw new Error("wav_range");
    const first=Math.floor(from*info.rate),last=Math.min(info.frames,Math.ceil(to*info.rate)),frames=last-first;
    if(frames<1)throw new Error("wav_range");
    const length=frames*info.align,result=new ArrayBuffer(44+length),out=new DataView(result),source=new DataView(buffer);
    const text=(offset,value)=>{for(let i=0;i<value.length;i++)out.setUint8(offset+i,value.charCodeAt(i));};
    text(0,"RIFF");out.setUint32(4,36+length,true);text(8,"WAVE");text(12,"fmt ");out.setUint32(16,16,true);out.setUint16(20,1,true);out.setUint16(22,info.channels,true);out.setUint32(24,info.rate,true);out.setUint32(28,info.byte_rate,true);out.setUint16(32,info.align,true);out.setUint16(34,16,true);text(36,"data");out.setUint32(40,length,true);
    let peak=0;
    if(normalize)for(let i=0;i<frames*info.channels;i++)peak=Math.max(peak,Math.abs(source.getInt16(info.offset+first*info.align+i*2,true)));
    const gain=normalize&&peak?31128/peak:1,edge=Math.min(Math.floor(frames/2),Math.round(fade*info.rate/1000));
    for(let frame=0;frame<frames;frame++){
      const envelope=edge?Math.min(1,frame/edge,(frames-1-frame)/edge):1;
      for(let channel=0;channel<info.channels;channel++){
        const sample=source.getInt16(info.offset+(first+frame)*info.align+channel*2,true);
        out.setInt16(44+frame*info.align+channel*2,Math.max(-32768,Math.min(32767,Math.round(sample*gain*envelope))),true);
      }
    }
    return result;
  };
  const view=id=>{
    const scope=M.files.scope(),panel=el("section",{class:"tool-workbench"}),output=el("div",{class:"tool-output",role:"status",hidden:true});let busy=false;
    panel.append(el("p",{class:"privacy-note"},tr("local_file_privacy")),el("details",{class:"usage-details"},el("summary",{},tr("privacy_and_limits")),el("p",{class:"caption"},tr("file_export_notice"))));
    const reset=()=>{
      scope.cancel();for(const audio of output.querySelectorAll("audio")){audio.pause();audio.removeAttribute("src");audio.load();}
      fill(output);output.hidden=true;
    };
    document.addEventListener("michiflin:navigate",reset,{once:true});
    const action=(key,run,enabled=()=>true)=>{
      const control=button(key,async()=>{
        if(busy||scope.disposed||!enabled())return;busy=true;control.disabled=true;panel.setAttribute("aria-busy","true");reset();const active=scope.current();
        try{await run(active);}catch(error){if(active())M.notify(tr(error.message in window.MichiflinLocales.en?error.message:"file_error"));}
        finally{busy=false;if(!scope.disposed){control.disabled=!enabled();panel.setAttribute("aria-busy","false");}}
      },"primary","utilities");control.disabled=!enabled();return control;
    };
    const show=(value,blob,name)=>{output.hidden=false;fill(output,el("pre",{},value.slice(0,24000)),value.length>24000?el("p",{class:"caption"},tr("preview_limited")):null,scope.exportControl(blob,name));};
    if(id==="csv_lab"){
      const file=el("input",{type:"file",accept:".csv,.tsv,.txt,.json,text/csv,application/json"}),source=el("textarea",{rows:10,maxlength:csvLimit,spellcheck:"false"}),delimiter=M.select([[",",tr("comma")],[";",tr("semicolon")],["\t",tr("tab")]],","),protect=el("input",{type:"checkbox",checked:true});
      const invalidate=reset;source.oninput=invalidate;delimiter.onchange=invalidate;protect.onchange=invalidate;
      file.onchange=async()=>{
        reset();const active=scope.current();
        try{const value=file.files?.[0];if(!value)return;if(value.size>2000000)throw new Error("csv_limit");const text=new TextDecoder("utf-8",{fatal:true}).decode(await value.arrayBuffer());if(text.length>csvLimit)throw new Error("csv_limit");if(active())source.value=text;}
        catch{if(active())M.notify(tr("csv_invalid"));}
      };
      panel.append(input("choose_file",file),input("input",source),input("delimiter",delimiter),input("csv_protect",protect),el("p",{class:"caption"},tr("csv_notice")),el("div",{class:"button-row"},
        action("csv_to_json",active=>{const rows=parseCsv(source.value,delimiter.value),text=JSON.stringify(rows,null,2);if(active())show(text,new Blob([text],{type:"application/json"}),"michiflin-table.json");}),
        action("json_to_csv",active=>{let rows;try{rows=JSON.parse(source.value);}catch{throw new Error("csv_invalid");}const text=writeCsv(rows,delimiter.value,protect.checked);if(active())show(text,new Blob(["\uFEFF",text],{type:"text/csv;charset=utf-8"}),"michiflin-table.csv");})));
    }
    if(id==="zip_inspector"){
      const file=el("input",{type:"file",accept:".zip,.epub,application/zip,application/epub+zip"});
      const inspect=action("inspect_archive",async active=>{
        const selected=file.files?.[0];if(!selected)throw new Error("choose_file");if(selected.size>M.files.limit)throw new Error("file_limit");
        const report=inspectZip(await selected.arrayBuffer());if(!active())return;
        const warnings=report.entries.filter(entry=>entry.warnings.length).length;
        const summary=tr("zip_entries")+": "+report.entries.length+"\n"+tr("zip_declared")+": "+report.declared_bytes+" B\n"+tr("zip_flags")+": "+warnings+(report.expansion_warning?"\n"+tr("zip_expansion"):"");
        const lines=report.entries.slice(0,150).map(entry=>entry.name+" · "+entry.uncompressed_bytes+" B"+(entry.warnings.length?"\n  "+entry.warnings.map(tr).join("; "):""));
        show(summary+"\n\n"+lines.join("\n")+(report.entries.length>150?"\n"+tr("preview_limited"):""),new Blob([JSON.stringify(report,null,2)],{type:"application/json"}),"michiflin-archive.json");
      },()=>!!file.files?.length);
      file.onchange=()=>{reset();inspect.disabled=busy||!file.files?.length;};
      panel.append(input("choose_file",file),el("p",{class:"caption"},tr("zip_notice")),inspect);
    }
    if(id==="audio_lab"){
      const file=el("input",{type:"file",accept:".wav,audio/wav,audio/x-wav"}),from=el("input",{type:"number",min:0,step:"0.001",value:0}),to=el("input",{type:"number",min:0,step:"0.001",value:1}),fade=el("input",{type:"number",min:0,max:1000,value:10}),normalize=el("input",{type:"checkbox"}),details=el("p",{class:"caption",role:"status"});
      const invalidate=reset;for(const control of [from,to,fade,normalize])control.addEventListener("change",invalidate);
      file.onchange=async()=>{
        invalidate();details.textContent="";const active=scope.current();
        try{const value=file.files?.[0];if(!value)return;if(value.size>M.files.limit)throw new Error("file_limit");const info=readWav(await value.arrayBuffer());if(active()){to.value=String(info.duration);details.textContent=info.duration.toFixed(3)+" s · "+info.rate+" Hz · "+info.channels+" "+tr("audio_channels");}}
        catch(error){if(active())M.notify(tr(error.message in window.MichiflinLocales.en?error.message:"wav_invalid"));}
      };
      panel.append(input("choose_file",file),el("p",{class:"caption"},tr("wav_notice")),details,el("div",{class:"field-row"},input("audio_start",from),input("audio_end",to)),input("audio_fade",fade),input("audio_normalize",normalize),action("trim_audio",async active=>{
        const selected=file.files?.[0];if(!selected)throw new Error("choose_file");if(selected.size>M.files.limit)throw new Error("file_limit");
        if([from,to,fade].some(control=>!control.value.trim()))throw new Error("wav_range");
        const options=[Number(from.value),Number(to.value),normalize.checked,Number(fade.value)],buffer=await selected.arrayBuffer();if(!active())return;
        const clipped=trimWav(buffer,...options),blob=new Blob([clipped],{type:"audio/wav"}),info=readWav(clipped);output.hidden=false;
        fill(output,el("audio",{src:scope.url(blob),controls:true,preload:"metadata",style:"max-width:100%;width:100%"}),el("p",{},info.duration.toFixed(3)+" s · "+blob.size+" B"),scope.exportControl(blob,"michiflin-audio.wav"));
      }));
    }
    panel.append(output);return panel;
  };
  M.filetools={parseCsv,writeCsv,inspectZip,readWav,trimWav,view};
})();
