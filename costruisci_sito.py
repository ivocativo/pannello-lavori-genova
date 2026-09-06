# -*- coding: utf-8 -*-
"""Genera il sito del pannello a partire da dati.json, in un unico file HTML."""
import io
import json
import os
import re

QUI = os.path.dirname(os.path.abspath(__file__))
DATI = json.load(io.open(os.path.join(QUI, "dati.json"), encoding="utf-8"))

# la soglia e il campo "marginale" li decide raccogli.py, cosi' i numeri
# mostrati in cima al pannello e quelli stampati a terminale coincidono
SOGLIA = 25

HTML = """<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<!-- Senza questa riga i telefoni impaginano a 980 pixel e rimpiccioliscono
     tutto: il pannello si consulta soprattutto da telefono, quindi serve. -->
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta name="color-scheme" content="light dark">
<title>Lavoro Genova</title>
<style>
  html{-webkit-text-size-adjust:100%}
  [hidden]{display:none !important}
  img{max-width:100%}
  :root{
    --sfondo:#f6f7f9; --carta:#ffffff; --bordo:#e2e6eb; --testo:#1c2128;
    --tenue:#5c6773; --blu:#1f3864; --blu-chiaro:#eaeff7;
    --verde:#1f7a45; --verde-chiaro:#e8f5ee; --ambra:#9a6700; --ambra-chiaro:#fff6e0;
    --rosso:#b42318; --rosso-chiaro:#fdeceb; --ombra:0 1px 2px rgba(16,24,40,.06);
  }
  @media (prefers-color-scheme: dark){ :root:not([data-theme="light"]){
    --sfondo:#12151a; --carta:#1a1f26; --bordo:#2b323c; --testo:#e6eaf0;
    --tenue:#9aa5b1; --blu:#8ab4f8; --blu-chiaro:#1c2836;
    --verde:#6ee7a0; --verde-chiaro:#14291d; --ambra:#f0c060; --ambra-chiaro:#2d2410;
    --rosso:#ff8a80; --rosso-chiaro:#33191a; --ombra:none;
  }}
  :root[data-theme="dark"]{
    --sfondo:#12151a; --carta:#1a1f26; --bordo:#2b323c; --testo:#e6eaf0;
    --tenue:#9aa5b1; --blu:#8ab4f8; --blu-chiaro:#1c2836;
    --verde:#6ee7a0; --verde-chiaro:#14291d; --ambra:#f0c060; --ambra-chiaro:#2d2410;
    --rosso:#ff8a80; --rosso-chiaro:#33191a; --ombra:none;
  }
  *{box-sizing:border-box}
  body{background:var(--sfondo); color:var(--testo);
       font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
       margin:0; padding:0 16px 64px}
  .contenitore{max-width:900px; margin:0 auto}
  header{padding:28px 0 16px}
  h1{font-size:26px; margin:0 0 4px; letter-spacing:-.3px}
  .sottotitolo{color:var(--tenue); font-size:13.5px; margin:0}
  .riepilogo{display:flex; flex-wrap:wrap; gap:8px; margin:18px 0 6px}
  .dato{background:var(--carta); border:1px solid var(--bordo); border-radius:10px;
        padding:9px 13px; box-shadow:var(--ombra); flex:1 1 auto; min-width:118px}
  .dato .n{font-size:21px; font-weight:700; line-height:1.1}
  .dato .e{font-size:11.5px; color:var(--tenue); text-transform:uppercase; letter-spacing:.4px}
  .dato.allerta .n{color:var(--rosso)}
  .dato.novita .n{color:var(--verde)}

  .avviso{background:var(--ambra-chiaro); color:var(--ambra); border:1px solid transparent;
          border-radius:10px; padding:10px 14px; font-size:13px; margin:16px 0 0}
  .barra{position:sticky; top:0; z-index:20; background:var(--sfondo);
         padding:12px 0 10px; border-bottom:1px solid var(--bordo); margin-bottom:14px}
  .filtri{display:flex; flex-wrap:wrap; gap:6px; align-items:center}
  button.f{background:var(--carta); border:1px solid var(--bordo); color:var(--testo);
           border-radius:999px; padding:6px 13px; font-size:13px; cursor:pointer;
           font-family:inherit}
  button.f:hover{border-color:var(--blu)}
  button.f[aria-pressed="true"]{background:var(--blu); border-color:var(--blu); color:#fff}
  :root[data-theme="dark"] button.f[aria-pressed="true"],
  @media (prefers-color-scheme: dark){button.f[aria-pressed="true"]{color:#0e1116}}
  input[type=search]{flex:1 1 190px; min-width:150px; padding:7px 12px; font-size:13.5px;
    border:1px solid var(--bordo); border-radius:999px; background:var(--carta);
    color:var(--testo); font-family:inherit}
  select{padding:7px 10px; border:1px solid var(--bordo); border-radius:999px;
    background:var(--carta); color:var(--testo); font-size:13px; font-family:inherit}

  h2.sezione{font-size:16px; margin:26px 0 10px; display:flex; align-items:center; gap:9px}
  h2.sezione .conta{font-size:12.5px; color:var(--tenue); font-weight:400}

  .scheda{background:var(--carta); border:1px solid var(--bordo); border-radius:12px;
          padding:14px 16px; margin-bottom:10px; box-shadow:var(--ombra); position:relative}
  .scheda.scartato{opacity:.45}
  .scheda.inviato{border-color:var(--verde)}
  .testa{display:flex; gap:12px; align-items:flex-start}
  .punteggio{flex:0 0 44px; height:44px; border-radius:10px; display:flex;
             align-items:center; justify-content:center; font-weight:700; font-size:16px;
             background:var(--blu-chiaro); color:var(--blu)}
  .punteggio.alto{background:var(--verde-chiaro); color:var(--verde)}
  .punteggio.basso{background:var(--sfondo); color:var(--tenue)}
  .titolo{font-weight:600; font-size:15.5px; margin:0 0 3px; line-height:1.3}
  .titolo a{color:inherit; text-decoration:none}
  .titolo a:hover{text-decoration:underline}
  .meta{color:var(--tenue); font-size:12.5px}
  .tag{display:inline-block; font-size:11px; padding:2px 8px; border-radius:999px;
       border:1px solid var(--bordo); margin:5px 5px 0 0; color:var(--tenue)}
  .tag.nuovo{background:var(--verde-chiaro); color:var(--verde); border-color:transparent; font-weight:600}
  .tag.urgente{background:var(--rosso-chiaro); color:var(--rosso); border-color:transparent; font-weight:600}
  .tag.presto{background:var(--ambra-chiaro); color:var(--ambra); border-color:transparent; font-weight:600}
  .tag.remoto{background:var(--blu-chiaro); color:var(--blu); border-color:transparent}
  .perche{font-size:12.5px; color:var(--tenue); margin-top:8px; line-height:1.45}
  .perche b{color:var(--verde); font-weight:600}
  .perche i{color:var(--ambra); font-style:normal; font-weight:600}
  .azioni{display:flex; gap:6px; margin-top:11px; flex-wrap:wrap}
  button.a{background:transparent; border:1px solid var(--bordo); color:var(--tenue);
           border-radius:8px; padding:5px 11px; font-size:12.5px; cursor:pointer; font-family:inherit}
  button.a:hover{border-color:var(--blu); color:var(--blu)}
  button.a[aria-pressed="true"]{background:var(--blu); border-color:var(--blu); color:#fff}
  a.vai{margin-left:auto; font-size:12.5px; color:var(--blu); text-decoration:none;
        align-self:center; font-weight:500}
  a.vai:hover{text-decoration:underline}

  details.gruppo{margin-top:22px; border:1px solid var(--bordo); border-radius:12px;
                 background:var(--carta); padding:0 14px}
  details.gruppo>summary{cursor:pointer; padding:13px 0; font-weight:600; font-size:14.5px}
  details.gruppo[open]{padding-bottom:12px}
  .vuoto{color:var(--tenue); font-size:13.5px; padding:22px; text-align:center;
         border:1px dashed var(--bordo); border-radius:12px}
  footer{margin-top:34px; padding-top:16px; border-top:1px solid var(--bordo);
         color:var(--tenue); font-size:12px; line-height:1.6}
  footer code{background:var(--sfondo); padding:1px 5px; border-radius:4px; font-size:11px}
  @media(max-width:560px){
    h1{font-size:22px} .dato{min-width:88px; padding:8px 10px} .dato .n{font-size:19px}
    .punteggio{flex-basis:38px; height:38px; font-size:14px}
    body{padding:0 12px 56px}
    /* su schermo stretto i comandi devono andare a capo, non uscire di lato */
    .filtri{gap:5px}
    .filtri input[type=search]{flex:1 1 100%; min-width:0}
    .filtri select{flex:1 1 46%; min-width:0; max-width:100%}
    .filtri button.f{flex:0 1 auto; font-size:12.5px; padding:6px 11px}
    .azioni button.a{flex:1 1 auto; text-align:center}
    a.vai{margin-left:0; width:100%; padding-top:4px}
  }
</style>
</head>
<body>

<div class="contenitore">
<header>
  <h1>Offerte di lavoro &middot; Genova</h1>
  <p class="sottotitolo">Aggiornato il __AGGIORNATO__ &middot; si aggiorna da solo il luned&igrave; e il gioved&igrave;</p>
</header>

__AVVISO_FONTI__
<div class="riepilogo">
  <div class="dato"><div class="n">__N_PRINCIPALI__</div><div class="e">annunci</div></div>
  <div class="dato novita"><div class="n">__NUOVI__</div><div class="e">nuovi</div></div>
  <div class="dato"><div class="n">__N_PRIVATI__</div><div class="e">privati</div></div>
  <div class="dato"><div class="n">__N_PUBBLICI__</div><div class="e">concorsi</div></div>
  <div class="dato allerta"><div class="n">__N_SCADENZA__</div><div class="e">in scadenza</div></div>
</div>

<div class="barra">
  <div class="filtri">
    <input type="search" id="cerca" placeholder="Cerca parola, azienda, ruolo&hellip;" aria-label="Cerca">
    <button class="f" id="f-nuovi" aria-pressed="false">Solo nuovi</button>
    <button class="f" id="f-remoto" aria-pressed="false">Da remoto</button>
    <button class="f" id="f-interessa" aria-pressed="false">Mi interessa</button>
    <select id="ordina" aria-label="Ordina">
      <option value="punteggio">Ordina per compatibilit&agrave;</option>
      <option value="data">Ordina per data</option>
      <option value="scadenza">Ordina per scadenza</option>
    </select>
    <select id="soglia" aria-label="Punteggio minimo">
      <option value="0">Qualsiasi punteggio</option>
      <option value="40">Da 40 in su</option>
      <option value="55">Da 55 in su</option>
      <option value="70">Da 70 in su</option>
    </select>
    <button class="f" id="f-scartati" aria-pressed="false">Mostra scartati</button>
  </div>
</div>

<h2 class="sezione">&#127970; Offerte private <span class="conta" id="conta-privati"></span></h2>
<div id="privati"></div>

<h2 class="sezione">&#127963; Bandi e concorsi pubblici <span class="conta" id="conta-pubblici"></span></h2>
<div id="pubblici"></div>

<details class="gruppo" id="box-determinati">
  <summary>Contratti a tempo determinato e in somministrazione (<span id="conta-determinati">0</span>)</summary>
  <div id="determinati"></div>
</details>

<details class="gruppo" id="box-marginali">
  <summary>Annunci meno pertinenti (<span id="conta-marginali">0</span>)</summary>
  <p class="sottotitolo" style="margin:0 0 10px">Compaiono qui gli annunci con punteggio sotto __SOGLIA__: raramente utili, ma restano consultabili.</p>
  <div id="marginali"></div>
</details>

<footer>
  <p><b>Come funziona il punteggio.</b> Da 0 a 100, calcolato sul curriculum caricato: conta il ruolo indicato nel titolo,
  le competenze che ha davvero (JIRA, Magento, analisi dei requisiti, UAT, e-commerce, Google Analytics, lingue),
  il tipo di contratto e la possibilit&agrave; di lavorare da remoto. Scendono le offerte che chiedono molti anni di
  esperienza o una laurea tecnica.</p>
  <p><b>Cosa viene escluso in automatico:</b> stage e tirocini, lavoro su turni, ruoli di controllo qualit&agrave; del software
  e di assistenza tecnica. __ESCLUSI__ annunci sono stati scartati in questo aggiornamento.</p>
  <p><b>Fonti:</b> __FONTI__.</p>
  <p>I pulsanti <i>mi interessa</i>, <i>scartato</i> e <i>candidatura inviata</i> restano su questo dispositivo e non sono visibili a nessun altro.</p>
</footer>
</div>

<script>
const DATI = __DATI__;
const SOGLIA_MARGINALE = __SOGLIA__;
const stato = caricaStato();

function caricaStato(){
  try { return JSON.parse(localStorage.getItem("lavoro-genova-stato") || "{}"); }
  catch(e){ return {}; }
}
function salvaStato(){
  try { localStorage.setItem("lavoro-genova-stato", JSON.stringify(stato)); }
  catch(e){ /* navigazione privata: pazienza */ }
}

function giorniA(iso){
  if(!iso) return null;
  const d = new Date(iso + "T23:59:59Z");
  if(isNaN(d)) return null;
  return Math.round((d - new Date()) / 86400000);
}
function fmt(iso){
  if(!iso) return null;
  const p = iso.split("-");
  return p.length === 3 ? p[2] + "/" + p[1] + "/" + p[0] : iso;
}
function esc(s){
  return String(s == null ? "" : s).replace(/[&<>"]/g, c =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
}

function scheda(a){
  const st = stato[a.id] || "";
  const gg = giorniA(a.scadenza);
  const d = a.dettaglio || {};
  let cls = "punteggio";
  if(a.punteggio >= 65) cls += " alto";
  else if(a.punteggio < 35) cls += " basso";

  let tag = "";
  if(a.nuovo) tag += '<span class="tag nuovo">NUOVO</span>';
  if(gg !== null && gg <= 3) tag += '<span class="tag urgente">scade fra ' + gg + ' giorni</span>';
  else if(gg !== null && gg <= 12) tag += '<span class="tag presto">scade fra ' + gg + ' giorni</span>';
  else if(a.scadenza) tag += '<span class="tag">scade il ' + fmt(a.scadenza) + '</span>';
  if(d.remoto) tag += '<span class="tag remoto">remoto o ibrido</span>';
  if(a.contratto && a.contratto !== "non indicato") tag += '<span class="tag">' + esc(a.contratto) + '</span>';
  if(a.posti && a.posti > 1) tag += '<span class="tag">' + a.posti + ' posti</span>';
  tag += '<span class="tag">' + esc(a.fonte) + '</span>';

  let perche = "";
  if((d.pro || []).length) perche += "<b>corrisponde per:</b> " + esc(d.pro.join(" \\u00b7 "));
  if((d.contro || []).length) perche += (perche ? "<br>" : "") + "<i>attenzione:</i> " + esc(d.contro.join(" \\u00b7 "));

  return '<article class="scheda ' + (st === "scartato" ? "scartato" : st === "inviato" ? "inviato" : "") + '" data-id="' + esc(a.id) + '">' +
    '<div class="testa">' +
      '<div class="' + cls + '">' + a.punteggio + '</div>' +
      '<div style="flex:1;min-width:0">' +
        '<p class="titolo"><a href="' + esc(a.url) + '" target="_blank" rel="noopener">' + esc(a.titolo) + '</a></p>' +
        '<p class="meta">' + esc(a.ente) + (a.luogo ? " &middot; " + esc(a.luogo) : "") +
          (a.pubblicato ? " &middot; pubblicato il " + fmt(a.pubblicato) : "") + '</p>' +
        (a.titolo_ufficiale ? '<p class="meta" style="margin-top:3px;font-size:11.5px;opacity:.85">' + esc(a.titolo_ufficiale) + '</p>' : "") +
        '<div>' + tag + '</div>' +
        (perche ? '<p class="perche">' + perche + '</p>' : "") +
        '<div class="azioni">' +
          '<button class="a" data-az="interessa" aria-pressed="' + (st === "interessa") + '">Mi interessa</button>' +
          '<button class="a" data-az="inviato" aria-pressed="' + (st === "inviato") + '">Candidatura inviata</button>' +
          '<button class="a" data-az="scartato" aria-pressed="' + (st === "scartato") + '">Scarta</button>' +
          '<a class="vai" href="' + esc(a.url) + '" target="_blank" rel="noopener">Vai all\\u2019annuncio &rarr;</a>' +
        '</div>' +
      '</div>' +
    '</div></article>';
}

function disegna(){
  const q = document.getElementById("cerca").value.trim().toLowerCase();
  const soloNuovi = document.getElementById("f-nuovi").getAttribute("aria-pressed") === "true";
  const soloRemoto = document.getElementById("f-remoto").getAttribute("aria-pressed") === "true";
  const soloInteressa = document.getElementById("f-interessa").getAttribute("aria-pressed") === "true";
  const mostraScartati = document.getElementById("f-scartati").getAttribute("aria-pressed") === "true";
  const ordine = document.getElementById("ordina").value;
  const minimo = parseInt(document.getElementById("soglia").value, 10);

  let lista = DATI.annunci.filter(a => {
    const st = stato[a.id] || "";
    if(st === "scartato" && !mostraScartati) return false;
    if(soloNuovi && !a.nuovo) return false;
    if(soloRemoto && !(a.dettaglio || {}).remoto) return false;
    if(soloInteressa && st !== "interessa") return false;
    if(a.punteggio < minimo) return false;
    if(q){
      const testo = (a.titolo + " " + a.ente + " " + a.luogo + " " + (a.descrizione || "")).toLowerCase();
      if(testo.indexOf(q) === -1) return false;
    }
    return true;
  });

  lista.sort((x, y) => {
    if(ordine === "data") return (y.pubblicato || "") .localeCompare(x.pubblicato || "");
    if(ordine === "scadenza"){
      const a1 = x.scadenza || "9999", b1 = y.scadenza || "9999";
      return a1.localeCompare(b1);
    }
    return y.punteggio - x.punteggio;
  });

  const privati = lista.filter(a => a.settore === "privato" && !a.secondario && !a.marginale);
  const pubblici = lista.filter(a => a.settore === "pubblico" && !a.marginale);
  const determinati = lista.filter(a => a.secondario && !a.marginale);
  const marginali = lista.filter(a => a.marginale);

  const scrivi = (id, arr, vuoto) => {
    document.getElementById(id).innerHTML =
      arr.length ? arr.map(scheda).join("") : '<p class="vuoto">' + vuoto + '</p>';
  };
  scrivi("privati", privati, "Nessuna offerta privata con questi filtri.");
  scrivi("pubblici", pubblici, "Nessun concorso aperto con questi filtri.");
  scrivi("determinati", determinati, "Nessuno.");
  scrivi("marginali", marginali, "Nessuno.");
  document.getElementById("conta-privati").textContent = privati.length + (privati.length === 1 ? " offerta" : " offerte");
  document.getElementById("conta-pubblici").textContent = pubblici.length + (pubblici.length === 1 ? " bando" : " bandi");
  document.getElementById("conta-determinati").textContent = determinati.length;
  document.getElementById("conta-marginali").textContent = marginali.length;
  document.getElementById("box-determinati").hidden = determinati.length === 0;
  document.getElementById("box-marginali").hidden = marginali.length === 0;
}

document.addEventListener("click", e => {
  const b = e.target.closest("button.a");
  if(b){
    const scheda = b.closest(".scheda");
    const id = scheda.getAttribute("data-id");
    const az = b.getAttribute("data-az");
    stato[id] = (stato[id] === az) ? "" : az;
    if(!stato[id]) delete stato[id];
    salvaStato();
    disegna();
    return;
  }
  const f = e.target.closest("button.f");
  if(f){
    f.setAttribute("aria-pressed", f.getAttribute("aria-pressed") === "true" ? "false" : "true");
    disegna();
  }
});
document.getElementById("cerca").addEventListener("input", disegna);
document.getElementById("ordina").addEventListener("change", disegna);
document.getElementById("soglia").addEventListener("change", disegna);
disegna();
</script>
</body>
</html>
"""


