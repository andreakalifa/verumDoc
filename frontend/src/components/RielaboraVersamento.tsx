// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * R07 (richiesta del committente): «Rielabora» di un proprio versamento rifiutato,
 * eseguita direttamente dall'ente (ADMIN_TENANT e OPERATORE; il conservatore la
 * mantiene anche nella coda di scarto). Stesse regole del servizio: anteprima del
 * piano, motivo obbligatorio, conferma con l'impronta del piano.
 */
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { anteprimaRielaboraVersamento, applicaRielaboraVersamento } from "../api/client";
import { leggiErrore, motivoErrore } from "../api/errori";
import { useAuth } from "../auth/AuthContext";

const MOTIVO_MIN = 10;
const RUOLI = ["ADMIN_TENANT", "OPERATORE", "SUPER_ADMIN", "RSC_CONSERVATORE"];

export function RielaboraVersamento({ pdvId }: { pdvId: string }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [piano, setPiano] = useState<any>(null);
  const [motivo, setMotivo] = useState("");
  const anteprima = useMutation({
    mutationFn: () => anteprimaRielaboraVersamento(pdvId),
    onSuccess: setPiano,
  });
  const applica = useMutation({
    mutationFn: () => applicaRielaboraVersamento(pdvId, piano.impronta_piano, motivo.trim()),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["versamenti"] }),
    onError: (e: any) => {
      const nuovo = e?.response?.data?.dettagli?.piano;
      if (leggiErrore(e).codice === "DSH-409-PIANO" && nuovo) setPiano(nuovo);
    },
  });
  if (!user || !RUOLI.includes(user.ruolo)) return null;
  const id = `vd-rielabora-${pdvId}`;

  if (applica.isSuccess) {
    return <p className="vd-sotto" role="status">Versamento di nuovo in lavorazione.</p>;
  }
  if (!piano) {
    return (
      <>
        <button type="button" className="vd-comando-testo" disabled={anteprima.isPending}
                onClick={() => anteprima.mutate()}>
          {anteprima.isPending ? "Verifica in corso…" : "Rielabora"}
        </button>
        {anteprima.isError && <span className="vd-sotto" role="alert">{motivoErrore(anteprima.error)}</span>}
      </>
    );
  }
  const elementi: any[] = Array.isArray(piano.elementi) ? piano.elementi : [];
  if (!piano.rielaborabili) {
    const motivi = [...elementi.map(e => e.motivo_blocco).filter(Boolean), ...(piano.avvisi ?? [])];
    return <span className="vd-sotto" role="status">Non rielaborabile: {motivi.join(" ") || "nessun messaggio da rielaborare"}</span>;
  }
  const avvisi: string[] = [...(piano.avvisi ?? []), ...elementi.flatMap(e => e.avvisi ?? [])];
  return (
    <form className="vd-sotto" onSubmit={e => { e.preventDefault(); applica.mutate(); }}>
      {avvisi.map((a, k) => <span key={k} className="vd-sotto">{a}</span>)}
      <label htmlFor={id} className="vd-sotto">Motivo della rielaborazione (almeno {MOTIVO_MIN} caratteri)</label>
      <input id={id} className="vd-input" value={motivo} maxLength={1000}
             onChange={e => setMotivo(e.target.value)} />
      <button type="submit" className="vd-bottone vd-bottone--secondario"
              disabled={motivo.trim().length < MOTIVO_MIN || applica.isPending}>
        {applica.isPending ? "Rielaborazione…" : "Conferma la rielaborazione"}
      </button>
      {applica.isError && <span className="vd-sotto" role="alert">{motivoErrore(applica.error, "operazione")}</span>}
    </form>
  );
}
