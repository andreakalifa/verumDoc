# VerumDoc — Makefile
# Shortcuts per operazioni comuni di sviluppo
# Uso: make <target>
#
# Ambienti Python per servizio (VD-C-030): ogni servizio gira con il PROPRIO
# interprete, scelto in quest'ordine:
#   1. variabile PY_<servizio> passata a make  (es. make test PY_onboarding=/percorso/venv/bin/python)
#   2. services/<servizio>/.venv/bin/python    (creato con: make venv SVC=<servizio>)
#   3. $(PYTHON) (predefinito: python)
# I target che scorrono tutti i servizi NON fermano il giro al primo errore, ma
# terminano con codice di uscita 1 se almeno un servizio fallisce, con il riepilogo.

.PHONY: help dev-up dev-down dev-restart logs logs-svc health health-db health-kafka \
	kafka-topics test test-servizi test-libs docs-api test-svc test-watch lint fmt type-check security quality \
	venv venv-all db-migrate db-shell db-reset clean install verifiche lint-critico \
	carico-locale carico-k6 verifiche-infra

SERVICES := gateway validation signature aip-builder hashchain distribution notification onboarding dashboard-api sftp-ingest testimone ciclo-vita
PYTHON   ?= python

# Interprete del servizio $(1) (vedi intestazione)
py_for = $(or $(PY_$(1)),$(if $(wildcard services/$(1)/.venv/bin/python),$(CURDIR)/services/$(1)/.venv/bin/python),$(PYTHON))

# Esegue il comando $(2) dentro services/<svc> per ogni servizio, con il suo
# interprete in $$PY; raccoglie i servizi falliti ed esce con errore se ce ne sono.
# Uso: $(call per_servizio,<etichetta>,<comando shell che usa $$PY>)
define per_servizio
	@falliti=""; \
	$(foreach svc,$(SERVICES),\
	echo "$(YELLOW)── $(1) $(svc) [$(call py_for,$(svc))]$(RESET)"; \
	if ! ( cd services/$(svc) && PY="$(call py_for,$(svc))" && $(2) ); then falliti="$$falliti $(svc)"; fi; ) \
	if [ -n "$$falliti" ]; then \
		echo "$(RED)$(1): FALLITO per:$$falliti$(RESET)"; exit 1; \
	else \
		echo "$(GREEN)$(1): OK su tutti i servizi$(RESET)"; \
	fi
endef

