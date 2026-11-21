PYTHON ?= python3
SUPERVISOR ?= evolved

.PHONY: iso host test test-diff test-qemu test-persist clean

iso:
	$(MAKE) -C native iso SUPERVISOR=$(SUPERVISOR)

host:
	$(MAKE) -C native host

test:
	$(PYTHON) -m pytest
	$(MAKE) -C native test-diff SUPERVISOR=$(SUPERVISOR)

test-diff:
	$(MAKE) -C native test-diff SUPERVISOR=$(SUPERVISOR)

test-qemu:
	$(MAKE) -C native test-qemu SUPERVISOR=$(SUPERVISOR)

test-persist:
	$(MAKE) -C native test-persist

clean:
	$(MAKE) -C native clean
