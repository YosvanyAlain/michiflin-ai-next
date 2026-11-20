"use strict";
(() => {
  const M=window.Michiflin,{el,tr,button,input,fill}=M;
  const bounded=value=>{if(!Number.isFinite(value)||Math.abs(value)>1e100)throw new Error("invalid_expression");return value;};
  const calculate=source=>{
    if(typeof source!=="string"||source.length>400)throw new Error("invalid_expression");
    source=source.replaceAll("×","*").replaceAll("÷","/");
    const tokens=[];
    for(let index=0;index<source.length;){
      if(/\s/.test(source[index])){index++;continue;}
      const number=source.slice(index).match(/^(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?/i);
      if(number){tokens.push(Number(number[0]));index+=number[0].length;}
      else if(source.slice(index,index+2)==="**"){tokens.push("**");index+=2;}
      else if("+-*/%()".includes(source[index]))tokens.push(source[index++]);
      else throw new Error("invalid_expression");
      if(tokens.length>64)throw new Error("invalid_expression");
    }
    let position=0,depth=0;
    const atom=()=>{
      if(++depth>24)throw new Error("invalid_expression");
      const token=tokens[position++];let result;
      if(typeof token==="number")result=bounded(token);
      else if(token==="("){result=sum();if(tokens[position++]!==")")throw new Error("invalid_expression");}
      else throw new Error("invalid_expression");
      depth--;return result;
    };
    const power=()=>{const value=atom();if(tokens[position]==="**"){position++;const exponent=unary();if(Math.abs(exponent)>32)throw new Error("invalid_expression");return bounded(value**exponent);}return value;};
    const unary=()=>{if(tokens[position]==="+"||tokens[position]==="-"){const sign=tokens[position++];return bounded((sign==="-"?-1:1)*unary());}return power();};
    const product=()=>{let value=unary();while(["*","/","%"].includes(tokens[position])){const operator=tokens[position++],right=unary();value=bounded(operator==="*"?value*right:operator==="/"?value/right:value%right);}return value;};
    const sum=()=>{let value=product();while(tokens[position]==="+"||tokens[position]==="-"){const operator=tokens[position++],right=product();value=bounded(value+(operator==="+"?right:-right));}return value;};
    const result=sum();if(position!==tokens.length)throw new Error("invalid_expression");return result;
  };
  const units={m:["length",1],km:["length",1000],cm:["length",.01],mi:["length",1609.344],ft:["length",.3048],in:["length",.0254],kg:["mass",1],g:["mass",.001],lb:["mass",.45359237],s:["time",1],min:["time",60],h:["time",3600],B:["storage",1],KB:["storage",1000],MB:["storage",1e6],GB:["storage",1e9],KiB:["storage",1024],MiB:["storage",1048576],GiB:["storage",1073741824]};
  const convert=(value,from,to)=>{
    if(!Number.isFinite(value))throw new Error("incompatible_units");
    if(["C","F","K"].includes(from)&&["C","F","K"].includes(to)){
      const c=from==="C"?value:from==="F"?(value-32)*5/9:value-273.15;
      if(c< -273.15000001)throw new Error("below_absolute_zero");
      return to==="C"?c:to==="F"?c*9/5+32:c+273.15;
    }
    if(!units[from]||!units[to]||units[from][0]!==units[to][0])throw new Error("incompatible_units");
    return bounded(value*units[from][1]/units[to][1]);
  };
  const password=length=>{
    if(!Number.isInteger(length)||length<12||length>72)throw new Error("invalid_length");
    const alphabet="abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%&*-_=+?";
    const ceiling=Math.floor(256/alphabet.length)*alphabet.length;
    for(let attempt=0;attempt<100;attempt++){
      let result="";
      while(result.length<length){for(const byte of crypto.getRandomValues(new Uint8Array(96))){if(byte<ceiling)result+=alphabet[byte%alphabet.length];if(result.length===length)break;}}
      if(/[a-z]/.test(result)&&/[A-Z]/.test(result)&&/[0-9]/.test(result)&&/[^A-Za-z0-9]/.test(result))return result;
    }
    throw new Error("random_generation_failed");
  };
  const view=id=>{
    const panel=el("section",{class:"tool-workbench"});
    const output=el("div",{class:"tool-output",role:"status","aria-live":"polite",hidden:true});
    const show=value=>{output.hidden=false;fill(output,el("pre",{},value),button("copy",()=>M.copy(String(value)),"text","copy"));};
    const safely=fn=>()=>{try{show(fn());}catch(error){M.notify(tr(error.message));}};
    if(id==="calculator"){
      const value=el("input",{placeholder:"17 × 23",inputmode:"text","aria-label":tr("expression")});
      panel.append(input("expression",value),button("calculate",safely(()=>new Intl.NumberFormat(M.locale(),{maximumSignificantDigits:14}).format(calculate(value.value))),"primary","calculator"));
      value.addEventListener("keydown",event=>{if(event.key==="Enter")panel.querySelector("button").click();});
    }
    if(id==="converter"){
      const value=el("input",{type:"number",value:"1",step:"any"});
      const options=Object.keys(units).concat(["C","F","K"]).map(unit=>[unit,unit]);
      const from=M.select(options,"km"),to=M.select(options,"m");
      panel.append(input("value",value),el("div",{class:"field-row"},input("from",from),input("to",to)),button("convert",safely(()=>new Intl.NumberFormat(M.locale(),{maximumSignificantDigits:12}).format(convert(Number(value.value),from.value,to.value))+" "+to.value),"primary","converter"));
    }
    if(id==="password"){
      const length=el("input",{type:"number",min:12,max:72,value:20});
      panel.append(el("p",{class:"privacy-note"},tr("password_privacy")),input("length",length),button("generate",safely(()=>password(Number(length.value))),"primary","password"));
    }
    if(id==="text_lab"){
      const value=el("textarea",{rows:8,maxlength:100000});
      const stats=el("div",{class:"text-statistics"});
      const update=()=>{
        const count=typeof Intl.Segmenter==="function"?Array.from(new Intl.Segmenter(M.locale(),{granularity:"word"}).segment(value.value)).filter(item=>item.isWordLike).length:(value.value.trim().match(/\S+/g)||[]).length;
        fill(stats,...[["words",count],["characters",Array.from(value.value).length],["lines",value.value?value.value.split("\n").length:0]].map(([key,total])=>el("span",{},el("strong",{},total),tr(key))));
      };
      value.oninput=update;update();
      const transform=fn=>()=>{value.value=fn(value.value);update();};
      panel.append(input("input",value),stats,el("div",{class:"button-row"},button("uppercase",transform(text=>text.toLocaleUpperCase(M.locale())),"secondary"),button("lowercase",transform(text=>text.toLocaleLowerCase(M.locale())),"secondary"),button("trim",transform(text=>text.replace(/[\t ]+/g," ").trim()),"secondary")));
    }
    if(id==="json"){
      const value=el("textarea",{rows:10,maxlength:250000,spellcheck:"false"});
      panel.append(input("input",value),button("format_json",()=>{try{show(JSON.stringify(JSON.parse(value.value),null,2));}catch{M.notify(tr("invalid_json"));}},"primary","json"));
    }
    if(id==="world_clock"){
      const zone=el("input",{value:"America/Havana",list:"time-zones"});
      panel.append(input("timezone",zone),el("datalist",{id:"time-zones"},...["America/Havana","America/New_York","Europe/Madrid","Asia/Singapore","Asia/Tokyo","UTC"].map(value=>el("option",{value}))),button("show_time",safely(()=>new Intl.DateTimeFormat(M.locale(),{timeZone:zone.value.trim(),dateStyle:"full",timeStyle:"long"}).format(new Date())),"primary","world_clock"));
    }
    panel.append(output);return panel;
  };
  M.offline={calculate,convert,password,view};
})();
