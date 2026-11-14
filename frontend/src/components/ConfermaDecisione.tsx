// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * R17 (VD-P-164): conferma firmata delle decisioni sensibili. `useConfermaDecisione()` dà
 * `ottieni(richiesta)`: se la conferma è attiva calcola l'impronta del contenuto deciso e la
 * fa firmare al gateway; se serve una conferma dell'identità recente apre il dialogo
 * laterale (password e codice a tempo, o passkey; con solo SPID/CIE spiega di accedere di
 * nuovo) e, confermata, riprova. `dialogo` si rende in un portale sul body (il suo modulo
 * non finisce mai dentro il modulo della decisione). Ogni conferma dell'identità vale per una
 * sola decisione.
 */
import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { leggiErrore } from "../api/errori";
import { firmaPasskey } from "../console/accesso";
import { DialogoLaterale, InLettura } from "../console/comuni";
import {
  ConfermaAnnullata, chiediConferma, improntaContenuto, statoConferme,
  type ConfermaFirmata, type RichiestaConferma,
} from "../console/conferme";
import { confermaConPasskey, confermaConPassword, leggiCredenziali, opzioniConfermaPasskey } from "../console/credenziali";

type InAttesa = { r: RichiestaConferma; impronta: string; risolvi: (c: ConfermaFirmata) => void; rifiuta: (e: unknown) => void };

const DESCRIZIONE: Record<string, string> = {
  SCARTO_APPROVA: "rendere eseguibile lo scarto",
  SCARTO_ESEGUI: "eseguire lo scarto",
  RIVERSAMENTO_USCITA_ESEGUI: "eseguire il riversamento verso l'altro conservatore",
  ANNULLAMENTO_RICHIEDI: "chiedere l'annullamento del versamento",
  ANNULLAMENTO_APPROVA: "approvare l'annullamento",
  RETTIFICA_RICHIEDI: "chiedere la rettifica dei metadati",
  PRATICA_RIPRENDI: "riprendere la pratica sospesa",
};

export function useConfermaDecisione() {
  const stato = useQuery({ queryKey: ["conferme-stato"], queryFn: statoConferme, staleTime: 300_000 });
  const [inAttesa, setInAttesa] = useState<InAttesa | null>(null);
  const corrente = useRef<InAttesa | null>(null);

  async function ottieni(r: RichiestaConferma): Promise<ConfermaFirmata | undefined> {
    const s = stato.data ?? await statoConferme();
    if (!s.attiva) return undefined;
    const impronta = await improntaContenuto(r.azione, r.campi);
    try {
      return await chiediConferma(r, impronta);
    } catch (e) {
      if (leggiErrore(e).codice !== "ACC-403-RIAUTENTICAZIONE") throw e;
      return new Promise<ConfermaFirmata>((risolvi, rifiuta) => {
        const attesa = { r, impronta, risolvi, rifiuta };
        corrente.current = attesa;
        setInAttesa(attesa);
      });
    }
  }

  const chiudi = () => {
    const a = corrente.current;
    corrente.current = null;
    setInAttesa(null);
    a?.rifiuta(new ConfermaAnnullata());
  };
  const confermata = async () => {
    const a = corrente.current;
    if (!a) return;
    try {
      const c = await chiediConferma(a.r, a.impronta);
      corrente.current = null;
      setInAttesa(null);
      a.risolvi(c);
    } catch (e) {
      corrente.current = null;
      setInAttesa(null);
      a.rifiuta(e);
    }
  };

  const dialogo = inAttesa ? createPortal(
    <DialogoLaterale id="vd-conferma-decisione" titolo="Conferma la tua identità"
                     sottotitolo={`Per ${DESCRIZIONE[inAttesa.r.azione] ?? "questa decisione"}`} onClose={chiudi}>
      <ConfermaIdentita onConfermata={confermata} />
    </DialogoLaterale>, document.body,
  ) : null;
  return { ottieni, dialogo };
}

