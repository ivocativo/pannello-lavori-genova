# -*- coding: utf-8 -*-
"""
Raccoglitore con browser automatico.

Serve per i siti che non pubblicano gli annunci nella pagina, ma li caricano
dopo con JavaScript: un browser vero li vede, una richiesta semplice no.

Gira PRIMA di raccogli.py e lascia i risultati in annunci_browser.json, che
raccogli.py legge come una fonte qualsiasi. Se qualcosa va storto scrive un
file vuoto: il pannello si costruisce lo stesso, senza questi datori.

Uso:  python raccogli_browser.py
Richiede: pip install playwright && python -m playwright install chromium
"""
import io
import json
import os
import re
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
QUI = os.path.dirname(os.path.abspath(__file__))
USCITA = os.path.join(QUI, "annunci_browser.json")

# Per ogni sito: nome, indirizzo, e cosa fare quando l'annuncio non dice la citta'.
#   "genova"  = l'azienda ha sede a Genova, un annuncio senza citta' va tenuto
#               e segnalato come da verificare
#   "scarta"  = azienda nazionale, senza una citta' esplicita non si tiene
SITI = [
    # --- grandi datori genovesi ---
    ("Fincantieri",      "https://www.fincantieri.com/it/persone/lavora-con-noi/posizioni-aperte/", "scarta"),
    ("Ansaldo Energia",  "https://www.ansaldoenergia.com/discover-our-job-opportunities", "genova"),
    ("ERG",              "https://www.erg.eu/it/lavorare-in-erg/entra-nella-squadra", "genova"),
    ("Esaote",           "https://www.esaote.com/it-IT/esaote-group/persone/posizioni-aperte/", "genova"),
    ("IIT",              "https://www.iit.it/it/web/guest/opportunities", "genova"),
    ("ETT (gruppo Deda)", "https://www.deda.com/careers", "scarta"),
    ("Aitek",            "https://www.aitek.it/lavora-con-noi/", "genova"),
    ("Engineering",      "https://www.eng.it/careers", "scarta"),
    ("De Wave",          "https://dewavegroup.com/current-jobs/", "genova"),
    ("Piaggio Aerospace", "https://www.piaggioaerospace.it/en/career", "scarta"),
    ("BPER Banca",       "https://www.bper.it/lavora-con-noi", "scarta"),
    # --- grandi gruppi nazionali ---
    ("FS Italiane",      "https://fscareers.gruppofs.it/jobs.php", "scarta"),
    ("Terna",            "https://www.terna.it/it/carriere/lavora-con-noi", "scarta"),
    ("Snam",             "https://www.snam.it/it/carriere/", "scarta"),
    ("Eni",              "https://www.eni.com/it-IT/carriere.html", "scarta"),
    ("TIM",              "https://www.gruppotim.it/it/persone/lavora-con-noi.html", "scarta"),
    ("Intesa Sanpaolo",  "https://group.intesasanpaolo.com/it/careers", "scarta"),
    ("UniCredit",        "https://www.unicreditgroup.eu/it/careers.html", "scarta"),
    ("Generali",         "https://www.generali.com/it/work-with-us/join-us/job-search", "scarta"),
    ("A2A",              "https://www.gruppoa2a.it/it/carriere", "scarta"),
    ("Acea",             "https://www.acea.it/opportunita-carriera", "scarta"),
    ("Autostrade",       "https://www.autostrade.it/it/lavora-con-noi", "scarta"),
]

