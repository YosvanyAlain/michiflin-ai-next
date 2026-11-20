"use strict";
(() => {
  const M=window.Michiflin,{el,fill,icon,tr,button,input,storage}=M;
  const content=document.querySelector("#content");
  let route="home";
  let latestBooks=storage.read("books",null),latestWeather=storage.read("weather",null);
  let selectedPlace=storage.read("place",null),bookPage=1,lastBookQuery=null;
  const nav=["home","assistant","library","utilities","activity"];
  const utilities=[
    ["calculator","peach",true],["converter","mint",true],["text_lab","",true],
    ["password","mint",true],["json","",true],["world_clock","peach",true],
    ["weather","",false],["notes","peach",true],
  ];
  const pageHeading=(title,body)=>el("header",{class:"page-heading"},el("h1",{},tr(title)),el("p",{},tr(body)));
  const remoteButton=(key,action,style="primary",glyph="arrow")=>{const control=button(key,action,style,glyph);control.dataset.remote="true";control.disabled=!M.transport.ready;return control;};
  const connectionHint=()=>{const node=el("div",{class:"connection-hint","data-connection-hint":"true"},icon("shield"),tr(window.webxdc?"waiting_connection":"open_in_chat"));node.hidden=M.transport.ready;return node;};
  const empty=(title,body,glyph="activity")=>el("div",{class:"empty-state"},icon(glyph),el("h2",{},tr(title)),el("p",{},tr(body)));
  const navigate=(next,args={})=>{route=next;history.replaceState(null,"","#"+next);draw();window.scrollTo(0,0);};
  const tile=([id,tint,offline])=>el("button",{type:"button",class:"tool-card",onclick:()=>navigate(id),"data-tool":id},el("span",{class:"tool-glyph "+tint},icon(id)),el("h3",{},tr(id)),el("p",{},tr(id+"_desc")),el("span",{class:"tool-badge"},tr(offline?"offline":"connected_tool")));
  const status=()=>{
    const ready=M.transport.ready;
    const node=document.querySelector("#connection-status");node.dataset.connected=String(ready);node.textContent=tr(ready?"connected":"not_connected");
    document.querySelectorAll("[data-remote]").forEach(control=>{control.disabled=!ready;});
    document.querySelectorAll("[data-connection-hint]").forEach(control=>{control.hidden=ready;});
    const models=document.querySelector("#assistant-model");
    if(models){const current=models.value;fill(models,...(M.transport.service?.models||[]).map(model=>el("option",{value:model.id},model.name)));models.value=current||models.options[0]?.value||"";if(!models.options.length)models.append(el("option",{value:""},tr("no_models")));}
  };
  const chrome=()=>{
    for(const target of ["#desktop-navigation","#mobile-navigation"]){fill(document.querySelector(target),...nav.map(id=>el("button",{type:"button",class:"navigation-item","data-navigation":id,"aria-label":tr(id),onclick:()=>navigate(id)},icon(id),el("span",{},tr(id)))));}
    document.querySelectorAll("[data-i18n]").forEach(node=>{node.textContent=tr(node.dataset.i18n);});
    document.querySelector(".skip").textContent=tr("skip_content");
    document.querySelector("#desktop-navigation").setAttribute("aria-label",tr("main_navigation"));
    document.querySelector("#mobile-navigation").setAttribute("aria-label",tr("mobile_navigation"));
    document.querySelector("#preferences-close").setAttribute("aria-label",tr("close"));
    fill(document.querySelector("#preferences-button"),icon("settings"));
    document.querySelector("#preferences-button").setAttribute("aria-label",tr("settings"));
  };
  const draw=()=>{
    fill(content);
    const category=nav.includes(route)?route:"utilities";
    document.querySelectorAll("[data-navigation]").forEach(node=>{if(node.dataset.navigation===category)node.setAttribute("aria-current","page");else node.removeAttribute("aria-current");});
    document.querySelector("#page-title").textContent=tr(route);
    document.title=tr(route)+" · Michiflin AI";
    if(route==="home")home();else if(route==="utilities")toolCatalogue();else if(route==="library")library();else if(route==="weather")weather();else if(route==="assistant")assistant();else if(route==="activity")activity();else if(route==="notes")notes();else tool();
    status();
  };
  const home=()=>{
    content.append(el("section",{class:"welcome-heading"},el("span",{class:"eyebrow"},tr("workspace")),el("h1",{},tr("greeting")),el("p",{},tr("greeting_body"))),
      el("section",{class:"feature-grid"},el("article",{class:"feature-hero"},el("span",{class:"eyebrow"},tr("hero_eyebrow")),el("h2",{},tr("hero_title"),el("br"),el("em",{},tr("hero_italic"))),el("p",{},tr("hero_body")),button("start_chat",()=>navigate("assistant"),"light","assistant")),
        el("article",{class:"feature-book"},el("span",{class:"eyebrow"},tr("books_eyebrow")),el("h2",{},tr("books_title")),el("p",{},tr("books_body")),el("div",{class:"book-spines","aria-hidden":"true"},el("span"),el("span"),el("span")),button("explore_books",()=>navigate("library"),"secondary","library"))),
      el("section",{},el("div",{class:"section-title"},el("h2",{},tr("quick_tools")),button("view_all",()=>navigate("utilities"),"text","arrow")),el("div",{class:"card-grid"},...utilities.slice(0,4).map(tile))));
    const recent=M.transport.tasks.slice(-2).reverse();
    const panel=el("section",{class:"paper-panel"},el("div",{class:"section-title"},el("h2",{},tr("recent")),button("activity",()=>navigate("activity"),"text","arrow")));
    panel.append(recent.length?el("div",{class:"activity-stack"},...recent.map(taskRow)):el("div",{class:"gentle-empty"},icon("activity"),el("div",{},el("h3",{},tr("empty_activity")),el("p",{},tr("empty_activity_body")))));
    const weatherPanel=latestWeather?weatherWidget(latestWeather,true):el("section",{class:"paper-panel"},el("h2",{},tr("weather")),el("div",{class:"gentle-empty"},icon("weather"),el("div",{},el("h3",{},tr("weather_empty")),el("p",{},tr("weather_empty_body")))),button("weather",()=>navigate("weather"),"text","arrow"));
    content.append(el("div",{class:"home-lower"},panel,weatherPanel),connectionHint());
  };
  const toolCatalogue=()=>{
    content.append(pageHeading("quick_tools","greeting_body"));
    const search=el("input",{type:"search",placeholder:tr("search"),"aria-label":tr("search")});
    const grid=el("div",{class:"card-grid"});
    const update=()=>fill(grid,...utilities.filter(([id])=>(tr(id)+" "+tr(id+"_desc")).toLocaleLowerCase().includes(search.value.toLocaleLowerCase())).map(tile));
    search.oninput=update;update();content.append(el("div",{class:"utility-search"},search),el("div",{class:"utility-catalog"},grid));
  };
  const tool=()=>{
    if(!utilities.some(([id])=>id===route)){navigate("home");return;}
    content.append(button("utilities",()=>navigate("utilities"),"text back-button","arrow"),pageHeading(route,route+"_desc"),M.offline.view(route));
  };
  const library=()=>{
    content.append(pageHeading("library_title","library_body"));
    const filters=el("form",{class:"library-filters"});
    const title=el("input",{type:"text",maxlength:160,value:storage.read("book.title","")});
    const author=el("input",{type:"text",maxlength:120,value:storage.read("book.author","")});
    const category=M.select([["",tr("all")],...["fiction","science","history","philosophy","poetry","children","biography","technology","adventure","art","education","travel"].map(id=>[id,tr(id)])],storage.read("book.category",""));
    const source=M.select([["gutenberg",tr("gutenberg")],["openlibrary",tr("openlibrary")]],storage.read("book.source","gutenberg"));
    const format=M.select([["",tr("all")],["epub","EPUB"],["txt","TXT"],["html","HTML"],["pdf","PDF"],["kindle","Kindle"]],storage.read("book.format",""));
    const language=M.select([["",tr("all")],["es",tr("language_es")],["en",tr("language_en")],["fr",tr("language_fr")],["pt",tr("language_pt")],["de",tr("language_de")]],storage.read("book.language",""));
    const info=el("p",{class:"caption"});
    const sourceChange=()=>{format.disabled=source.value==="openlibrary";if(format.disabled)format.value="";info.textContent=format.disabled?tr("catalogue_format"):"";};source.onchange=sourceChange;sourceChange();
    const search=page=>{
      bookPage=page;
      const args={title:title.value.trim(),author:author.value.trim(),category:category.value,source:source.value,format:format.value,language:language.value,page};
      for(const key of ["title","author","category","source","format","language"])storage.write("book."+key,args[key]);
      lastBookQuery=args;
      const call=M.transport.call("library.search",args,{label:"library"});
      if(call)showPending(document.querySelector("#library-results"));
    };
    filters.onsubmit=event=>{event.preventDefault();search(1);};
    filters.append(input("book_title",title),input("author",author),input("category",category),input("source",source),input("format",format),input("language",language),info,remoteButton("search_books",()=>search(1),"primary","search"));
    const results=el("section",{id:"library-results","aria-live":"polite"});
    content.append(connectionHint(),el("div",{class:"library-layout"},filters,results));
    renderBooks(results,latestBooks);
  };
  const showPending=node=>{if(node)fill(node,el("div",{class:"loading-line"}),el("p",{class:"caption",role:"status"},tr("pending")));};
  const renderBooks=(node,data)=>{
    if(!node)return;
    if(!data){fill(node,empty("books_title","books_body","library"));return;}
    fill(node,el("div",{class:"library-notice"},tr(data.notice)));
    if(!data.books?.length)node.append(el("div",{class:"empty-state"},icon("library"),el("p",{},tr("no_results"))));
    const grid=el("div",{class:"book-grid"});
    for(const book of data.books||[]){
      const card=el("article",{class:"book-card"},el("div",{class:"book-cover","aria-hidden":"true"},icon("library"),el("span",{},book.title)),el("h3",{},book.title),el("p",{class:"book-author"},(book.authors||[]).join(" · ")),el("span",{class:"access-label"},tr(book.access)));
      const links=el("div",{class:"book-links"});
      for(const download of book.downloads||[]){const url=M.safeLink(download.url);if(url)links.append(el("a",{href:url,target:"_blank",rel:"noopener noreferrer"},String(download.format).toUpperCase()));}
      const source=M.safeLink(book.url);card.append(links);if(source)card.append(el("a",{href:source,target:"_blank",rel:"noopener noreferrer",class:"source-link"},tr("open_source")," · ",book.source,icon("external")));grid.append(card);
    }
    node.append(grid);
    if(data.has_more)node.append(remoteButton("next_page",()=>{
      const args=lastBookQuery||{title:storage.read("book.title",""),author:storage.read("book.author",""),category:storage.read("book.category",""),source:storage.read("book.source","gutenberg"),format:storage.read("book.format",""),language:storage.read("book.language","")};
      bookPage=Number(data.page||1)+1;lastBookQuery={...args,page:bookPage};
      if(M.transport.call("library.search",lastBookQuery,{label:"library"}))showPending(node);
    },"secondary","arrow"));
    status();
  };
  const weather=()=>{
    content.append(pageHeading("weather_title","weather_body"));
    const query=el("input",{type:"search",placeholder:tr("city"),"aria-label":tr("city"),maxlength:100});
    const places=el("section",{id:"weather-places",class:"places-list","aria-live":"polite"});
    const lookup=()=>{if(query.value.trim().length>=2&&M.transport.call("weather.locations",{query:query.value.trim()},{label:"weather"}))showPending(places);};
    const form=el("form",{class:"search-inline",onsubmit:event=>{event.preventDefault();lookup();}},query,remoteButton("search",lookup,"primary","search"));
    const result=el("section",{id:"weather-results"});
    content.append(form,connectionHint(),places,result);
    renderWeather(result,latestWeather);
  };
  const weatherWidget=(data,compact=false)=>{
    const widget=el("article",{class:"weather-widget","data-effect":data.effect,"aria-label":tr(data.effect)});
    const scene=el("div",{class:"weather-scene","aria-hidden":"true"});
    if(data.effect==="clear_day"||data.effect==="partly_cloudy")scene.append(el("span",{class:"weather-sun"}));
    if(data.effect==="clear_night")scene.append(el("span",{class:"weather-moon"}));
    if(["cloudy","partly_cloudy","rain","snow","thunderstorm"].includes(data.effect))scene.append(el("span",{class:"weather-cloud back"}),el("span",{class:"weather-cloud"}));
    if(["rain","thunderstorm","snow"].includes(data.effect))for(let index=0;index<15;index++){
      const item=el("i",{class:data.effect==="snow"?"snowflake":"rain-drop"});item.style.setProperty("--x",(7+index*7)%100+"%");item.style.setProperty("--speed",(data.effect==="snow"?5+index%5:0.8+(index%5)/8)+"s");item.style.setProperty("--delay",(-index*.39)+"s");scene.append(item);
    }
    if(data.effect==="fog")for(let index=0;index<4;index++){const bank=el("span",{class:"fog-bank"});bank.style.setProperty("--x",(index*23+10)+"%");bank.style.setProperty("--delay",(-index*3)+"s");scene.append(bank);}
    if(data.effect==="thunderstorm")scene.append(el("span",{class:"lightning"}));
    const value=data.current?.temperature_2m;
    widget.append(scene,el("header",{class:"weather-location"},el("div",{},el("h2",{},data.place?.name||tr("weather")),el("p",{},data.place?[data.place.region,data.place.country].filter(Boolean).join(" · "):data.timezone)),icon("location")),el("div",{class:"weather-temperature"},typeof value==="number"?Math.round(value)+"°":"—"),el("div",{class:"weather-description"},tr(data.effect)),el("div",{class:"weather-source"},el("span",{},tr("model_data")),el("span",{},tr("updated")+" · "+M.date(data.as_of*1000))));
    if(compact)widget.addEventListener("click",()=>navigate("weather"));
    return widget;
  };
  const renderWeather=(node,data)=>{
    if(!node)return;
    if(!data){fill(node,empty("weather_empty","weather_empty_body","weather"));return;}
    fill(node,weatherWidget(data));
    const current=data.current||{},units=data.current_units||{};
    if(data.stale||Date.now()/1000-data.as_of>5400)node.append(el("p",{class:"library-notice"},tr("stale_weather")));
    const value=(key,suffix)=>typeof current[key]==="number"?Math.round(current[key])+" "+(units[key]||suffix):"—";
    node.append(el("div",{class:"weather-metrics"},...[["feels",value("apparent_temperature","°C")],["humidity",value("relative_humidity_2m","%")],["wind",value("wind_speed_10m","km/h")],["precipitation",value("precipitation","mm")]].map(([key,total])=>el("div",{class:"weather-metric"},el("span",{},tr(key)),el("strong",{},total)))));
    const hourly=data.hourly||{},hours=hourly.time||[];
    const currentTime=String(current.time||"");const first=Math.max(0,hours.findIndex(hour=>hour>=currentTime));
    if(hours.length){const strip=el("div",{class:"hourly-strip",tabindex:"0","aria-label":tr("hourly")});for(let index=first;index<Math.min(first+18,hours.length);index++)strip.append(el("div",{class:"hour-card"},el("span",{},String(hours[index]).slice(11,16)),el("strong",{},typeof hourly.temperature_2m?.[index]==="number"?Math.round(hourly.temperature_2m[index])+"°":"—"),el("span",{},(hourly.precipitation_probability?.[index]??"—")+"%")));node.append(el("h2",{},tr("hourly")),strip);}
    const daily=data.daily||{},week=el("section",{class:"paper-panel"},el("h2",{},tr("seven_days")));
    (daily.time||[]).slice(0,7).forEach((day,index)=>{const label=new Date(day+"T12:00:00").toLocaleDateString(M.locale(),{weekday:"short",day:"numeric"});week.append(el("div",{class:"forecast-row"},el("span",{},label),icon("weather"),el("strong",{},Math.round(daily.temperature_2m_min?.[index])+"° / "+Math.round(daily.temperature_2m_max?.[index])+"°"),el("small",{},(daily.precipitation_probability_max?.[index]??"—")+"%")));});
    if(daily.time?.length)node.append(week);
  };
  const assistant=()=>{
    content.append(pageHeading("assistant_title","assistant_body"));
    const workspace=el("div",{class:"assistant-workbench"});
    const text=el("textarea",{id:"assistant-input",placeholder:tr("ask_placeholder"),maxlength:6000,value:storage.read("assistant.draft","")});text.oninput=()=>storage.write("assistant.draft",text.value);
    const model=M.select([],"");model.id="assistant-model";model.setAttribute("aria-label",tr("assistant"));
    const local=el("input",{type:"checkbox",checked:storage.read("assistant.local",false)});local.onchange=()=>storage.write("assistant.local",local.checked);
    const send=()=>{
      if(!text.value.trim()||!model.value)return;
      if(M.transport.tasks.some(task=>task.op==="assistant.ask"&&task.status==="pending")){M.notify(tr("request_pending"));return;}
      const effective=local.checked?"local":model.value;
      const history=M.transport.tasks.filter(task=>task.op==="assistant.ask"&&task.status==="done"&&task.context?.model===effective&&task.data?.text).slice(-5).flatMap(task=>[{role:"user",content:task.context.input},{role:"assistant",content:task.data.text}]);
      const request=M.transport.call("assistant.ask",{text:text.value.trim(),model:model.value,local_only:local.checked,history},{label:"assistant",context:{model:effective,input:text.value.trim()}});
      if(request){text.value="";storage.write("assistant.draft","");renderConversation();}
    };
    const results=el("section",{id:"assistant-results","aria-live":"polite"});
    const composer=el("form",{class:"assistant-composer",onsubmit:event=>{event.preventDefault();send();}},text,el("div",{class:"composer-actions"},model,remoteButton("send",send,"primary","up")));
    text.onkeydown=event=>{if(event.key==="Enter"&&(event.ctrlKey||event.metaKey)){event.preventDefault();send();}};
    workspace.append(el("div",{class:"assistant-welcome"},el("img",{src:"assets/mark.svg",alt:"",width:60,height:60}),el("h2",{},tr("hero_title")+" "+tr("hero_italic")),el("p",{},tr("assistant_body"))),connectionHint(),results,composer,el("label",{class:"local-only"},local,tr("local_only")),el("p",{class:"caption"},tr("ai_caution")));
    content.append(workspace);renderConversation();
  };
  const renderConversation=()=>{
    const node=document.querySelector("#assistant-results");if(!node)return;
    fill(node,...M.transport.tasks.filter(task=>task.op==="assistant.ask").slice(-15).map(task=>el("article",{class:"paper-panel",style:"margin-bottom:15px"},task.context?.input?el("p",{class:"caption"},task.context.input):null,el("div",{class:"activity-result"},task.status==="pending"?tr("pending"):task.data?.text||tr(task.error||"unknown_error")),task.data?.model?el("p",{class:"caption"},task.data.model):null)));
  };
  const taskRow=task=>el("article",{class:"activity-row"},icon(task.label||"activity"),el("div",{style:"min-width:0;flex:1"},el("h3",{},tr(task.label||task.op)),el("p",{},M.date(task.at)+" · "+tr(task.status==="pending"?"pending":task.status==="done"?"result_ready":"failed")),task.error?el("div",{class:"activity-result"},tr(task.error)):task.data?.text?el("div",{class:"activity-result"},task.data.text):null));
  const activity=()=>{
    content.append(pageHeading("recent","empty_activity_body"));
    if(!M.transport.tasks.length){content.append(empty("empty_activity","empty_activity_body","activity"));return;}
    content.append(el("div",{class:"activity-stack"},...M.transport.tasks.slice().reverse().map(taskRow)));
  };
  const notes=()=>{
    content.append(pageHeading("notes","notes_desc"));
    const saved=storage.read("notes",[]),text=el("textarea",{rows:5,maxlength:6000,placeholder:tr("note_placeholder")});
    const list=el("div",{class:"notes-grid"});
    const redraw=()=>fill(list,...saved.slice().reverse().map(note=>el("article",{class:"note-card"},note.text,button("delete",()=>{const index=saved.findIndex(item=>item.id===note.id);saved.splice(index,1);storage.write("notes",saved);redraw();},"text","close"))));
    const save=()=>{if(!text.value.trim())return;const note={id:Date.now()+"-"+Math.random().toString(36).slice(2),text:text.value.trim()};saved.push(note);if(!storage.write("notes",saved)){saved.pop();M.notify(tr("storage_unavailable"));return;}text.value="";redraw();};
    content.append(el("div",{class:"tool-workbench"},el("p",{class:"caption"},tr("device_only")),input("input",text),button("save",save,"primary","notes")),list);redraw();
  };
  const preferences=()=>{
    const dialog=document.querySelector("#preferences-dialog"),body=document.querySelector("#preferences-content");fill(body);
    for(const [key,options]of[["theme",[["system",tr("system")],["light",tr("light")],["dark",tr("dark")]]],["language",[["auto",tr("system")],["es",tr("language_es")],["en",tr("language_en")]]],["motion",[["on",tr("motion_on")],["off",tr("motion_off")]]]]){
      const value=key==="language"?M.preferences.locale:key==="motion"?(M.preferences.motion?"on":"off"):M.preferences.theme;
      const control=M.select(options,value);control.onchange=()=>{if(key==="language")M.preferences.locale=control.value;else if(key==="motion")M.preferences.motion=control.value==="on";else M.preferences.theme=control.value;M.applyPreferences();chrome();draw();preferences();};body.append(input(key,control));
    }
    body.append(el("p",{class:"caption"},tr("role")+": "+(M.transport.ready?tr(M.transport.service?.role||"member"):"—")),button("reconnect",()=>M.transport.connect(),"secondary","assistant"),connectionHint());
    if(!dialog.open)dialog.showModal();
  };
  document.querySelector("#preferences-button").onclick=preferences;
  document.querySelector("#preferences-close").onclick=()=>document.querySelector("#preferences-dialog").close();
  document.querySelector("#connection-status").onclick=preferences;
  document.addEventListener("visibilitychange",()=>{document.documentElement.dataset.visible=document.visibilityState;});
  document.addEventListener("michiflin:transport",status);
  document.addEventListener("michiflin:result",event=>{
    const task=event.detail;
    if(task.op==="library.search"){
      const node=document.querySelector("#library-results");
      if(task.status==="done"){latestBooks=task.data;storage.write("books",task.data);renderBooks(node,task.data);}else if(node)fill(node,el("p",{class:"library-notice"},tr(task.error)));
    }
    if(task.op==="weather.locations"){
      const node=document.querySelector("#weather-places");
      if(node){fill(node);if(task.status!=="done")node.append(el("p",{class:"caption"},tr(task.error)));else{
        node.append(el("h2",{},tr("choose_place")));
        if(!task.data.places?.length)node.append(el("p",{class:"caption"},tr("no_results")));
        for(const place of task.data.places||[])node.append(el("button",{type:"button",class:"place-option",onclick:()=>{
          selectedPlace=place;storage.write("place",place);
          if(M.transport.call("weather.forecast",{latitude:place.latitude,longitude:place.longitude},{label:"weather",context:{place}})){fill(node);showPending(document.querySelector("#weather-results"));}
        }},icon("location"),el("span",{},el("strong",{},place.name),el("small",{},[place.region,place.country].filter(Boolean).join(" · ")))));
      }}
    }
    if(task.op==="weather.forecast"){
      const node=document.querySelector("#weather-results");
      if(task.status==="done"){latestWeather={...task.data,place:task.context?.place||null};storage.write("weather",latestWeather);renderWeather(node,latestWeather);if(route==="home")draw();}else if(node)fill(node,el("p",{class:"library-notice"},tr(task.error)));
    }
    if(task.op==="assistant.ask")renderConversation();
    if(route==="activity"){fill(content);activity();}
    status();
  });
  window.addEventListener("hashchange",()=>{route=location.hash.slice(1)||"home";draw();});
  M.applyPreferences();chrome();route=location.hash.slice(1)||"home";draw();M.transport.start();
})();