# Colori output
GREEN  := \033[0;32m
YELLOW := \033[0;33m
RED    := \033[0;31m
RESET  := \033[0m

help: ## Mostra questo help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  $(GREEN)%-20s$(RESET) %s\n", $$1, $$2}'

# ── Ambienti Python ───────────────────────────────────────────────
venv: ## Crea services/<SVC>/.venv e installa il servizio con le dipendenze dev (make venv SVC=gateway)
	@test -n "$(SVC)" || { echo "$(RED)Indicare SVC=<servizio>$(RESET)"; exit 1; }
	$(PYTHON) -m venv services/$(SVC)/.venv
	services/$(SVC)/.venv/bin/pip install -q --upgrade pip
	cd services/$(SVC) && .venv/bin/pip install -q -e ".[dev]"

venv-all: ## Crea un ambiente separato per ogni servizio
	@for svc in $(SERVICES); do $(MAKE) --no-print-directory venv SVC=$$svc || exit 1; done

# ── Ambiente locale ────────────────────────────────────────────────
dev-up: ## Avvia tutti i servizi locali (docker compose)
	@echo "$(GREEN)Avvio ambiente di sviluppo...$(RESET)"
	@# R04: un solo generatore (Python, anche su Windows) per tutti i segreti di sviluppo,
	@# compresi quelli della catena e del testimone (infra/dev_catena.env, infra/dev_testimone.env)
	@test -f infra/dev_jwt.env -a -f infra/dev_sftp.env -a -f infra/dev_webhook.env \
		-a -f infra/dev_catena.env -a -f infra/dev_testimone.env || $(PYTHON) tools/dev/genera_segreti_dev.py
	docker compose up -d
	@echo "$(GREEN)Attendendo che i servizi siano pronti...$(RESET)"
	@sleep 5
	@$(MAKE) --no-print-directory health

dev-down: ## Ferma tutti i servizi locali
	docker compose down

dev-restart: ## Riavvia i servizi locali
	docker compose restart

logs: ## Mostra log di tutti i servizi
	docker compose logs -f

logs-svc: ## Mostra log di un servizio specifico (make logs-svc SVC=gateway)
	docker compose logs -f $(SVC)

# ── Health check ───────────────────────────────────────────────────
# porta:percorso di salute reale di ogni servizio (porte del docker-compose,
# percorsi delle rotte /health nei main.py)
HEALTH_ENDPOINTS := \
	gateway=8000:/api/v1/health/live \
	validation=8001:/api/v1/health/live \
	signature=8003:/api/v1/health/live \
	hashchain=8004:/api/v1/health/live \
	aip-builder=8005:/api/v1/health/live \
	notification=8006:/api/v1/health/live \
	onboarding=8007:/api/v1/health/live \
	distribution=8008:/api/v2/health/live \
	dashboard-api=8009:/api/health/live \
	sftp-ingest=8010:/api/v1/health/live \
	testimone=8011:/api/v1/health/live \
	ciclo-vita=8012:/api/v1/health/live

health: ## Verifica che tutti i servizi rispondano (esce con errore se uno non risponde)
	@echo "$(GREEN)Health check servizi...$(RESET)"
	@ko=0; for ep in $(HEALTH_ENDPOINTS); do \
		nome=$${ep%%=*}; resto=$${ep#*=}; porta=$${resto%%:*}; percorso=$${resto#*:}; \
		if curl -sf "http://localhost:$$porta$$percorso" > /dev/null 2>&1; then \
			echo "  $(GREEN)✓$(RESET) $$nome localhost:$$porta$$percorso"; \
		else \
			echo "  $(RED)✗$(RESET) $$nome localhost:$$porta$$percorso (non raggiungibile)"; ko=1; \
		fi; \
	done; exit $$ko

health-db: ## Verifica connessione database
	@docker compose exec postgres pg_isready -U vd_user -d verumdoc_dev

health-kafka: ## Elenca i topic Kafka (immagine cp-kafka: il comando è kafka-topics)
	@docker compose exec kafka kafka-topics --bootstrap-server localhost:9092 --list

kafka-topics: ## Crea i topic Kafka da infra/kafka/topics.yaml (unica fonte)
	KAFKA_CONTAINER=vd-kafka bash infra/scripts/kafka_create_topics.sh local

# ── Test ───────────────────────────────────────────────────────────
# I test d'integrazione usano un PostgreSQL REALE migrato: VD_TEST_DATABASE_URL
# (es. postgresql+asyncpg://postgres@127.0.0.1:5432/vd_template, dopo
# `make db-migrate` con DATABASE_URL su quel database) usato come template; ogni
# test ne copia uno nuovo. Senza la variabile quei test sono SALTATI con motivo
# scritto (non falliscono): la suite verde «vera» è quella con la variabile.
test: ## Test di tutti i servizi (ognuno nel suo ambiente) + libs/shared + infra/kafka (errore se uno fallisce)
	@esito=0; \
	$(MAKE) --no-print-directory test-servizi || esito=1; \
	$(MAKE) --no-print-directory test-libs || esito=1; \
	exit $$esito

test-servizi: ## Solo i test dei servizi
	$(call per_servizio,test,$$PY -m pytest tests/ -q --tb=short -p no:cacheprovider)

# R04: libs/catena, infra/tests (storage S3 reale con VD_TEST_S3_URL), tools/carico
# R05: tools/validatore (validatore preventivo da riga di comando, contratto Q1)
# R07: tools/manutenzione (pulizia della cronologia Git, D09)
# R18: tools/opensource (controllo della pubblicazione open source, VD-P-190)
TEST_LIBS := libs/shared libs/verumdoc_rules $(wildcard libs/catena) libs/sincro infra/kafka infra/k8s \
	infra/tests tools/dev tools/docs tools/carico tools/validatore tools/sincro tools/profili tools/pulizia \
	tools/manutenzione tools/opensource sdk/tests tests/integrity

test-libs: ## Test di libs (shared, verumdoc_rules, catena), infra (kafka, k8s, storage), tools e sdk/tests
	@echo "$(YELLOW)── test $(TEST_LIBS) [$(PYTHON)]$(RESET)"
	$(PYTHON) -m pytest $(TEST_LIBS) -q --tb=short -p no:cacheprovider

conformita-ipda: ## R11 (D03): stato dello schema XSD UNI 11386 e test di conformità dell'IPdA (skip senza XSD ufficiale)
	$(PYTHON) tools/sincro/verifica_ipda.py stato; echo "stato schema: uscita $$? (0 ufficiale, 2 non disponibile)"
	cd services/aip-builder && $(PYTHON) -m pytest tests/test_r11_ipda.py -q -rs -p no:cacheprovider

docs-api: ## Rigenera OpenAPI unica, elenco endpoint, catalogo errori e cataloghi R14 (docs/catalogo)
	$(PYTHON) tools/docs/genera_openapi.py
	$(PYTHON) tools/docs/genera_catalogo_errori.py
	$(PYTHON) tools/docs/genera_cataloghi.py --solo-nuovi

verifiche: ## Controlli statici di coerenza: topic Kafka, values/manifest, OpenAPI/ingress, cataloghi, documentazione, tests/integrity, Dockerfile, pubblicazione, docker compose
	$(PYTHON) infra/kafka/verifica_topic.py
	$(PYTHON) infra/k8s/verifica_manifest.py
	$(PYTHON) tools/docs/genera_openapi.py --verifica
	$(PYTHON) tools/docs/genera_openapi.py --ingress
	$(PYTHON) tools/docs/genera_catalogo_errori.py --verifica --codici
	@# R14 (VD-C-082): cataloghi generati, documentazione coerente, suite di integrità
	$(PYTHON) tools/docs/genera_cataloghi.py --controlla --solo-nuovi
	@# R15 (VD-C-177): manuale, piano della sicurezza, guida, route e permessi generati
	$(PYTHON) tools/conformita/genera_documenti.py --controlla
	$(PYTHON) tools/docs/verifica_documentazione.py
	$(PYTHON) -m pytest tests/integrity -q -p no:cacheprovider
	@copia=$$(mktemp -d) && cp -r infra/k8s "$$copia/k8s" && \
	$(PYTHON) infra/k8s/genera_manifest.py >/dev/null && \
	if diff -r -x __pycache__ "$$copia/k8s" infra/k8s; then rm -rf "$$copia"; \
	else rm -rf "$$copia"; echo "$(RED)Manifest non allineati ai values: ora rigenerati, includerli nella modifica$(RESET)"; exit 1; fi
	$(PYTHON) tools/dev/verifica_dockerfile.py
	@# R18 (VD-P-190): intestazioni SPDX, segreti evidenti, riferimenti vietati, file ignorati tracciati
	$(PYTHON) tools/opensource/controlla_pubblicazione.py
	test -f infra/dev_jwt.env || $(PYTHON) tools/dev/genera_segreti_dev.py
	docker compose config -q

# ── Infrastruttura (R04) ───────────────────────────────────────────
TOFU ?= tofu
verifiche-infra: ## OpenTofu fmt/validate/test, helm lint e template, kustomize + kubeconform (strumenti installati)
	$(TOFU) fmt -check -recursive infra/terraform
	@for d in infra/terraform/modules/*/ infra/terraform/environments/*/; do \
		echo "── $$d"; (cd $$d && $(TOFU) init -backend=false -input=false -no-color >/dev/null && $(TOFU) validate -no-color) || exit 1; \
		if [ -d $$d/tests ]; then (cd $$d && $(TOFU) test -no-color) || exit 1; fi; \
	done
	@for r in prod staging testimone; do \
		echo "── helm $$r"; helm lint infra/helm -f infra/helm/values.yaml -f infra/helm/values.$$r.yaml >/dev/null || exit 1; \
		helm template verumdoc infra/helm -f infra/helm/values.yaml -f infra/helm/values.$$r.yaml \
			| kubeconform -strict -summary -ignore-missing-schemas - || exit 1; \
	done
	@for d in infra/k8s/base infra/k8s/overlays/staging infra/k8s/overlays/prod infra/k8s/testimone; do \
		echo "── kustomize $$d"; kustomize build $$d | kubeconform -strict -summary -ignore-missing-schemas - || exit 1; \
	done

# ── Prova di carico (R04, VD-C-081) ───────────────────────────────
# Locale: gateway reale + PostgreSQL (CARICO_DB, database DEDICATO migrato a head)
# + Redis + S3 moto; risultati in CARICO_USCITA. k6: ambiente reale (VD_URL, VD_TOKEN).
CARICO_DB ?= postgresql+asyncpg://postgres@127.0.0.1:5432/r04_carico
CARICO_LIVELLI ?= 10,25,50
CARICO_DURATA ?= 180
CARICO_USCITA ?= carico-risultati
carico-locale: ## Prova di carico locale del versamento (make carico-locale CARICO_DB=... CARICO_LIVELLI=10,25,50)
	$(call py_for,gateway) tools/carico/prova_locale.py --database-url "$(CARICO_DB)" \
		--livelli "$(CARICO_LIVELLI)" --durata "$(CARICO_DURATA)" --riavvia-s3 --uscita "$(CARICO_USCITA)"

carico-k6: ## Scenario k6 contro un ambiente reale (make carico-k6 VD_URL=https://... VD_TOKEN=...)
	k6 run -e VD_URL="$(VD_URL)" -e VD_TOKEN="$(VD_TOKEN)" -e RITMO="$(or $(RITMO),50)" \
		-e DURATA="$(or $(DURATA),1h)" tools/carico/k6/versamenti.js

test-svc: ## Test di un servizio specifico con coverage (make test-svc SVC=gateway)
	cd services/$(SVC) && $(call py_for,$(SVC)) -m pytest tests/ -v --cov=app --cov-report=term-missing

test-watch: ## Test in modalità watch (make test-watch SVC=gateway; richiede pytest-xdist)
	cd services/$(SVC) && $(call py_for,$(SVC)) -m pytest tests/ -f

# ── Qualità codice ─────────────────────────────────────────────────
lint: ## Lint di tutti i servizi (ruff)
	$(call per_servizio,lint,$$PY -m ruff check app/ tests/)

lint-critico: ## Errori che rompono l'esecuzione (sintassi, nomi non definiti) su tutto il repository
	$(PYTHON) -m ruff check --select E9,F63,F7,F82 --output-format concise .

fmt: ## Formatta tutto il codice (ruff format)
	$(call per_servizio,fmt,$$PY -m ruff format app/ tests/)

type-check: ## Type check mypy --strict su tutti i servizi
	$(call per_servizio,mypy,$$PY -m mypy app/ --strict --ignore-missing-imports)

security: ## Security scan (bandit)
	$(call per_servizio,bandit,$$PY -m bandit -r app/ -ll -q)

quality: lint type-check security ## Esegui lint + type-check + security scan

# ── Database ───────────────────────────────────────────────────────
db-migrate: ## Esegui le migrazioni Alembic (vivono nel gateway)
	cd services/gateway && $(call py_for,gateway) -m alembic upgrade head

db-shell: ## Connettiti al DB locale
	docker compose exec postgres psql -U vd_user -d verumdoc_dev

db-reset: ## Droppa e ricrea il DB locale (DISTRUGGE I DATI)
	docker compose exec postgres psql -U vd_user -c "DROP DATABASE IF EXISTS verumdoc_dev;"
	docker compose exec postgres psql -U vd_user -c "CREATE DATABASE verumdoc_dev;"

# ── Utilities ──────────────────────────────────────────────────────
clean: ## Pulisci cache e file temporanei
	find . -type d -name __pycache__ -not -path "*/node_modules/*" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -not -path "*/node_modules/*" -delete

install: ## Installa ogni servizio nel proprio ambiente (alias di venv-all)
	@$(MAKE) --no-print-directory venv-all

