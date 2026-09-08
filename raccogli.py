# -*- coding: utf-8 -*-
"""
Pannello lavoro Genova - raccoglitore.

Interroga tutte le fonti verificate, normalizza gli annunci, assegna un punteggio
di compatibilita' col profilo, e salva i dati per il sito.

Uso:  python raccogli.py
"""
import io
import json
import os
import re
import ssl
import sys
import time
import html
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

QUI = os.path.dirname(os.path.abspath(__file__))
CONFIG = json.load(io.open(os.path.join(QUI, "config.json"), encoding="utf-8"))
PROFILO = CONFIG["profilo_candidata"]
ADZ = dict(CONFIG["adzuna"])

# Sul computer le chiavi stanno in config.json; su GitHub quel file non c'e'
# (contiene credenziali) e arrivano dai "secrets" come variabili d'ambiente.
ADZ["app_id"] = os.environ.get("ADZUNA_APP_ID") or ADZ.get("app_id")
ADZ["app_key"] = os.environ.get("ADZUNA_APP_KEY") or ADZ.get("app_key")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

OGGI = datetime.now(timezone.utc)

# sotto questo punteggio un annuncio e' considerato poco pertinente
SOGLIA_MARGINALE = 25


# ---------------------------------------------------------------- utilita' ---

def http(url, data=None, headers=None, timeout=90):
    h = {"User-Agent": UA, "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
         "Accept-Language": "it-IT,it;q=0.9,en;q=0.8"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    r = urllib.request.urlopen(req, timeout=timeout, context=CTX)
    return r.read(3000000).decode(r.headers.get_content_charset() or "utf-8", "replace")


def http_json(url, body=None, headers=None, timeout=90):
    data = None
    h = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        h["Content-Type"] = "application/json"
    return json.loads(http(url, data=data, headers=h, timeout=timeout))


def pulisci(testo):
    if not testo:
        return ""
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", str(testo), flags=re.S | re.I)
    t = re.sub(r"<br\s*/?>|</p>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    # html.unescape copre tutte le entita' (&#x27; &egrave; &nbsp; ...) senza
    # doverle elencare a mano: prima ne sfuggivano e finivano nei titoli
    t = html.unescape(t).replace(" ", " ")
    return re.sub(r"[ \t]+", " ", t).strip()


def senza_accenti(s):
    return "".join(c for c in unicodedata.normalize("NFD", s or "")
                   if unicodedata.category(c) != "Mn").lower()


def data_iso(v):
    """Normalizza date di formati diversi in AAAA-MM-GG."""
    if not v:
        return None
    s = str(v)
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", s)
    if m:
        return "%s-%s-%s" % (m.group(3), m.group(2), m.group(1))
    return None


def giorni_da(iso):
    if not iso:
        return None
    try:
        d = datetime.strptime(iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (OGGI - d).days
    except Exception:
        return None


def giorni_a(iso):
    g = giorni_da(iso)
    return None if g is None else -g



# ----------------------------------------------------------------- stipendio --
# Pochi annunci dichiarano quanto pagano: circa uno su dieci. Quel poco che
# c'e' va comunque raccolto, sia dai campi delle fonti sia dal testo.

RX_RAL = re.compile(
    r"(?:RAL|R\.A\.L\.|retribuzione(?:\s+annua)?(?:\s+lorda)?|stipendio|"
    r"compenso annuo|pacchetto retributivo)"
    r"[^.\n]{0,50}?"
    r"(?:€|EUR|euro)?\s*"
    r"(\d{1,3}(?:[.,\s]\d{3})+|\d{2,3}\s*(?:k|mila))",
    re.I)


def _numero(pezzo):
    p = senza_accenti(pezzo).replace(" ", "")
    if p.endswith("k") or p.endswith("mila"):
        n = re.sub(r"[^0-9]", "", p)
        return int(n) * 1000 if n else None
    n = re.sub(r"[^0-9]", "", p)
    if not n:
        return None
    v = int(n)
    return v if 10000 <= v <= 200000 else None


def stipendio_da_testo(testo):
    """Cerca una cifra annua lorda nel testo dell'annuncio."""
    if not testo:
        return None, None
    valori = []
    for m in RX_RAL.finditer(testo):
        v = _numero(m.group(1))
        if v:
            valori.append(v)
    if not valori:
        return None, None
    return min(valori), (max(valori) if max(valori) != min(valori) else None)


def aggiungi_stipendio(ann):
    """Riempie stipendio_min/max: prima quello dichiarato dalla fonte, poi il testo."""
    if ann.get("stipendio_min"):
        return
    mn, mx = stipendio_da_testo(" ".join(filter(None, [ann.get("titolo"),
                                                       ann.get("descrizione")])))
    ann["stipendio_min"] = mn
    ann["stipendio_max"] = mx


# ------------------------------------------------------------- punteggiatura --

AMBITI = [
    ("Analista funzionale / Business Analyst", 10, [
        "business analyst", "analista funzionale", "analista applicativo",
        "functional analyst", "analista di processo", "requirements analyst",
        "analista informatico", "system analyst", "analista software",
        "it analyst", "data analyst", "process analyst"]),
    ("Project management / PMO", 10, [
        "project manager", "pmo", "project management", "capo progetto",
        "program manager", "project specialist", "project coordinator",
        "coordinatore di progetto", "project cost controller",
        "digital project", "scrum master", "project office",
        "capo progetto informatico", "funzionario informatico"]),
    ("Amministrativo e organizzativo", 10, [
        "impiegato amministrativo", "impiegata amministrativa", "back office",
        "assistente di direzione", "segreteria", "amministrazione",
        "funzionario amministrativo", "istruttore amministrativo",
        "collaboratore amministrativo", "assistente amministrativo",
        "office manager", "addetto amministrativo", "supporto amministrativo",
        "area amministrativa", "gestione ordini", "order management",
        "administrative support", "administrative assistant", "office assistant",
        "operations specialist", "planning & reporting", "order control",
        "istruttore direttivo", "area dei funzionari", "funzionario servizi",
        "specialista amministrativo", "affari generali",
        # profili amministrativi usati dagli enti sanitari e dai comuni
        "coadiutore amministrativo", "operatore amministrativo",
        "collaboratore amministrativo professionale", "assistente amministrativo",
        "coadiutore amministrativo senior", "istruttore amministrativo contabile",
        "funzionario amministrativo contabile", "esperto amministrativo"]),
    ("E-commerce", 8, [
        "e-commerce", "ecommerce", "digital commerce", "magento", "shopify",
        "catalogo prodotti", "marketplace", "web shop", "vendita online"]),
    ("Marketing digitale e SEO", 8, [
        "digital marketing", "marketing digitale", "seo", "sem", "google ads",
        "web marketing", "performance marketing", "marketing specialist",
        "growth", "campagne digitali", "google analytics"]),
    ("Comunicazione e social", 8, [
        "comunicazione", "social media", "ufficio stampa", "content",
        "addetto stampa", "communication", "brand manager", "editoriale",
        "copywriter", "relazioni esterne", "event manager"]),
    ("Commerciale e contatto cliente", 5, [
        "account", "commerciale", "customer success", "sales",
        "customer experience", "consulente commerciale", "business development"]),
]

# competenze effettive del profilo: se compaiono, il punteggio sale
COMPETENZE = {
    "jira": "JIRA", "confluence": "Confluence", "magento": "Magento",
    "azure": "Azure", "agile": "Agile", "scrum": "Scrum",
    "requisit": "analisi dei requisiti", "user story": "user story",
    "backlog": "backlog", "uat": "UAT", "test case": "test case",
    "collaudo": "collaudo", "stakeholder": "stakeholder",
    "google analytics": "Google Analytics", "google ads": "Google Ads",
    "seo": "SEO", "e-commerce": "e-commerce", "ecommerce": "e-commerce",
    "automotive": "automotive", "excel": "Excel", "inglese": "inglese",
    "francese": "francese", "aftersales": "aftersales", "erp": "ERP",
    "crm": "CRM", "digitalizzazione": "digitalizzazione",
}

# esclusioni assolute
ESCLUDI_STAGE = re.compile(
    r"\b(stage|stagista|tirocin\w+|internship|intern\b|apprendista di primo livello)", re.I)
ESCLUDI_TURNI = re.compile(
    r"\b(su turni|lavoro a turni|turnazione|turni notturni|notturno|"
    r"disponibilit[àa] ai turni|ciclo continuo|h24|su 3 turni|tre turni)", re.I)
ESCLUDI_RUOLO = re.compile(
    r"^(?=.*\b(qa|quality assurance|software tester|test engineer|tester|"
    r"help ?desk|service desk|supporto applicativo|assistenza tecnica|"
    r"operatore call center|call center)\b)", re.I)

# segnali di contratto
RX_DETERMINATO = re.compile(r"tempo determinato|a termine|fixed[- ]term|determinato", re.I)
RX_INDETERMINATO = re.compile(r"tempo indeterminato|indeterminato|permanent", re.I)
RX_APPRENDISTATO = re.compile(r"apprendistato|apprendist", re.I)
RX_SOMMINISTRAZIONE = re.compile(r"somministrazione|interinale|staff leasing", re.I)
RX_PARTTIME = re.compile(r"part[- ]?time|tempo parziale", re.I)
RX_REMOTO = re.compile(r"\b(smart working|full remote|da remoto|remote|telelavoro|ibrid\w+|hybrid)\b", re.I)
RX_SENIOR = re.compile(r"\b(senior|almeno (5|6|7|8|9|10)\s*anni|5\+\s*anni|"
                       r"esperienza pluriennale|responsabile di funzione|head of|director)\b", re.I)
RX_LAUREA_TECNICA = re.compile(
    r"laurea in ingegneria|laurea in informatica|ingegneria informatica|"
    r"laurea magistrale in ingegneria|degree in engineering|computer science", re.I)
RX_LINGUE = re.compile(r"\b(inglese|english|francese|french)\b", re.I)


def valuta(ann):
    """Assegna punteggio 0-100 e spiegazione. Restituisce anche l'eventuale esclusione."""
    testo = " ".join([ann.get("titolo") or "", ann.get("descrizione") or "",
                      ann.get("ente") or ""])
    t = senza_accenti(testo)
    titolo = senza_accenti(ann.get("titolo") or "")

    # --- esclusioni ---
    # uno stage nel titolo e' sempre uno stage, anche se il testo cita l'apprendistato
    if ESCLUDI_STAGE.search(ann.get("titolo") or ""):
        return None, None, "stage o tirocinio"
    if ESCLUDI_STAGE.search(testo) and not RX_APPRENDISTATO.search(testo):
        return None, None, "stage o tirocinio"
    if ESCLUDI_TURNI.search(testo):
        return None, None, "lavoro su turni"
    if ESCLUDI_RUOLO.search(ann.get("titolo") or ""):
        return None, None, "ruolo di QA o assistenza"

    # --- ambito migliore ---
    # Il titolo dice che lavoro e'. La descrizione cita di tutto: se l'ambito
    # compare solo li', vale molto meno (altrimenti un "capo cantiere" la cui
    # descrizione nomina il project management finisce in cima).
    migliore, punteggio_migliore, solo_descrizione = None, 0, True
    for nome, peso, chiavi in AMBITI:
        nel_titolo = [k for k in chiavi if k in titolo]
        nel_testo = [k for k in chiavi if k in t]
        if not nel_testo:
            continue
        if nel_titolo:
            valore = peso * 5.5 + 10        # match sul titolo: pieno (65 per un ambito da 10)
            da_titolo = True
        else:
            valore = peso * 2.2             # solo nella descrizione: molto ridotto
            da_titolo = False
        if valore > punteggio_migliore:
            migliore, punteggio_migliore, solo_descrizione = nome, valore, not da_titolo

    if not migliore:
        return 0, {"ambito": None, "pro": [],
                   "contro": ["non corrisponde a nessuno degli ambiti cercati"]}, None

    punti = punteggio_migliore
    pro, contro = [], []
    if solo_descrizione:
        pro.append("attinente a " + migliore.lower() + ", ma solo nel testo dell'annuncio")
        contro.append("il ruolo nel titolo non e' quello cercato")
    else:
        pro.append(migliore.lower())

    # il mestiere che fa oggi vale un premio: e' li' che e' piu' credibile
    if migliore.startswith(("Analista", "Project")) and not solo_descrizione:
        punti += 8
        pro.append("e' esattamente il ruolo che ricopre oggi")

    # competenze in comune
    comp = []
    for chiave, etichetta in COMPETENZE.items():
        if chiave in t and etichetta not in comp:
            comp.append(etichetta)
    if comp:
        punti += min(len(comp) * 3, 12)
        pro.append("competenze in comune: " + ", ".join(comp[:5]))

    # lingue
    if RX_LINGUE.search(testo):
        punti += 3
        pro.append("richiede lingue straniere")

    # contratto
    contratto = "non indicato"
    if RX_APPRENDISTATO.search(testo):
        contratto = "apprendistato"
        punti += 4
        pro.append("apprendistato")
    elif RX_INDETERMINATO.search(testo):
        contratto = "indeterminato"
        punti += 6
        pro.append("tempo indeterminato")
    elif RX_SOMMINISTRAZIONE.search(testo):
        contratto = "somministrazione"
        punti -= 10
        contro.append("contratto in somministrazione")
    elif RX_DETERMINATO.search(testo):
        contratto = "determinato"
        contro.append("tempo determinato")

    if RX_PARTTIME.search(testo):
        punti -= 12
        contro.append("part time")

    remoto = bool(RX_REMOTO.search(testo))
    if remoto:
        punti += 5
        pro.append("previsto lavoro da remoto o ibrido")

    if RX_SENIOR.search(testo):
        punti -= 18
        contro.append("richiede esperienza senior")

    if RX_LAUREA_TECNICA.search(testo):
        punti -= 15
        contro.append("chiede una laurea tecnica")

    # freschezza
    g = giorni_da(ann.get("pubblicato"))
    if g is not None and g > 45:
        punti -= 10
        contro.append("annuncio di oltre 45 giorni fa")

    punti = max(0, min(100, int(round(punti))))
    return punti, {"ambito": migliore, "pro": pro, "contro": contro,
                   "contratto": contratto, "remoto": remoto}, None


# ------------------------------------------------------------------- fonti ---

FONTI_ESITO = []


def fonte(nome):
    """Decoratore: cattura gli errori di una fonte senza fermare tutto."""
    def deco(fn):
        def wrap():
            t0 = time.time()
            try:
                res = fn()
                FONTI_ESITO.append({"fonte": nome, "ok": True, "n": len(res),
                                    "secondi": round(time.time() - t0, 1)})
                return res
            except Exception as e:
                FONTI_ESITO.append({"fonte": nome, "ok": False, "n": 0,
                                    "errore": type(e).__name__ + ": " + str(e)[:120],
                                    "secondi": round(time.time() - t0, 1)})
                return []
        wrap.__name__ = fn.__name__
        return wrap
    return deco


@fonte("InPA - concorsi pubblici")
def fonte_inpa():
    url = ("https://portale.inpa.gov.it/concorsi-smart/api/"
           "concorso-public-area/search-better?page=0&size=200")
    body = {"text": "", "categoriaId": None, "regioneId": "8", "status": ["OPEN"],
            "settoreId": None, "provinciaCodice": "GE", "dateFrom": None, "dateTo": None,
            "livelliAnzianitaIds": None, "tipoImpiegoId": None,
            "salaryMin": 0, "salaryMax": 1000000, "enteRiferimentoName": ""}
    j = http_json(url, body=body)
    out = []
    def campo(valore, *chiavi):
        """L'API restituisce a volte un oggetto, a volte una stringa: reggi entrambi."""
        if isinstance(valore, dict):
            for k in chiavi:
                if valore.get(k):
                    return valore[k]
            return ""
        if isinstance(valore, list) and valore:
            return campo(valore[0], *chiavi)
        return valore or ""

    for c in j.get("content", []):
        ente = campo(c.get("ente"), "nome", "descrizione", "denominazione")
        if not ente:
            ente = campo(c.get("entiRiferimento"), "nome", "descrizione", "denominazione")
        sede = campo(c.get("sedi"), "comune", "descrizione", "citta", "nome")
        # Il campo "titolo" e' il titolo burocratico del bando, spesso troncato
        # prima di dire che profilo cercano ("...PER LA COPERTURA DI N.1 POSTO DI").
        # Il ruolo vero sta in figuraRicercata: usiamo quello come intestazione,
        # altrimenti meta' dei concorsi risulterebbe senza ruolo riconoscibile.
        ufficiale = pulisci(c.get("titolo"))
        figura = pulisci(c.get("figuraRicercata"))
        out.append({
            "id": "inpa-" + str(c.get("id")),
            "titolo": figura or ufficiale,
            "titolo_ufficiale": ufficiale if figura and ufficiale != figura else None,
            "ente": pulisci(ente) or "Ente pubblico",
            "luogo": pulisci(sede) or "Provincia di Genova",
            "settore": "pubblico",
            "fonte": "InPA",
            "url": "https://www.inpa.gov.it/bandi-e-avvisi/dettaglio-bando-avviso/?concorso_id=" + str(c.get("id")),
            "pubblicato": data_iso(c.get("dataPubblicazione")),
            "scadenza": data_iso(c.get("dataScadenza")),
            "posti": c.get("numPosti"),
            "stipendio_min": c.get("salaryMin") or None,
            "stipendio_max": c.get("salaryMax") or None,
            "descrizione": pulisci(" ".join(filter(None, [
                ufficiale, c.get("descrizioneBreve"), c.get("descrizione")])))[:2500],
        })
    return out


@fonte("Formazione Lavoro Regione Liguria")
def fonte_liguria():
    # Dal computer di casa risponde in 3 secondi, dai server di GitHub e' molto
    # piu' lenta e la prima chiamata va spesso in timeout: si riprova, con
    # pagine piu' piccole a ogni tentativo.
    # Da una connessione italiana risponde in 3 secondi; dai server di GitHub
    # la connessione resta appesa fino al timeout, anche riprovando a lungo:
    # con ogni probabilita' il portale scarta gli indirizzi esteri. Quindi si
    # fa un solo tentativo breve e, se non risponde, si tira avanti senza:
    # il pannello dira' da solo che questa fonte manca.
    url = ("https://flguest.regione.liguria.it/services/api/DomandeLavoro"
           "?dataByOption=all&idProgramma=5&pageNumber=1&pageSize=500&stato=3")
    j = http_json(url, timeout=25)
    out = []
    for x in j.get("items", []):
        if (x.get("provincia") or "").upper() != "GE":
            continue
        fig = x.get("figuraProfessionale") or {}
        titolo = fig.get("tag") if isinstance(fig, dict) else str(fig)
        out.append({
            "id": "liguria-" + str(x.get("id") or x.get("codice")),
            "titolo": pulisci(titolo),
            "ente": pulisci(x.get("azienda")) or "Azienda non indicata",
            "luogo": pulisci(x.get("comune")),
            "settore": "privato",
            "fonte": "Regione Liguria",
            "url": "https://flguest.regione.liguria.it/public/#/dashboard/annunci",
            "pubblicato": data_iso(x.get("createdAt")),
            "scadenza": data_iso(x.get("dataScadenza")),
            "descrizione": pulisci(x.get("descrizione"))[:2500],
        })
    return out


@fonte("Adzuna")
def fonte_adzuna():
    out, visti = [], set()
    chiavi = ["business analyst", "analista funzionale", "project manager", "PMO",
              "e-commerce", "digital marketing", "comunicazione",
              "impiegato amministrativo", "back office", "marketing",
              "analista", "coordinatore"]
    for kw in chiavi:
        p = {"app_id": ADZ["app_id"], "app_key": ADZ["app_key"],
             "results_per_page": 50, "what": kw, "where": "Genova",
             "distance": 30, "max_days_old": 45, "sort_by": "date"}
        try:
            j = http_json(ADZ["endpoint"] + "?" + urllib.parse.urlencode(p))
        except Exception:
            time.sleep(1)
            continue
        for r in j.get("results", []):
            rid = str(r.get("id"))
            if rid in visti:
                continue
            visti.add(rid)
            stimato = bool(r.get("salary_is_predicted") in (1, "1", True))
            out.append({
                "id": "adzuna-" + rid,
                "stipendio_min": None if stimato else r.get("salary_min"),
                "stipendio_max": None if stimato else r.get("salary_max"),
                "titolo": pulisci(r.get("title")),
                "ente": pulisci((r.get("company") or {}).get("display_name")) or "Azienda non indicata",
                "luogo": pulisci((r.get("location") or {}).get("display_name")),
                "settore": "privato",
                "fonte": "Adzuna",
                "url": r.get("redirect_url"),
                "pubblicato": data_iso(r.get("created")),
                "scadenza": None,
                "descrizione": pulisci(r.get("description"))[:2500],
            })
        time.sleep(0.35)
    return out


@fonte("Leonardo")
def fonte_leonardo():
    url = ("https://leonardocompany.wd3.myworkdayjobs.com/wday/cxs/"
           "leonardocompany/LeonardoCareerSite/jobs")
    out = []
    for offset in (0, 20, 40):
        j = http_json(url, body={"appliedFacets": {}, "limit": 20,
                                 "offset": offset, "searchText": "Genova"})
        posti = j.get("jobPostings") or []
        if not posti:
            break
        for p in posti:
            loc = p.get("locationsText") or ""
            if "genova" not in senza_accenti(loc):
                continue
            out.append({
                "id": "leonardo-" + str(p.get("bulletFields", [""])[0] or p.get("externalPath")),
                "titolo": pulisci(p.get("title")),
                "ente": "Leonardo",
                "luogo": pulisci(loc),
                "settore": "privato",
                "fonte": "Leonardo",
                "url": "https://leonardocompany.wd3.myworkdayjobs.com/it-IT/LeonardoCareerSite" + (p.get("externalPath") or ""),
                "pubblicato": None,
                "scadenza": None,
                "descrizione": pulisci(p.get("title")),
            })
        time.sleep(0.3)
    return out


@fonte("MSC Crociere")
def fonte_msc():
    body = {"lang": "en_gb", "deviceType": "desktop", "country": "gb",
            "pageName": "search-results", "ddoKey": "refineSearch", "sortBy": "",
            "subsearch": "", "from": 0, "jobs": True, "counts": True,
            "all_fields": ["country", "state", "city", "category", "type"],
            "size": 50, "clearAll": False, "jdsource": "facets",
            "isSliderEnable": False, "pageId": "page11", "siteType": "external",
            "keywords": "", "global": True,
            "selected_fields": {"country": ["Italy"]}, "locationData": {}}
    j = http_json("https://careers.msccruises.com/widgets", body=body)
    jobs = (((j.get("refineSearch") or {}).get("data") or {}).get("jobs")) or []
    out = []
    for x in jobs:
        citta = x.get("cityState") or x.get("city") or ""
        if "gen" not in senza_accenti(citta):
            continue
        out.append({
            "id": "msc-" + str(x.get("jobId") or x.get("reqId")),
            "titolo": pulisci(x.get("title")),
            "ente": "MSC Crociere",
            "luogo": pulisci(citta) or "Genova",
            "settore": "privato",
            "fonte": "MSC",
            "url": x.get("applyUrl") or ("https://careers.msccruises.com/gb/en/job/" + str(x.get("jobId"))),
            "pubblicato": data_iso(x.get("postedDate")),
            "scadenza": None,
            "descrizione": pulisci(x.get("descriptionTeaser") or x.get("title"))[:2500],
        })
    return out


@fonte("Costa Crociere")
def fonte_costa():
    base = ("https://eicl.fa.em5.oraclecloud.com/hcmRestApi/resources/latest/"
            "recruitingCEJobRequisitions?onlyData=true&expand=requisitionList&finder=")
    out = []
    # il parametro offset non e' supportato da questo finder: si chiede tutto
    # in un colpo solo e si filtra l'Italia qui.
    for limite in (200, 50):
        # siteNumber=CORP e' il sito globale del gruppo (216 posizioni, Italia inclusa);
        # CX_1 e' solo quello statunitense e non contiene Genova.
        f = "findReqs;siteNumber=CORP,limit=%d,sortBy=POSTING_DATES_DESC" % limite
        try:
            j = http_json(base + urllib.parse.quote(f, safe=";,="))
        except Exception:
            continue
        items = j.get("items") or []
        reqs = (items[0].get("requisitionList") or []) if items else []
        if not reqs:
            continue
        for x in reqs:
            paese = str(x.get("PrimaryLocationCountry") or "")
            luogo = str(x.get("PrimaryLocation") or "")
            if paese.upper() != "IT" and "ital" not in senza_accenti(luogo):
                continue
            out.append({
                "id": "costa-" + str(x.get("Id")),
                "titolo": pulisci(x.get("Title")),
                "ente": "Costa Crociere",
                "luogo": pulisci(luogo),
                "settore": "privato",
                "fonte": "Costa",
                "url": "https://career.costacrociere.it/shoreside/",
                "pubblicato": data_iso(x.get("PostedDate")),
                "scadenza": data_iso(x.get("PostingEndDate")),
                "descrizione": pulisci(" ".join(filter(None, [
                    x.get("Title"), x.get("JobFamily"), x.get("JobFunction"),
                    x.get("ContractType"), x.get("JobSchedule")]))),
            })
        if out:
            break
        time.sleep(0.3)
    return out


@fonte("RINA")
def fonte_rina():
    out = []
    for citta in ("Genova", "Genoa"):
        try:
            h = http("https://careers.rina.org/search/?q=&locationsearch=" +
                     urllib.parse.quote(citta))
        except Exception:
            continue
        for m in re.finditer(
                r'<a[^>]+href="(/job/[^"]+)"[^>]*>(.*?)</a>', h, re.S | re.I):
            titolo = pulisci(m.group(2))
            if not titolo or len(titolo) < 6:
                continue
            out.append({
                "id": "rina-" + re.sub(r"\W+", "", m.group(1))[-24:],
                "titolo": titolo,
                "ente": "RINA",
                "luogo": "Genova",
                "settore": "privato",
                "fonte": "RINA",
                "url": "https://careers.rina.org" + m.group(1),
                "pubblicato": None,
                "scadenza": None,
                "descrizione": titolo,
            })
    return out


# ------------------------------------------- aziende genovesi, pagina per pagina --
# Queste sei non hanno un canale dati: si legge la pagina cosi' com'e'. Se un
# giorno rifanno il sito il pezzo smette di funzionare, ma il pannello lo dice
# in cima invece di far finta di niente.

CITTA = re.compile(
    r"\b(genova|genoa|milano|roma|torino|trieste|napoli|bologna|firenze|padova|"
    r"verona|bari|palermo|catania|venezia|brescia|marina di carrara|la spezia|"
    r"savona|imperia|chiavari|rapallo|sestri|arenzano|busalla)\b", re.I)


def sede_da_testo(testo, predefinita="sede da verificare"):
    m = CITTA.search(testo or "")
    return m.group(0).title() if m else predefinita


def in_zona(testo):
    """Vero se il testo parla di Genova o di lavoro da remoto."""
    t = senza_accenti(testo or "")
    return ("genova" in t or "genoa" in t or "remot" in t or "ibrid" in t
            or "smart working" in t)


@fonte("Circle Group")
def fonte_circle():
    h = http("https://www.circlegroup.eu/work-with-us/")
    out = []
    # I testi delle posizioni sono lunghi: cercare titolo e corpo con una sola
    # espressione limitata a 2500 caratteri non trovava nulla. Si taglia sui
    # titoli e si guarda dentro ogni pezzo.
    for blocco in re.split(r"(?=<h3)", h)[1:]:
        tit = re.match(r"<h3[^>]*>\s*<a[^>]*>(.*?)</a>\s*</h3>", blocco, re.S | re.I)
        if not tit:
            continue
        titolo = pulisci(tit.group(1))
        corpo = pulisci(blocco[tit.end():])[:1800]
        if not titolo or len(titolo) < 5 or len(titolo) > 95:
            continue
        if not re.search(r"[A-Za-z]{3}", titolo):
            continue
        out.append({
            "id": "circle-" + re.sub(r"\W+", "", senza_accenti(titolo))[:32],
            "titolo": titolo,
            "ente": "Circle Group",
            "luogo": sede_da_testo(corpo, "Genova"),
            "settore": "privato",
            "fonte": "Circle Group",
            "url": "https://www.circlegroup.eu/work-with-us/",
            "pubblicato": None, "scadenza": None,
            "descrizione": corpo,
        })
    return out


@fonte("NTT Data Italia")
def fonte_nttdata():
    h = http("https://it.nttdata.com/career/posizioni-aperte")
    out = []
    for blocco in re.split(r'(?=<a\s+href="[^"]*"[^>]*class="search-result row job-detail-link")',
                           h)[1:]:
        blocco = blocco[:2500]
        href = re.search(r'<a\s+href="([^"]+)"', blocco)
        titoli = re.findall(r'is-h5[^>]*>(.*?)</p>', blocco, re.S)
        piccoli = re.findall(r'is-small[^>]*>(.*?)</p>', blocco, re.S)
        if not href or not titoli:
            continue
        m = href
        titolo = pulisci(titoli[0])
        luogo = pulisci(piccoli[0]) if piccoli else ""
        area = pulisci(piccoli[1]) if len(piccoli) > 1 else ""
        if not titolo:
            continue
        # "Multilocations" vuol dire piu' sedi possibili: si tiene ma si dice
        multi = "multilocation" in senza_accenti(luogo)
        if not multi and not in_zona(luogo):
            continue
        out.append({
            "id": "ntt-" + re.sub(r"\W+", "", href.group(1))[-28:],
            "titolo": titolo,
            "ente": "NTT Data Italia",
            "luogo": "piu' sedi, da verificare" if multi else luogo,
            "settore": "privato",
            "fonte": "NTT Data",
            "url": urllib.parse.urljoin("https://it.nttdata.com/", href.group(1)),
            "pubblicato": None, "scadenza": None,
            "descrizione": " ".join([titolo, area, luogo]),
        })
    return out


@fonte("Softjam")
def fonte_softjam():
    h = http("https://www.softjam.it/careers/")
    out = []
    for m in re.finditer(
            r'<article[^>]*data-link="([^"]+)"[^>]*>.*?'
            r'careers-title">(.*?)</h3>.*?'
            r'careers-location"><strong>(.*?)</strong>', h, re.S | re.I):
        titolo, luogo = pulisci(m.group(2)), pulisci(m.group(3))
        if not titolo or not in_zona(luogo):
            continue
        out.append({
            "id": "softjam-" + re.sub(r"\W+", "", m.group(1))[-28:],
            "titolo": titolo, "ente": "Softjam", "luogo": luogo,
            "settore": "privato", "fonte": "Softjam", "url": m.group(1),
            "pubblicato": None, "scadenza": None,
            "descrizione": titolo + " " + luogo,
        })
    return out


@fonte("Sogegross / Basko")
def fonte_sogegross():
    h = http("https://sogegross.intervieweb.it/app.php?module=career&lang=it")
    out = []
    # La pagina pesa mezzo megabyte: cercare con un'unica espressione su tutto
    # il documento la mandava in stallo. Si taglia prima in blocchi, uno per
    # annuncio, e si cerca dentro ciascuno.
    for blocco in re.split(r'vacancy__title"', h)[1:]:
        blocco = blocco[:2500]
        link = re.search(r'<a href="([^"]+)"', blocco)
        tit = re.search(r'<h3>\s*(.*?)\s*</h3>', blocco, re.S)
        if not link or not tit:
            continue
        m = link
        titolo = pulisci(tit.group(1))
        corpo = pulisci(blocco)[:700]
        if not titolo:
            continue
        if not in_zona(titolo + " " + corpo):
            continue
        out.append({
            "id": "sogegross-" + re.sub(r"\W+", "", m.group(1))[-28:],
            "titolo": titolo, "ente": "Sogegross / Basko",
            "luogo": sede_da_testo(titolo + " " + corpo, "Genova"),
            "settore": "privato", "fonte": "Sogegross", "url": m.group(1),
            "pubblicato": None, "scadenza": None,
            "descrizione": titolo + " " + corpo,
        })
    return out


@fonte("Grendi")
def fonte_grendi():
    h = http("https://www.grendi.it/lavora-con-noi/")
    out = []
    for blocco in re.split(r"(?=e-loop-item e-loop-item-)", h)[1:]:
        link = re.search(r'href="(https://www\.grendi\.it/posizioni-aperte/[^"]+)"', blocco)
        if not link:
            continue
        titoli = [pulisci(t[1]) for t in re.findall(r"<(h[1-4])[^>]*>(.*?)</\1>",
                                                    blocco, re.S | re.I)]
        titoli = [t for t in titoli if t]
        if not titoli:
            continue
        # nel loro impaginato il primo titolo e' la citta', il secondo il ruolo
        luogo = titoli[0] if CITTA.fullmatch(titoli[0] or "") else ""
        ruolo = next((t for t in titoli if t != luogo), titoli[-1])
        if not luogo:
            luogo = sede_da_testo(blocco, "")
        if not in_zona(luogo + " " + ruolo):
            continue
        out.append({
            "id": "grendi-" + re.sub(r"\W+", "", link.group(1))[-28:],
            "titolo": ruolo, "ente": "Grendi", "luogo": luogo or "Genova",
            "settore": "privato", "fonte": "Grendi", "url": link.group(1),
            "pubblicato": None, "scadenza": None,
            "descrizione": ruolo + " " + luogo,
        })
    return out


MESI = {"gennaio": "01", "febbraio": "02", "marzo": "03", "aprile": "04",
        "maggio": "05", "giugno": "06", "luglio": "07", "agosto": "08",
        "settembre": "09", "ottobre": "10", "novembre": "11", "dicembre": "12"}


@fonte("Liguria Digitale")
def fonte_liguria_digitale():
    url = ("https://trasparenza.liguriadigitale.it/trasparenza/"
           "selezione-del-personale/avvisi-di-selezione.html")
    h = http(url)
    out = []
    for m in re.finditer(r"<strong>([^<]{6,110})</strong>(.{0,600}?)"
                         r"(?=<strong>|\Z)", h, re.S | re.I):
        titolo = pulisci(m.group(1))
        corpo = m.group(2)
        if not re.search(r"(?i)rif\.|selezione|ricerca", titolo):
            continue
        sc = re.search(r"(?i)scadenza[^.]{0,80}?(\d{1,2})\s+([a-zà]+)\s+(\d{4})", corpo)
        scadenza = None
        if sc and sc.group(2).lower() in MESI:
            scadenza = "%s-%s-%02d" % (sc.group(3), MESI[sc.group(2).lower()],
                                       int(sc.group(1)))
        if scadenza and (giorni_a(scadenza) or 0) < 0:
            continue          # avviso gia' scaduto
        out.append({
            "id": "ligdig-" + re.sub(r"\W+", "", senza_accenti(titolo))[:32],
            "titolo": re.sub(r"\s*-\s*Rif\..*$", "", titolo, flags=re.I),
            "ente": "Liguria Digitale", "luogo": "Genova",
            "settore": "privato", "fonte": "Liguria Digitale", "url": url,
            "pubblicato": None, "scadenza": scadenza,
            "descrizione": titolo + " " + pulisci(corpo)[:600],
        })
    return out



# ------------------------------------------- connettori riutilizzabili --------
# Molte aziende usano le stesse due piattaforme di reclutamento. Scritte una
# volta sola, aggiungere un datore di lavoro costa una riga.

def da_workday(azienda, tenant, sito, cerca="Genova", solo_genova=True):
    url = ("https://%s.wd3.myworkdayjobs.com/wday/cxs/%s/%s/jobs"
           % (tenant, tenant, sito))
    if "wd103" in tenant or "." in tenant:
        url = "https://%s/wday/cxs/%s/%s/jobs" % (tenant, tenant.split(".")[0], sito)
    out = []
    for offset in (0, 20, 40):
        j = http_json(url, body={"appliedFacets": {}, "limit": 20,
                                 "offset": offset, "searchText": cerca})
        posti = j.get("jobPostings") or []
        if not posti:
            break
        for p in posti:
            loc = p.get("locationsText") or ""
            if solo_genova and "genova" not in senza_accenti(loc) and                "genoa" not in senza_accenti(loc):
                continue
            out.append({
                "id": "wd-" + re.sub(r"\W+", "", azienda)[:10].lower() + "-" +
                      re.sub(r"\W+", "", str(p.get("externalPath") or p.get("title")))[-24:],
                "titolo": pulisci(p.get("title")),
                "ente": azienda,
                "luogo": pulisci(loc) or "Genova",
                "settore": "privato",
                "fonte": azienda,
                "url": "https://%s.wd3.myworkdayjobs.com/it-IT/%s%s" % (
                    tenant, sito, p.get("externalPath") or ""),
                "pubblicato": None, "scadenza": None,
                "descrizione": pulisci(p.get("title")) + " " + loc,
            })
        time.sleep(0.3)
    return out


def da_oracle(azienda, host, sito, url_pubblico):
    base = (host + "/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
            "?onlyData=true&expand=requisitionList&finder=")
    f = "findReqs;siteNumber=%s,limit=200,sortBy=POSTING_DATES_DESC" % sito
    j = http_json(base + urllib.parse.quote(f, safe=";,="))
    items = j.get("items") or []
    reqs = (items[0].get("requisitionList") or []) if items else []
    out = []
    for x in reqs:
        luogo = str(x.get("PrimaryLocation") or "")
        if not re.search(r"(?i)genova|genoa|liguria", luogo):
            continue
        out.append({
            "id": "orc-" + re.sub(r"\W+", "", azienda)[:10].lower() + "-" + str(x.get("Id")),
            "titolo": pulisci(x.get("Title")),
            "ente": azienda,
            "luogo": pulisci(luogo),
            "settore": "privato",
            "fonte": azienda,
            "url": url_pubblico,
            "pubblicato": data_iso(x.get("PostedDate")),
            "scadenza": data_iso(x.get("PostingEndDate")),
            "descrizione": pulisci(" ".join(filter(None, [
                x.get("Title"), x.get("JobFamily"), x.get("JobFunction"),
                x.get("ContractType"), x.get("JobSchedule")]))),
        })
    return out


# Accenture NON e' collegata di proposito: il suo Workday restituisce 2000
# posizioni di tutto il mondo e non espone il campo della sede, quindi non
# c'e' modo di isolare Genova. Includerla avrebbe voluto dire riempire il
# pannello di annunci stranieri o etichettare come genovesi offerte di
# Stoccolma. Meglio non averla che averla sbagliata.


@fonte("Poste Italiane")
def fonte_poste():
    return da_oracle("Poste Italiane",
                     "https://fa-emza-saasfaprod1.fa.ocs.oraclecloud.com",
                     "CX_3001",
                     "https://carriere.posteitaliane.it/it/sites/CX_3001/jobs")



SEZIONI_NON_TITOLO = re.compile(
    r"(?i)^(about us|chi siamo|job description|descrizione|accountabilit|"
    r"requirement|requisiti|responsabilit|qualification|competenze|"
    r"cosa offriamo|what we offer|benefit|sede|luogo|contratto|profilo)")

RUOLO_PLAUSIBILE = re.compile(
    r"(?i)(engineer|manager|analyst|analista|specialist|technician|tecnic|"
    r"developer|architect|consultant|consulente|coordinator|coordinatore|"
    r"designer|planner|supervisor|officer|controller|impiegat|addett|"
    r"responsabile|coordinat|coordinamento|buyer|account|expert|esperto)")


@fonte("Hitachi Rail")
def fonte_hitachi():
    """Hitachi Rail (stabilimento a Genova Sestri).

    L'elenco lo serve un canale dati aperto, ma senza il campo del titolo:
    il ruolo va pescato nel testo della descrizione, dove e' in grassetto."""
    base = ("https://www.hitachirail.com/umbraco/api/workdayrebuild/getjobs"
            "?ItemsPerPage=100&sortByField=primaryJobPostingDate"
            "&sortDirection=DESC&page=%d&")
    intestazioni = {"Referer": "https://www.hitachirail.com/careers/vacancies/"}
    tutti = []
    for pagina in range(1, 6):
        j = http_json(base % pagina, headers=intestazioni)
        elementi = j.get("items") or []
        if not elementi:
            break
        tutti.extend(elementi)
        if len(tutti) >= (j.get("totalResults") or 0):
            break

    def titolo_da(descrizione):
        for m in re.finditer(r"<(?:b|strong)>(.{3,80}?)</(?:b|strong)>",
                             descrizione or "", re.S | re.I):
            t = pulisci(m.group(1)).strip(" :–-")
            if not t or SEZIONI_NON_TITOLO.match(t):
                continue
            if RUOLO_PLAUSIBILE.search(t):
                return t
        return None

    out = []
    for x in tutti:
        sede = str(x.get("primaryJobPostingLocation") or
                   x.get("jobRequisitionIdAndLocation") or "")
        if not re.search(r"(?i)genoa|genova", sede):
            continue
        titolo = titolo_da(x.get("jobDescription"))
        if not titolo:
            continue
        out.append({
            "id": "hitachi-" + str(x.get("jobRequisitionId")),
            "titolo": titolo,
            "ente": "Hitachi Rail",
            "luogo": "Genova",
            "settore": "privato",
            "fonte": "Hitachi Rail",
            "url": ("https://www.hitachirail.com/careers/vacancies/job-details/?jobId="
                    + urllib.parse.quote(str(x.get("jobRequisitionIdAndLocation") or ""))),
            "pubblicato": None,
            "scadenza": None,
            "descrizione": pulisci(x.get("jobDescription"))[:2500],
        })
    return out


@fonte("Enel")
def fonte_enel():
    """Enel, portale Avature.

    La pagina e' servita gia' completa, ma mostra sei annunci per volta e
    ignora la richiesta di mostrarne di piu': si scorrono le pagine. Non
    serve rispondere all'avviso sui cookie, l'elenco c'e' comunque."""
    base = ("https://jobs.enel.com/en_US/careers/JobOpenings/"
            "?jobRecordsPerPage=6&jobOffset=%d")

    def una_pagina(offset):
        try:
            return http(base % offset, timeout=45)
        except Exception:
            return ""

    with ThreadPoolExecutor(max_workers=6) as ex:
        pagine = list(ex.map(una_pagina, range(0, 240, 6)))

    visti, out = set(), []
    for h in pagine:
        if not h:
            continue
        pezzi = re.split(r"Job ID\s*(\d+)", h)
        for i in range(1, len(pezzi), 2):
            jid = pezzi[i]
            if jid in visti:
                continue
            visti.add(jid)
            testo = re.sub(r"<[^>]+>", "\n", pezzi[i + 1][:2000])
            righe = [x.strip() for x in testo.split("\n") if x.strip()]
            if len(righe) < 2:
                continue
            titolo, luogo = pulisci(righe[0]), pulisci(righe[1])
            if not re.search(r"(?i)genova|genoa|liguria", luogo):
                continue
            out.append({
                "id": "enel-" + jid,
                "titolo": titolo,
                "ente": "Enel",
                "luogo": luogo,
                "settore": "privato",
                "fonte": "Enel",
                "url": "https://jobs.enel.com/en_US/careers/JobDetail/" + jid,
                "pubblicato": None,
                "scadenza": None,
                "descrizione": " ".join(righe[:5]),
            })
    return out


@fonte("Randstad")
def fonte_randstad():
    """Randstad, agenzia per il lavoro.

    Le agenzie arrivano gia' da Adzuna, ma solo in parte: Adzuna ne mostrava
    9 per Genova, il loro sito ne ha 148. Vale la pena leggerlo direttamente.
    La pagina giusta e' /offerte-lavoro/re-liguria/ci-genova/, trenta annunci
    per pagina."""
    base = "https://www.randstad.it/offerte-lavoro/re-liguria/ci-genova/"

    def una(pag):
        try:
            return http(base if pag == 1 else base + "page-%d/" % pag, timeout=45)
        except Exception:
            return ""

    with ThreadPoolExecutor(max_workers=5) as ex:
        pagine = list(ex.map(una, range(1, 8)))

    out, visti = [], set()
    for h in pagine:
        if not h:
            continue
        # ogni scheda: un collegamento con la citta' e un codice nell'indirizzo,
        # e il titolo nell'intestazione che segue
        for m in re.finditer(
                r'href="(/offerte-lavoro/[^"]*_[a-z]+_[0-9a-f\-]{8,}/)"(.{0,600}?)</a>',
                h, re.S | re.I):
            url = m.group(1)
            if url in visti:
                continue
            visti.add(url)
            tit = re.search(r"<h[1-4][^>]*>(.*?)</h[1-4]>", m.group(2), re.S | re.I)
            titolo = pulisci(tit.group(1)) if tit else ""
            if not titolo or len(titolo) < 4:
                continue
            corpo = pulisci(m.group(2))
            comune = "Genova"
            mc = re.search(r"_([a-z\-]+)_[0-9a-f\-]{8,}/$", url)
            if mc:
                comune = mc.group(1).replace("-", " ").title()
            out.append({
                "id": "randstad-" + re.sub(r"\W+", "", url)[-30:],
                "titolo": titolo,
                "ente": "Randstad (agenzia)",
                "luogo": comune,
                "settore": "privato",
                "fonte": "Randstad",
                "url": "https://www.randstad.it" + url,
                "pubblicato": None,
                "scadenza": None,
                "descrizione": (titolo + " " + corpo)[:1500],
            })
    return out


@fonte("Fratelli Cosulich")
def fonte_cosulich():
    """Gruppo armatoriale genovese, portale carriere proprio.

    Ogni scheda ha titolo, societa' del gruppo e sede in tre campi distinti:
    si legge senza ambiguita'."""
    h = http("https://career.cosulich.com/")
    out = []
    for blocco in re.split(r'class="card position-cards', h)[1:]:
        blocco = blocco[:2500]
        tit = re.search(r'card-jobs-titolo"[^>]*>(.*?)</', blocco, re.S)
        soc = re.search(r'card-jobs-testo"[^>]*>(.*?)</', blocco, re.S)
        sede = re.search(r'card-jobs-quote[^"]*"[^>]*>(.*?)</', blocco, re.S)
        link = re.search(r'href="(/jobs/[^"]+)"', blocco)
        if not tit or not link:
            continue
        titolo = pulisci(tit.group(1))
        luogo = pulisci(sede.group(1)) if sede else ""
        if not re.search(r"(?i)genova|genoa|liguria|sampierdarena|sestri|"
                         r"cornigliano|voltri|chiavari|rapallo", luogo):
            continue
        societa = pulisci(soc.group(1)) if soc else ""
        out.append({
            "id": "cosulich-" + re.sub(r"\W+", "", link.group(1))[-30:],
            "titolo": titolo,
            "ente": "Gruppo Cosulich" + (" - " + societa if societa else ""),
            "luogo": luogo,
            "settore": "privato",
            "fonte": "Fratelli Cosulich",
            "url": "https://career.cosulich.com" + link.group(1),
            "pubblicato": None,
            "scadenza": None,
            "descrizione": " ".join([titolo, societa, luogo]),
        })
    return out



# I bandi degli enti sanitari hanno titoli lunghissimi in cui il profilo
# cercato e' sepolto a meta' frase ("...di n. 2 unita' di personale con la
# qualifica di dirigente farmacista..."). Senza tirarlo fuori, ogni bando
# prende zero punti e finisce fra i meno pertinenti, com'e' successo alla
# prima prova con i 114 bandi della ASL 3.
PROFILO_NEL_TITOLO = [
    r"(?i)qualifica\s+di\s+([A-Za-zÀ-ù /'\-]{5,70})",
    r"(?i)profilo\s+professionale\s+di\s+([A-Za-zÀ-ù /'\-]{5,70})",
    r"(?i)n\.\s*\d+\s+posti?\s+di\s+([A-Za-zÀ-ù /'\-]{5,70})",
    r"(?i)n\.\s*\d+\s+unit\w*\s+di\s+([A-Za-zÀ-ù /'\-]{5,70})",
    r"(?i)incarico[^,.]{0,60}?\s+a\s+([A-Za-zÀ-ù /'\-]{5,70})",
    r"(?i)per\s+la\s+copertura[^,.]{0,40}?\s+di\s+([A-Za-zÀ-ù /'\-]{5,70})",
]


def profilo_da_titolo(titolo):
    for schema in PROFILO_NEL_TITOLO:
        m = re.search(schema, titolo or "")
        if not m:
            continue
        p = re.sub(r"\s+", " ", m.group(1)).strip(" -/,;")
        p = re.sub(r"(?i)(personale|con|della|dell|delle|degli|per|area|"
                   r"disciplina|comma|ai sensi).*$", "", p).strip(" -/,;")
        if 4 < len(p) < 70:
            return p[:1].upper() + p[1:]
    return None


# Diversi enti pubblici liguri usano lo stesso componente per pubblicare i
# bandi ("publiccompetitions"): un solo lettore li serve tutti. Serve perche'
# InPA, che doveva raccoglierli, in pratica non li ha: verificato il
# 2026-09-08 con la ASL 3, i cui concorsi non compaiono affatto sul portale
# nazionale.
ENTI_PUBBLICI = [
    ("ASL 3 Genovese", "concorsi",
     "https://www.asl3.liguria.it/amministrazione-trasparente/bandi-di-concorso/"
     "concorsi-aperti/publiccompetitions/"),
    ("ASL 3 Genovese", "avvisi pubblici",
     "https://www.asl3.liguria.it/amministrazione-trasparente/bandi-di-concorso/"
     "avvisi-pubblici/publiccompetitions/"),
    ("ASL 3 Genovese", "mobilita",
     "https://www.asl3.liguria.it/amministrazione-trasparente/bandi-di-concorso/"
     "mobilit%C3%A0/publiccompetitions/"),
    # Policlinico San Martino: il suo sito ha otto sezioni. Si prendono solo
    # quelle aperte all'esterno e pertinenti; restano fuori gli avvisi interni,
    # i bandi riservati ai dipendenti, gli incarichi di direzione sanitaria,
    # quelli per le categorie protette e i corsi di laurea, che non sono lavoro.
    ("Policlinico San Martino", "avvisi pubblici",
     "https://www.ospedalesanmartino.it/it/amministrazione-trasparente/"
     "bandi-di-concorso-trasparenza/avvisi-pubblici/publiccompetitions/"),
    ("Policlinico San Martino", "bandi PNRR",
     "https://www.ospedalesanmartino.it/it/amministrazione-trasparente/"
     "bandi-di-concorso-trasparenza/bandi-pnrr/publiccompetitions/"),
    ("Policlinico San Martino", "collaborazioni",
     "https://www.ospedalesanmartino.it/it/amministrazione-trasparente/"
     "bandi-di-concorso-trasparenza/selezioni-co-co-co/publiccompetitions/"),
    # Regione Liguria non e' in elenco: la sua pagina "bandi e avvisi" contiene
    # concessioni idriche e gare d'appalto, non offerte di lavoro. I suoi
    # concorsi passano invece da InPA (verificato).
]


@fonte("Enti pubblici liguri")
def fonte_enti_pubblici():
    def uno(voce):
        ente, sezione, url = voce
        try:
            h = http(url, timeout=45)
        except Exception:
            return []
        # Si parte dall'intestazione della sezione "contenuti attivi" e si
        # prende quello che viene dopo. Cercare la parola "archivio" per
        # tagliare non funziona: nel menu del sito c'e' "Archivio Newsletter"
        # e il taglio cancellava l'intera pagina.
        inizio = re.search(r"pc_item_section[^>]*>[^<]{0,120}attiv", h, re.I)
        testa = h[inizio.start():] if inizio else h
        trovati = []
        for blocco in re.split(r"pc_latest_item", testa)[1:]:
            blocco = blocco[:3000]
            m = re.search(r"bando_link'?\"?\s+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
                          blocco, re.S | re.I)
            if not m:
                continue
            titolo = pulisci(m.group(2))
            if len(titolo) < 15:
                continue
            scad = None
            ms = re.search(r"(?i)scadenz\w*[^0-9]{0,40}(\d{2})[/-](\d{2})[/-](\d{4})",
                           pulisci(blocco))
            if ms:
                scad = "%s-%s-%s" % (ms.group(3), ms.group(2), ms.group(1))
            if scad and (giorni_a(scad) or 0) < 0:
                continue                      # gia' scaduto
            link = m.group(1)
            if link.startswith("/"):
                link = re.match(r"(https?://[^/]+)", url).group(1) + link
            trovati.append({
                "id": "pub-" + re.sub(r"\W+", "", link)[-34:],
                "titolo": profilo_da_titolo(titolo) or titolo,
                "titolo_ufficiale": titolo if profilo_da_titolo(titolo) else None,
                "ente": ente,
                "luogo": "Genova",
                "settore": "pubblico",
                "fonte": ente,
                "url": link,
                "pubblicato": None,
                "scadenza": scad,
                "descrizione": titolo + " " + sezione,
            })
        return trovati

    out, visti = [], set()
    with ThreadPoolExecutor(max_workers=4) as ex:
        for lista in ex.map(uno, ENTI_PUBBLICI):
            for a in lista:
                if a["id"] in visti:
                    continue
                visti.add(a["id"])
                out.append(a)
    return out


@fonte("Browser automatico")
def fonte_browser():
    """Legge quello che ha raccolto raccogli_browser.py, se e' stato eseguito.

    Non lancia il browser da qui: e' un passo separato, cosi' se il browser
    si rompe il pannello si costruisce lo stesso con tutte le altre fonti."""
    percorso = os.path.join(QUI, "annunci_browser.json")
    if not os.path.exists(percorso):
        return []
    d = json.load(io.open(percorso, encoding="utf-8"))
    # se il file e' vecchio di piu' di tre giorni meglio ignorarlo: quegli
    # annunci potrebbero essere gia' chiusi
    if (time.time() - os.path.getmtime(percorso)) > 3 * 86400:
        return []
    return d.get("annunci", [])


# -------------------------------------------------------------- costruzione --

def chiave_dedup(a):
    """Uno stesso annuncio arriva spesso da piu' fonti, e l'azienda e' scritta
    in modi diversi (Costa Crociere / Carnival Corporation). Se il titolo e'
    lungo e specifico basta quello; se e' generico ('Project Manager') serve
    anche l'azienda, altrimenti si fondono offerte diverse."""
    t = re.sub(r"[^a-z0-9 ]", "", senza_accenti(a.get("titolo") or ""))
    t = re.sub(r"\s+", " ", t).strip()[:70]
    if len(t.split()) >= 4:
        return t
    e = re.sub(r"[^a-z0-9]", "", senza_accenti(a.get("ente") or ""))[:18]
    return t + "|" + e


def main():
    print("Raccolta in corso...\n")
    funzioni = [fonte_inpa, fonte_liguria, fonte_adzuna, fonte_leonardo,
                fonte_msc, fonte_costa, fonte_rina,
                fonte_circle, fonte_nttdata, fonte_softjam,
                fonte_sogegross, fonte_grendi, fonte_liguria_digitale,
                fonte_poste, fonte_hitachi, fonte_enel, fonte_randstad, fonte_cosulich, fonte_enti_pubblici, fonte_browser]
    grezzi = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(lambda f: f(), funzioni):
            grezzi.extend(res)

    for e in FONTI_ESITO:
        stato = "ok " if e["ok"] else "KO "
        print("  %s %-34s %4d annunci  (%ss)%s" % (
            stato, e["fonte"], e["n"], e["secondi"],
            "  " + e.get("errore", "") if not e["ok"] else ""))

    # dedup
    visti, annunci = {}, []
    for a in grezzi:
        k = chiave_dedup(a)
        if k in visti:
            visti[k].setdefault("anche_su", []).append(a["fonte"])
            continue
        visti[k] = a
        annunci.append(a)

    # punteggio
    tenuti, esclusi = [], []
    for a in annunci:
        punti, dett, motivo_escl = valuta(a)
        if motivo_escl:
            a["escluso_perche"] = motivo_escl
            esclusi.append(a)
            continue
        aggiungi_stipendio(a)
        for chiave in ("stipendio_min", "stipendio_max"):
            v = a.get(chiave)
            try:
                v = int(float(v)) if v else None
            except (TypeError, ValueError):
                v = None
            a[chiave] = v if (v and 8000 <= v <= 250000) else None
        if a.get("stipendio_min"):
            dett = dett or {}
            dett.setdefault("pro", []).append(
                "stipendio dichiarato: %s euro" % format(a["stipendio_min"], ",d").replace(",", "."))
        a["punteggio"] = punti
        a["dettaglio"] = dett
        a["contratto"] = (dett or {}).get("contratto", "non indicato")
        a["remoto"] = (dett or {}).get("remoto", False)
        # i tempi determinati vanno in coda, come concordato
        a["secondario"] = a["contratto"] in ("determinato", "somministrazione")
        tenuti.append(a)

    # storico: cosa e' nuovo
    percorso_storico = os.path.join(QUI, "storico.json")
    storico = {}
    if os.path.exists(percorso_storico):
        try:
            storico = json.load(io.open(percorso_storico, encoding="utf-8"))
        except Exception:
            storico = {}
    # Lo storico ha due parti: quando ogni annuncio e' stato visto la prima
    # volta, e la data dell'esecuzione precedente. Serve la seconda perche'
    # "nuovo" deve voler dire "comparso dall'ultimo aggiornamento", non
    # "comparso negli ultimi sette giorni": altrimenti nella prima settimana
    # di vita del pannello risulta nuovo tutto quanto.
    if "visti" in storico and isinstance(storico.get("visti"), dict):
        visti = storico["visti"]
        esecuzione_precedente = storico.get("ultima_esecuzione")
    else:
        visti = {k: v for k, v in storico.items() if isinstance(v, str)}
        esecuzione_precedente = None

    oggi_s = OGGI.strftime("%Y-%m-%d")
    prima_volta_in_assoluto = not visti   # alla prima esecuzione e' tutto nuovo: inutile dirlo
    nuovi = 0
    for a in tenuti:
        k = chiave_dedup(a)
        if k not in visti:
            visti[k] = oggi_s
        a["visto_la_prima_volta"] = visti[k]
        giorni = giorni_da(visti[k])
        # per il pannello: comparso dall'ultimo aggiornamento in poi
        if prima_volta_in_assoluto:
            a["nuovo"] = False
        elif esecuzione_precedente:
            a["nuovo"] = visti[k] > esecuzione_precedente
        else:
            a["nuovo"] = visti[k] == oggi_s
        # per la mail del lunedi': comparso negli ultimi sette giorni
        a["nuovo_settimana"] = (not prima_volta_in_assoluto
                                and giorni is not None and giorni <= 7)

    json.dump({"ultima_esecuzione": oggi_s, "visti": visti},
              io.open(percorso_storico, "w", encoding="utf-8"),
              ensure_ascii=False, indent=0)

    tenuti.sort(key=lambda a: (a["secondario"], -a["punteggio"],
                               a.get("scadenza") or "9999"))

    # sotto questa soglia l'annuncio finisce fra i "meno pertinenti" e non
    # entra nei conteggi in cima al pannello, altrimenti i numeri mentono
    for a in tenuti:
        a["marginale"] = a["punteggio"] < SOGLIA_MARGINALE

    principali = [a for a in tenuti if not a["marginale"]]
    pubblici = [a for a in principali if a["settore"] == "pubblico"]
    privati = [a for a in principali if a["settore"] == "privato"]
    in_scadenza = [a for a in pubblici
                   if (giorni_a(a.get("scadenza")) or 999) <= 12]
    nuovi = sum(1 for a in principali if a.get("nuovo"))

    dati = {
        "aggiornato": OGGI.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "aggiornato_it": OGGI.strftime("%d/%m/%Y alle %H:%M"),
        "totale": len(tenuti),
        "n_principali": len(principali),
        "n_marginali": len(tenuti) - len(principali),
        "nuovi": nuovi,
        "n_privati": len(privati),
        "n_pubblici": len(pubblici),
        "n_scadenza": len(in_scadenza),
        "esclusi": len(esclusi),
        "motivi_esclusione": {},
        "fonti": FONTI_ESITO,
        "annunci": tenuti,
    }
    for a in esclusi:
        m = a["escluso_perche"]
        dati["motivi_esclusione"][m] = dati["motivi_esclusione"].get(m, 0) + 1

    json.dump(dati, io.open(os.path.join(QUI, "dati.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    print("\n  Annunci raccolti: %d  -> dopo deduplica: %d  -> tenuti: %d  (esclusi %d)"
          % (len(grezzi), len(annunci), len(tenuti), len(esclusi)))
    print("  Motivi di esclusione: %s" % dati["motivi_esclusione"])
    print("  Principali %d (privati %d, concorsi %d) | meno pertinenti %d | nuovi %d | in scadenza %d"
          % (len(principali), len(privati), len(pubblici), len(tenuti)-len(principali), nuovi, len(in_scadenza)))
    print("\n  I 12 piu' in linea:")
    for a in tenuti[:12]:
        print("   %3d  %-52s | %-24s | %s" % (
            a["punteggio"], a["titolo"][:52], (a["ente"] or "")[:24], a["fonte"]))
    return dati


if __name__ == "__main__":
    main()