def genera():
    fonti_ok = [f["fonte"] for f in DATI["fonti"] if f["ok"]]
    fonti_ko = [f["fonte"] for f in DATI["fonti"] if not f["ok"]]
    avviso = ""
    if fonti_ko:
        avviso = ('<p class="avviso">In questo aggiornamento non ha risposto: '
                  + ", ".join(fonti_ko)
                  + ". L&rsquo;elenco potrebbe essere incompleto: al prossimo"
                    " aggiornamento gli annunci mancanti ricompaiono.</p>")
    html = (HTML
            .replace("__DATI__", json.dumps(DATI, ensure_ascii=False))
            .replace("__AGGIORNATO__", DATI["aggiornato_it"])
            .replace("__N_PRINCIPALI__", str(DATI["n_principali"]))
            .replace("__NUOVI__", str(DATI["nuovi"]))
            .replace("__N_PRIVATI__", str(DATI["n_privati"]))
            .replace("__N_PUBBLICI__", str(DATI["n_pubblici"]))
            .replace("__N_SCADENZA__", str(DATI["n_scadenza"]))
            .replace("__ESCLUSI__", str(DATI["esclusi"]))
            .replace("__SOGLIA__", str(SOGLIA))
            .replace("__FONTI__", ", ".join(fonti_ok))
            .replace("__AVVISO_FONTI__", avviso))
    cartella = os.path.join(QUI, "sito")
    os.makedirs(cartella, exist_ok=True)
    # Solo il contenuto di sito/ viene pubblicato online: i CV e le credenziali
    # restano fuori dalla cartella e quindi fuori da internet.
    percorso = os.path.join(cartella, "index.html")
    io.open(percorso, "w", encoding="utf-8").write(html)
    print("Scritto %s (%.0f KB)" % (percorso, len(html) / 1024))
    print("  %d annunci principali, %d marginali, %d concorsi"
          % (DATI["n_principali"], DATI["n_marginali"], DATI["n_pubblici"]))
    return percorso


if __name__ == "__main__":
    genera()