# Estrazione: si cercano i collegamenti che sembrano annunci, e nel blocco che
# li contiene si cerca una citta'. E' una regola generale, non su misura per
# ogni sito: prende il grosso e ogni tanto lascia qualcosa indietro.
ESTRAI = r"""
() => {
  const RUOLO = /(analyst|analista|manager|specialist|engineer|ingegner|impiegat|addett|tecnic|responsabil|coordinat|consulent|developer|operator|junior|senior|account|buyer|controller|marketing|commercial|assistant|officer|progettista|programmatore|sistemista|architect|designer|planner|supervisor|esperto|neolaureat)/i;
  const SCARTA = /(cookie|privacy|accedi|log ?in|newsletter|iscriviti|informativa|condizioni|mappa del sito|governance|board of|consiglio di amministrazione|assistenza|servizi|soluzioni|prodotti|chi siamo|about us)/i;
  // un collegamento e' un annuncio se punta a una scheda di posizione
  const HREF_LAVORO = /(job|posizion|vacanc|career|carrier|offert|annunc|requisition|dettaglio|opportunit|apply|candidat)/i;
  const out = [];
  const visti = new Set();
  document.querySelectorAll('a[href]').forEach(a => {
    let t = (a.innerText || a.textContent || '').trim().replace(/\s+/g, ' ');
    if (t.length < 6 || t.length > 110) return;
    if (!RUOLO.test(t) || SCARTA.test(t)) return;
    const href = a.getAttribute('href') || '';
    const classi = ((a.className||'') + ' ' + ((a.closest('[class]')||{}).className||'')).toString();
    if (!HREF_LAVORO.test(href) && !HREF_LAVORO.test(classi)) return;
    const chiave = t.toLowerCase();
    if (visti.has(chiave)) return;
    visti.add(chiave);
    // Il blocco giusto e' il PIU' PICCOLO antenato che contiene qualcosa in
    // piu' del titolo: li' dentro c'e' la sede di quella posizione e solo di
    // quella. Salendo troppo si finisce per leggere l'elenco intero e si
    // attribuisce a un annuncio la citta' di un altro.
    let el = a, blocco = t;
    for (let i = 0; i < 5 && el.parentElement; i++) {
      el = el.parentElement;
      const testo = (el.innerText || '').replace(/\s+/g, ' ').trim();
      if (testo.length > t.length + 2 && testo.length < t.length * 2.5 + 90) { blocco = testo; break; }
    }
    out.push({ titolo: t, url: a.href, blocco: blocco.slice(0, 300) });
  });
  return out.slice(0, 150);
}
"""

# Sestri Levante e Riva Trigoso (cantieri Fincantieri) sono in provincia di Genova
# Diversi portali non mostrano gli annunci finche' non si risponde all'avviso
# sui cookie. Si sceglie sempre l'opzione piu' rispettosa: solo i necessari,
# rifiutando gli altri. Non si preme mai "accetta tutti".
RIFIUTI = [
    "Rifiuta", "Rifiuta tutti", "Solo necessari", "Accetta solo i necessari",
    "Solo i cookie necessari", "Continua senza accettare", "Reject", "Reject all",
    "Decline", "Only necessary", "Necessary only", "Use necessary cookies only",
]


def rifiuta_cookie(pagina):
    for etichetta in RIFIUTI:
        try:
            b = pagina.get_by_role("button", name=etichetta, exact=False).first
            if b.is_visible(timeout=1200):
                b.click(timeout=2500)
                pagina.wait_for_timeout(1200)
                return etichetta
        except Exception:
            continue
    return None


GENOVA = re.compile(r"(?i)\b(genova|genoa|liguria|sestri|cornigliano|erzelli|bolzaneto|"
                    r"riva trigoso|chiavari|rapallo|lavagna|arenzano|busalla|"
                    r"campomorone|cogoleto)\b")
ALTRA_CITTA = re.compile(
    r"(?i)\b(milano|roma|torino|napoli|bologna|firenze|venezia|trieste|bari|palermo|"
    r"padova|verona|brescia|bergamo|monfalcone|marghera|castellammare|ancona|"
    r"grottaglie|taranto|pisa|livorno|la spezia|savona|imperia|cagliari|catania|"
    r"parma|modena|perugia|pescara|udine|vicenza|treviso|como|varese|novara|"
    r"alessandria|asti|cuneo|londra|london|madrid|parigi|paris|berlino|stoccolma|"
    r"stockholm|amsterdam|bruxelles|dublino|lisbona|varsavia|praga|bucarest)\b")


