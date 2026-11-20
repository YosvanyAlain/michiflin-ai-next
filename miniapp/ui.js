"use strict";
window.Michiflin = (() => {
  const icons = {
    home:'<path d="m3 10 9-7 9 7M5 9v12h14V9M10 21v-7h4v7"/>',
    assistant:'<path d="m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4ZM20 2v4m-2-2h4"/>',
    library:'<path d="M3 4h6a4 4 0 0 1 3 2 4 4 0 0 1 3-2h6v15h-6a4 4 0 0 0-3 2 4 4 0 0 0-3-2H3ZM12 6v15"/>',
    utilities:'<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><path d="M17.5 14v7M14 17.5h7"/>',
    activity:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l4 2"/>',
    settings:'<path d="m9 3-1 3-3 1-2 4 2 2v4l4 3 3-1 3 1 4-3v-4l2-2-2-4-3-1-1-3Z"/><circle cx="12" cy="12" r="3"/>',
    arrow:'<path d="M5 12h14m-5-5 5 5-5 5"/>',
    up:'<path d="M12 20V4m-6 6 6-6 6 6"/>',
    search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
    calculator:'<rect x="5" y="2" width="14" height="20" rx="3"/><path d="M8 7h8M8 12h1m6 0h1m-8 4h1m6 0h1m-8 4h1m6 0h1"/>',
    converter:'<path d="M3 7h17m-4-4 4 4-4 4M21 17H4m4-4-4 4 4 4"/>',
    text_lab:'<path d="M4 5h16M12 5v15M8 20h8"/>',
    password:'<rect x="4" y="10" width="16" height="12" rx="3"/><path d="M8 10V6a4 4 0 0 1 8 0v4m-4 5v3"/>',
    json:'<path d="M8 3H6v7l-3 2 3 2v7h2m8-18h2v7l3 2-3 2v7h-2"/>',
    world_clock:'<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a18 18 0 0 1 0 18 18 18 0 0 1 0-18"/>',
    weather:'<circle cx="8" cy="8" r="4"/><path d="M8 1v1M1 8h1m2-4-1-1M16 8h-1M8 15v1m-4-4-1 1m14-1a5 5 0 0 0-9 3 3 3 0 0 0 0 6h10a4 4 0 0 0-1-9Z"/>',
    youtube:'<rect x="2" y="5" width="20" height="14" rx="4"/><path d="m10 9 5 3-5 3Z"/>',
    reader:'<rect x="4" y="2" width="16" height="20" rx="2"/><path d="M8 7h8M8 11h8M8 15h6M8 19h4"/>',
    notes:'<rect x="4" y="3" width="16" height="19" rx="3"/><path d="M8 2v3m8-3v3M8 10h8m-8 4h8m-8 4h5"/>',
    copy:'<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
    check:'<path d="m4 12 5 5L20 6"/>',
    external:'<path d="M14 3h7v7m0-7L10 14M9 4H4v16h16v-5"/>',
    location:'<path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="3"/>',
    close:'<path d="m6 6 12 12M6 18 18 6"/>',
    shield:'<path d="m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6Zm-5 9 3 3 6-6"/>',
  };
  Object.assign(icons,{finance:icons.calculator,date_math:icons.world_clock,encoding:icons.converter,file_inspector:icons.shield,image_lab:icons.utilities,csv_lab:icons.json,zip_inspector:icons.shield,audio_lab:icons.activity});
  const el = (tag, attrs = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === undefined || value === null || value === false) continue;
      if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else if (["value", "checked", "disabled", "hidden"].includes(key)) node[key] = value;
      else node.setAttribute(key, String(value));
    }
    for (const child of children.flat(Infinity)) if (child !== null && child !== undefined && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    return node;
  };
  const storage = {
    read(key, fallback) { try { const value = localStorage.getItem("michiflin.next." + key); return value === null ? fallback : JSON.parse(value); } catch { return fallback; } },
    write(key, value) { try { localStorage.setItem("michiflin.next." + key, JSON.stringify(value)); return true; } catch { return false; } },
  };
  const preferences = Object.assign({locale:"auto",theme:"system",motion:true},storage.read("preferences",{}));
  const locale = () => preferences.locale === "auto" ? (navigator.language.toLowerCase().startsWith("es") ? "es" : "en") : preferences.locale;
  const tr = (key) => window.MichiflinLocales[locale()]?.[key] || window.MichiflinLocales.en[key] || key.replaceAll("_", " ");
  const icon = (name) => { const holder = document.createElement("span"); holder.innerHTML = '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">' + (icons[name] || icons.assistant) + '</svg>'; return holder.firstChild; };
  const fill = (node, ...children) => { node.replaceChildren(); children.flat(Infinity).forEach((child) => { if (child !== null && child !== false && child !== undefined) node.append(child instanceof Node ? child : document.createTextNode(String(child))); }); return node; };
  const notify = (message) => { const node = el("div", {class:"notification"}, icon("assistant"), message); document.querySelector("#notifications").append(node); setTimeout(()=>node.remove(),6000); };
  const applyPreferences = () => { document.documentElement.dataset.theme=preferences.theme; document.documentElement.dataset.motion=preferences.motion ? "on" : "off"; document.documentElement.lang=locale(); storage.write("preferences",preferences); };
  const button = (key, action, style="", glyph="") => el("button",{type:"button",class:"button "+style,onclick:action},glyph ? icon(glyph):null,tr(key));
  let fieldCounter=0;
  const input = (label, control) => { const id="control-"+(++fieldCounter); control.id=id; return el("label",{class:"field",for:id},el("span",{},tr(label)),control); };
  const select = (options, value="") => el("select",{},...options.map(([key,label])=>el("option",{value:key,selected:key===value ? "selected":null},label)));
  const safeLink = (url) => { try { const parsed=new URL(url); return parsed.protocol==="https:" || parsed.protocol==="http:" ? parsed.href : ""; } catch { return ""; } };
  const date = (value) => new Date(value).toLocaleString(locale(),{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"});
  const copy = async (text) => { try { await navigator.clipboard.writeText(text); notify(tr("copied")); } catch { notify(tr("copy_failed")); } };
  return {el,fill,icon,storage,preferences,locale,tr,notify,applyPreferences,button,input,select,safeLink,date,copy};
})();
