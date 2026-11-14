# Schemi XSD dell'IPdA

Cartella degli schemi caricati dal conservatore, una sottocartella per versione
(es. `uni11386-2020/`). È vuota finché il committente non acquista la UNI 11386:2020
con il suo XSD (decisione D03, VD-N-01, criticità VD-C-071).

Istruzioni: README, sezione «Schema UNI 11386 (IPdA)». In breve:

```bash
mkdir -p libs/sincro/schemas/uni11386-2020
cp <cartella-dell'acquisto>/* libs/sincro/schemas/uni11386-2020/
python tools/sincro/verifica_ipda.py impronte --cartella libs/sincro/schemas/uni11386-2020
# copiare le impronte in libs/sincro/schemi_ufficiali.py (revisione + decisione VD-N)
python tools/sincro/verifica_ipda.py registra --cartella libs/sincro/schemas/uni11386-2020 \
    --principale <file-principale>.xsd --versione uni11386-2020 --ufficiale \
    --fonte "UNI 11386:2020, copia acquistata il <data>"
make conformita-ipda
```

Non mettere qui schemi di prova: quelli dei test stanno nei test e sono marcati
NON ufficiali.
