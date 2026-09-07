# -*- coding: utf-8 -*-
"""
Manda il riepilogo del lunedi' via email.

Le credenziali arrivano da variabili d'ambiente, mai dal codice:
  EMAIL_MITTENTE   indirizzo Gmail che spedisce
  EMAIL_PASSWORD   "password per le app" di Google (non la password normale)
  EMAIL_DESTINATARIO  a chi arriva il riepilogo
  INDIRIZZO_PANNELLO  link al pannello, da mettere nella mail

Uso:  python manda_email.py
Se mancano le credenziali stampa l'anteprima e non spedisce nulla.
"""
import io
import json
import os
import smtplib
import sys
from email.message import EmailMessage
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

QUI = os.path.dirname(os.path.abspath(__file__))
DATI = json.load(io.open(os.path.join(QUI, "dati.json"), encoding="utf-8"))

MITTENTE = os.environ.get("EMAIL_MITTENTE")
PASSWORD = os.environ.get("EMAIL_PASSWORD")
DESTINATARIO = os.environ.get("EMAIL_DESTINATARIO", "")
LINK = os.environ.get("INDIRIZZO_PANNELLO", "")


def giorni_a(iso):
    if not iso:
        return None
    try:
        return (datetime.strptime(iso, "%Y-%m-%d") - datetime.now()).days
    except Exception:
        return None


def costruisci():
    annunci = [a for a in DATI["annunci"] if not a.get("marginale")]
    nuovi = [a for a in annunci if a.get("nuovo")]
    nuovi.sort(key=lambda a: -a["punteggio"])
    scadenza = [a for a in annunci if a["settore"] == "pubblico"
                and (giorni_a(a.get("scadenza")) or 999) <= 12]
    scadenza.sort(key=lambda a: a.get("scadenza") or "9999")
    migliori = sorted(annunci, key=lambda a: -a["punteggio"])[:5]

    if nuovi:
        oggetto = "Lavoro Genova: %d nuovi annunci questa settimana" % len(nuovi)
    else:
        oggetto = "Lavoro Genova: nessuna novità questa settimana"

    righe = []
    righe.append("<div style=\"font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;"
                 "max-width:640px;margin:0 auto;color:#1c2128;line-height:1.5\">")
    righe.append("<h2 style=\"font-size:20px;margin:0 0 4px\">Offerte di lavoro &middot; Genova</h2>")
    righe.append("<p style=\"color:#5c6773;font-size:13px;margin:0 0 18px\">Riepilogo del %s</p>"
                 % DATI["aggiornato_it"])

    def blocco(titolo, lista, mostra_scadenza=False):
        if not lista:
            return
        righe.append("<h3 style=\"font-size:15px;margin:22px 0 8px;padding-bottom:5px;"
                     "border-bottom:1px solid #e2e6eb\">%s</h3>" % titolo)
        for a in lista[:12]:
            extra = ""
            if mostra_scadenza and a.get("scadenza"):
                g = giorni_a(a["scadenza"])
                extra = ("<span style=\"color:#b42318;font-weight:600\"> &middot; scade fra %d giorni</span>"
                         % g) if g is not None else ""
            righe.append(
                "<div style=\"margin:0 0 12px;padding:10px 12px;border:1px solid #e2e6eb;"
                "border-radius:9px\">"
                "<div style=\"font-weight:600;font-size:14.5px\">"
                "<a href=\"%s\" style=\"color:#1f3864;text-decoration:none\">%s</a></div>"
                "<div style=\"color:#5c6773;font-size:12.5px;margin-top:2px\">"
                "%s &middot; %s &middot; compatibilit&agrave; %d/100%s</div></div>"
                % (a.get("url", "#"), a.get("titolo", ""), a.get("ente", ""),
                   a.get("luogo", ""), a.get("punteggio", 0), extra))

    blocco("Nuovi questa settimana (%d)" % len(nuovi), nuovi)
    blocco("Concorsi in scadenza", scadenza, mostra_scadenza=True)
    if not nuovi:
        blocco("I più in linea, già visti", migliori)

    if LINK:
        righe.append("<p style=\"margin:24px 0\"><a href=\"%s\" style=\"background:#1f3864;"
                     "color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none;"
                     "font-size:14px;display:inline-block\">Apri il pannello completo</a></p>" % LINK)
    righe.append("<p style=\"color:#8a929c;font-size:11.5px;margin-top:24px;"
                 "border-top:1px solid #e2e6eb;padding-top:12px\">"
                 "In totale %d annunci attivi: %d privati e %d concorsi. "
                 "Questo messaggio parte da solo ogni luned&igrave;.</p>"
                 % (DATI.get("n_principali", 0), DATI["n_privati"], DATI["n_pubblici"]))
    righe.append("</div>")
    return oggetto, "\n".join(righe)


def main():
    oggetto, html = costruisci()
    if not MITTENTE or not PASSWORD:
        print("Credenziali email assenti: non spedisco.")
        print("Oggetto sarebbe: " + oggetto)
        anteprima = os.path.join(QUI, "anteprima_email.html")
        io.open(anteprima, "w", encoding="utf-8").write(html)
        print("Anteprima salvata in " + anteprima)
        return
    if not DESTINATARIO:
        print("Manca EMAIL_DESTINATARIO: non spedisco.")
        return
    msg = EmailMessage()
    msg["Subject"] = oggetto
    msg["From"] = MITTENTE
    msg["To"] = DESTINATARIO
    msg.set_content("Riepilogo settimanale delle offerte di lavoro a Genova. "
                    "Apri il pannello: " + (LINK or "(link non configurato)"))
    msg.add_alternative(html, subtype="html")
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(MITTENTE, PASSWORD)
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        # L'errore piu' probabile, e quello che e' capitato davvero:
        # nel segreto c'e' la password normale di Google invece della
        # "password per le app". Meglio spiegarlo che stampare un tracciato.
        testo = str(e)
        print("NON SONO RIUSCITO A SPEDIRE: Gmail ha rifiutato le credenziali.")
        if "Application-specific password" in testo or "5.7.9" in testo:
            print("")
            print("  Nel segreto EMAIL_PASSWORD c'e' la password normale dell'account.")
            print("  Gmail non la accetta dai programmi: serve una 'password per le app',")
            print("  16 lettere che si generano su myaccount.google.com/apppasswords")
            print("  (richiede la verifica in due passaggi attiva sull'account).")
            print("  Vanno incollate senza spazi nel segreto EMAIL_PASSWORD.")
        else:
            print("  Risposta del server: " + testo[:300])
        print("")
        print("Il pannello si e' aggiornato lo stesso: manca solo la mail.")
        raise SystemExit(2)
    except Exception as e:
        print("NON SONO RIUSCITO A SPEDIRE: %s: %s" % (type(e).__name__, str(e)[:300]))
        print("Il pannello si e' aggiornato lo stesso: manca solo la mail.")
        raise SystemExit(2)
    print("Email inviata a " + DESTINATARIO + " -> " + oggetto)


if __name__ == "__main__":
    main()
