// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
// Oracolo esterno per libs/catena (REGOLE_SVILUPPO §23.5): verifica con il
// pacchetto Go golang.org/x/mod/sumdb (note e tlog, implementazione di
// riferimento delle note firmate e degli alberi RFC 6962) i dati prodotti da
// Python, e produce dati Go che Python deve accettare.
//
// Ingresso (stdin, JSON): {"foglie": [hex dati di foglia...], "verificatore":
// "<verifier key>", "nota": "<nota firmata>", "privata_go": "PRIVATE+KEY+...",
// "testo_go": "<testo da firmare>", "prove_inclusione": [{"i","n","percorso"}],
// "prove_consistenza": [{"da","a","percorso"}]}.
// Uscita (stdout, JSON): {"nota_valida", "firme", "nota_go", "radici": [hex per n],
// "inclusione_ok": [bool], "consistenza_ok": [bool], "inclusione_go", "consistenza_go"}.
package main

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"

	"golang.org/x/mod/sumdb/note"
	"golang.org/x/mod/sumdb/tlog"
)

type provaI struct {
	I        int64    `json:"i"`
	N        int64    `json:"n"`
	Percorso []string `json:"percorso"`
}

type provaC struct {
	Da       int64    `json:"da"`
	A        int64    `json:"a"`
	Percorso []string `json:"percorso"`
}

type ingresso struct {
	Foglie           []string `json:"foglie"`
	Verificatore     string   `json:"verificatore"`
	Nota             string   `json:"nota"`
	PrivataGo        string   `json:"privata_go"`
	TestoGo          string   `json:"testo_go"`
	ProveInclusione  []provaI `json:"prove_inclusione"`
	ProveConsistenza []provaC `json:"prove_consistenza"`
}

type memoria []tlog.Hash

func (m memoria) ReadHashes(indici []int64) ([]tlog.Hash, error) {
	out := make([]tlog.Hash, len(indici))
	for i, x := range indici {
		out[i] = m[x]
	}
	return out, nil
}

func hashes(valori []string) []tlog.Hash {
	out := make([]tlog.Hash, len(valori))
	for i, v := range valori {
		b, err := hex.DecodeString(v)
		if err != nil || len(b) != 32 {
			panic("hash non valido")
		}
		copy(out[i][:], b)
	}
	return out
}

func esad(h []tlog.Hash) []string {
	out := make([]string, len(h))
	for i, x := range h {
		out[i] = hex.EncodeToString(x[:])
	}
	return out
}

func main() {
	var in ingresso
	if err := json.NewDecoder(os.Stdin).Decode(&in); err != nil {
		panic(err)
	}
	uscita := map[string]any{}

	v, err := note.NewVerifier(in.Verificatore)
	if err != nil {
		panic(err)
	}
	n, err := note.Open([]byte(in.Nota), note.VerifierList(v))
	uscita["nota_valida"] = err == nil
	firme := []string{}
	if err == nil {
		for _, s := range n.Sigs {
			firme = append(firme, s.Name)
		}
	}
	uscita["firme"] = firme

	s, err := note.NewSigner(in.PrivataGo)
	if err != nil {
		panic(err)
	}
	firmata, err := note.Sign(&note.Note{Text: in.TestoGo}, s)
	if err != nil {
		panic(err)
	}
	uscita["nota_go"] = string(firmata)

	// Albero tlog costruito dai dati di foglia
	var m memoria
	for i, f := range in.Foglie {
		dati, err := hex.DecodeString(f)
		if err != nil {
			panic(err)
		}
		nuovi, err := tlog.StoredHashes(int64(i), dati, m)
		if err != nil {
			panic(err)
		}
		m = append(m, nuovi...)
	}
	radici := []string{}
	for k := int64(0); k <= int64(len(in.Foglie)); k++ {
		h, err := tlog.TreeHash(k, m)
		if err != nil {
			panic(err)
		}
		radici = append(radici, hex.EncodeToString(h[:]))
	}
	uscita["radici"] = radici

	inclOk, inclGo := []bool{}, [][]string{}
	for _, p := range in.ProveInclusione {
		th, _ := tlog.TreeHash(p.N, m)
		dati, _ := hex.DecodeString(in.Foglie[p.I])
		err := tlog.CheckRecord(tlog.RecordProof(hashes(p.Percorso)), p.N, th, p.I, tlog.RecordHash(dati))
		inclOk = append(inclOk, err == nil)
		prova, err := tlog.ProveRecord(p.N, p.I, m)
		if err != nil {
			panic(err)
		}
		inclGo = append(inclGo, esad(prova))
	}
	uscita["inclusione_ok"] = inclOk
	uscita["inclusione_go"] = inclGo

	consOk, consGo := []bool{}, [][]string{}
	for _, p := range in.ProveConsistenza {
		h1, _ := tlog.TreeHash(p.Da, m)
		h2, _ := tlog.TreeHash(p.A, m)
		err := tlog.CheckTree(tlog.TreeProof(hashes(p.Percorso)), p.A, h2, p.Da, h1)
		consOk = append(consOk, err == nil)
		prova, err := tlog.ProveTree(p.A, p.Da, m)
		if err != nil {
			panic(err)
		}
		consGo = append(consGo, esad(prova))
	}
	uscita["consistenza_ok"] = consOk
	uscita["consistenza_go"] = consGo

	if err := json.NewEncoder(os.Stdout).Encode(uscita); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
