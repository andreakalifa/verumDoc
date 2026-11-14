# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Modulo: libs/sincro — schemi e validazione dell'IPdA (UNI 11386, SInCRO).

R11 (D03, VD-C-071): ``validatore`` valida l'IPdA contro lo schema XSD caricato
dal conservatore in ``schemas/<versione>/`` (registrato con l'impronta SHA-256
di ogni file). Senza lo schema ufficiale l'esito è «schema ufficiale non
disponibile», mai «conforme». Il costruttore dell'IPdA sta nell'aip-builder
(``services/aip-builder/app/services/sincro_builder.py``).
"""
