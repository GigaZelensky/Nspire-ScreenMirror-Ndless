.DEFAULT_GOAL := all
PYTHON ?= python
SDKROOT ?= $(NDLESS_SDK)
SDK_OPTION = $(if $(strip $(SDKROOT)),--sdk "$(SDKROOT)",)
VERSION := $(shell $(PYTHON) -c "from pathlib import Path; print(Path('VERSION').read_text().strip())")

.PHONY: all receiver sdl pc check lint package
ifeq ($(OS),Windows_NT)
all: receiver pc
else
all: receiver
endif

sdl:
	$(PYTHON) build_sdl.py $(SDK_OPTION)

receiver: sdl
	$(PYTHON) build.py $(SDK_OPTION) --sdl-directory build/nsdl --output build/calculator

pc:
	$(PYTHON) build_display_setup.py
	$(PYTHON) tools/build_pc.py

check:
	$(PYTHON) tools/ci_support.py verify
	$(PYTHON) tools/run_checks.py $(SDK_OPTION)

lint:
	$(PYTHON) -m ruff check pc checks tools build.py build_sdl.py build_display_setup.py package.py
	$(PYTHON) -m ruff format --check pc checks tools build.py build_sdl.py build_display_setup.py package.py

package: all check
	$(PYTHON) package.py dist/ND-Screen-Mirror-$(VERSION)-windows --calculator-build build/calculator --pc-build build/pc/ND-Screen-Mirror --check-report build/checks/report.json
