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

def http(url, data=None, headers=None, timeout=40):
    h = {"User-Agent": UA, "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
         "Accept-Language": "it-IT,it;q=0.9,en;q=0.8"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    r = urllib.request.urlopen(req, timeout=timeout, context=CTX)
    return r.read(3000000).decode(r.headers.get_content_charset() or "utf-8", "replace")


def http_json(url, body=None, headers=None):
    data = None
    h = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        h["Content-Type"] = "application/json"
    return json.loads(http(url, data=data, headers=h))


def pulisci(testo):
    if not testo:
        return ""
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", str(testo), flags=re.S | re.I)
    t = re.sub(r"<br\s*/?>|</p>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = (t.replace("&amp;", "&").replace("&nbsp;", " ").replace("&egrave;", "è")
         .replace("&agrave;", "à").replace("&ograve;", "ò").replace("&#39;", "'")
         .replace("&quot;", '"').replace("&rsquo;", "'").replace("&#x2019;", "'"))
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
        "specialista amministrativo", "affari generali"]),
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
            "descrizione": pulisci(" ".join(filter(None, [
                ufficiale, c.get("descrizioneBreve"), c.get("descrizione")])))[:2500],
        })
    return out


@fonte("Formazione Lavoro Regione Liguria")
def fonte_liguria():
    url = ("https://flguest.regione.liguria.it/services/api/DomandeLavoro"
           "?dataByOption=all&idProgramma=5&pageNumber=1&pageSize=500&stato=3")
    j = http_json(url)
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
            out.append({
                "id": "adzuna-" + rid,
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
                fonte_msc, fonte_costa, fonte_rina]
    grezzi = []
    with ThreadPoolExecutor(max_workers=7) as ex:
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
    oggi_s = OGGI.strftime("%Y-%m-%d")
    prima_volta_in_assoluto = not storico   # alla prima esecuzione e' tutto "nuovo": inutile dirlo
    nuovi = 0
    for a in tenuti:
        k = chiave_dedup(a)
        if k not in storico:
            storico[k] = oggi_s
            a["visto_la_prima_volta"] = oggi_s
            a["nuovo"] = not prima_volta_in_assoluto
            if a["nuovo"]:
                nuovi += 1
        else:
            a["visto_la_prima_volta"] = storico[k]
            a["nuovo"] = (giorni_da(storico[k]) or 99) <= 7
    json.dump(storico, io.open(percorso_storico, "w", encoding="utf-8"),
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