function Errore({ errore }: { errore: unknown }) {
  if (!errore) return null;
  const l = leggiErrore(errore);
  const testo = l.codice === "ACC-422-RIAUTENTICAZIONE" ? "Password, codice o passkey non corretti."
    : l.codice === "ACC-429" ? "Troppi tentativi: attendi qualche minuto e riprova."
    : l.testo || "Conferma non riuscita: riprova.";
  return <div className="vd-esito vd-esito--critico" role="alert"><p className="vd-esito-titolo">Non è andata</p><p>{testo}</p></div>;
}

function ConfermaIdentita({ onConfermata }: { onConfermata: () => Promise<void> }) {
  const q = useQuery({ queryKey: ["credenziali"], queryFn: leggiCredenziali });
  const [password, setPassword] = useState("");
  const [codice, setCodice] = useState("");
  const [recupero, setRecupero] = useState(false);
  const conPassword = useMutation({
    mutationFn: () => confermaConPassword(password, recupero ? { codice_recupero: codice } : { codice }),
    onSuccess: async () => { setPassword(""); setCodice(""); await onConfermata(); },
    onError: () => { setPassword(""); setCodice(""); },
  });
  const conPasskey = useMutation({
    mutationFn: async () => confermaConPasskey(await firmaPasskey((await opzioniConfermaPasskey()).opzioni)),
    onSuccess: onConfermata,
  });
  if (q.isLoading) return <InLettura testo="Lettura dei metodi di conferma…" />;
  const metodi = q.data?.riautenticazione.metodi ?? [];
  return (
    <section aria-labelledby="vd-conf-dec-testo">
      <p id="vd-conf-dec-testo" className="vd-nota">
        È una decisione che il servizio esegue senza ritorno: il gateway la firma solo dopo una conferma della tua identità in questa sessione. La conferma vale per questa sola decisione.
      </p>
      {metodi.length === 0 ? (
        <div className="vd-esito" role="status" style={{ marginTop: 16 }}>
          <p className="vd-esito-titolo">Accedi di nuovo</p>
          <p>Il tuo ente ammette solo SPID o CIE: esci, accedi di nuovo con la tua identità digitale e ripeti la decisione entro pochi minuti dall'accesso.</p>
        </div>
      ) : null}
      {metodi.includes("PASSWORD_TOTP") && (
        <form className="vd-modulo" style={{ marginTop: 16 }} onSubmit={e => { e.preventDefault(); conPassword.mutate(); }}>
          <div className="vd-campo">
            <label htmlFor="vd-conf-dec-pw">Password</label>
            <input id="vd-conf-dec-pw" type="password" className="vd-input" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} />
          </div>
          <div className="vd-campo">
            <label htmlFor="vd-conf-dec-codice">{recupero ? "Codice di recupero" : "Codice a sei cifre dell'app"}</label>
            <input id="vd-conf-dec-codice" className="vd-input vd-mono" autoComplete="one-time-code" inputMode={recupero ? "text" : "numeric"}
                   maxLength={recupero ? 19 : 6} value={codice}
                   onChange={e => setCodice(recupero ? e.target.value.toUpperCase() : e.target.value.replace(/\D/g, ""))} />
            <button type="button" className="vd-comando-testo" onClick={() => { setRecupero(!recupero); setCodice(""); }}>
              {recupero ? "Uso il codice a tempo" : "Non ho il telefono: uso un codice di recupero"}
            </button>
          </div>
          <div className="vd-comandi">
            <button type="submit" className="vd-bottone" disabled={conPassword.isPending || !password || (recupero ? codice.length < 16 : codice.length !== 6)}>
              {conPassword.isPending ? "Verifica…" : "Conferma e decidi"}
            </button>
          </div>
          <Errore errore={conPassword.error} />
        </form>
      )}
      {metodi.includes("PASSKEY") && (
        <div className="vd-comandi" style={{ marginTop: 16 }}>
          <button type="button" className={metodi.includes("PASSWORD_TOTP") ? "vd-bottone vd-bottone--secondario" : "vd-bottone"}
                  disabled={conPasskey.isPending} onClick={() => conPasskey.mutate()}>
            {conPasskey.isPending ? "In attesa del dispositivo…" : "Conferma con una passkey"}
          </button>
          <Errore errore={conPasskey.error} />
        </div>
      )}
    </section>
  );
}