def raccogli():
    from playwright.sync_api import sync_playwright

    trovati, esiti = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--disable-dev-shm-usage", "--no-sandbox"])
        contesto = browser.new_context(
            locale="it-IT",
            viewport={"width": 1400, "height": 1000},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"))
        contesto.set_default_timeout(30000)

        for nome, url, senza_citta in SITI:
            t0 = time.time()
            pagina = None
            try:
                pagina = contesto.new_page()
                # le immagini non servono e rallentano parecchio
                pagina.route(re.compile(r"\.(png|jpe?g|gif|webp|svg|woff2?|mp4)$"),
                             lambda r: r.abort())
                pagina.goto(url, wait_until="domcontentloaded", timeout=30000)
                rifiuta_cookie(pagina)
                try:
                    pagina.wait_for_load_state("networkidle", timeout=12000)
                except Exception:
                    pass                      # alcuni siti non stanno mai fermi
                pagina.wait_for_timeout(2500)  # ultimo respiro per le liste tardive
                grezzi = pagina.evaluate(ESTRAI)
            except Exception as e:
                esiti.append({"fonte": nome, "ok": False, "n": 0,
                              "errore": type(e).__name__ + ": " + str(e)[:110],
                              "secondi": round(time.time() - t0, 1)})
                if pagina:
                    try:
                        pagina.close()
                    except Exception:
                        pass
                print("  KO  %-20s %s" % (nome, type(e).__name__))
                continue

            tenuti = []
            for g in grezzi:
                blocco = g.get("blocco", "")
                titolo = g.get("titolo", "")
                # la sede sta in quello che nel blocco avanza dopo il titolo
                sede = blocco.replace(titolo, " ").strip(" -–|·,")
                qui = GENOVA.search(sede) or GENOVA.search(titolo)
                altrove = ALTRA_CITTA.search(sede) or ALTRA_CITTA.search(titolo)
                if not qui:
                    continue        # senza una sede genovese esplicita non si tiene
                if altrove and altrove.start() < (qui.start() if qui else 9999):
                    continue        # nomina prima un'altra citta': non e' di Genova
                luogo_finale = qui.group(0).title()
                tenuti.append({
                    "id": "browser-" + re.sub(r"\W+", "", nome)[:10].lower() + "-"
                          + re.sub(r"\W+", "", g["titolo"])[:34].lower(),
                    "titolo": g["titolo"],
                    "ente": nome,
                    "luogo": luogo_finale,
                    "settore": "privato",
                    "fonte": nome,
                    "url": g.get("url") or url,
                    "pubblicato": None,
                    "scadenza": None,
                    "descrizione": g.get("blocco", ""),
                })
            trovati.extend(tenuti)
            esiti.append({"fonte": nome, "ok": True, "n": len(tenuti),
                          "secondi": round(time.time() - t0, 1)})
            print("  ok  %-20s %3d annunci  (%.0fs, %d candidati grezzi)"
                  % (nome, len(tenuti), time.time() - t0, len(grezzi)))
            try:
                pagina.close()
            except Exception:
                pass
        browser.close()
    return trovati, esiti


def main():
    print("Browser automatico: %d siti da visitare.\n" % len(SITI))
    try:
        trovati, esiti = raccogli()
    except Exception as e:
        print("Il browser non e' partito: %s: %s" % (type(e).__name__, str(e)[:150]))
        trovati, esiti = [], [{"fonte": "browser automatico", "ok": False, "n": 0,
                               "errore": type(e).__name__}]
    json.dump({"annunci": trovati, "esiti": esiti},
              io.open(USCITA, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    ok = sum(1 for e in esiti if e["ok"])
    print("\n  %d siti su %d hanno risposto, %d annunci per Genova."
          % (ok, len(esiti), len(trovati)))
    print("  Salvato in " + USCITA)


if __name__ == "__main__":
    main()
