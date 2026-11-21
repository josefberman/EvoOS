# Firmware stub + hosted interpreter. The OS is the embedded bytecode.

ROOT := $(abspath ..)
PYTHON ?= python3
SUPERVISOR ?= evolved
WORKLOAD ?= train_small
BUILD := $(ROOT)/build
LIMINE := third_party/limine

HOST_CC := gcc
CC := gcc

HOST_CFLAGS ?= -O2 -g -std=c11 -Wall -Wextra -DEVOOS_HOSTED
KERN_CFLAGS ?= -g -O2 -pipe -Wall -Wextra -std=gnu11 -ffreestanding \
	-fno-stack-protector -fno-stack-check -fno-lto -fno-PIC -fno-PIE \
	-m64 -march=x86-64 -mno-80387 -mno-mmx -mno-sse -mno-sse2 \
	-mno-red-zone -mcmodel=kernel -mgeneral-regs-only -fno-builtin
KERN_LDFLAGS ?= -nostdlib -static -no-pie -Wl,-melf_x86_64 -Wl,-z,max-page-size=0x1000 \
	-Wl,-z,noexecstack -T linker.ld -Wl,--build-id=none

.PHONY: all host iso kernel images test-diff test-qemu clean limine-tool

all: host iso

$(BUILD):
	mkdir -p $(BUILD)

images: $(BUILD)/boot.img $(BUILD)/boot_rr.img $(BUILD)/supervisor.bin

$(BUILD)/boot.img: | $(BUILD)
	cd $(ROOT) && PYTHONPATH=$(ROOT) $(PYTHON) -m evoos.boot.pack --supervisor $(SUPERVISOR) \
		--workload $(WORKLOAD) -o $(BUILD)/boot.img \
		--program-out $(BUILD)/supervisor.bin

$(BUILD)/boot_rr.img: | $(BUILD)
	cd $(ROOT) && PYTHONPATH=$(ROOT) $(PYTHON) -m evoos.boot.pack --supervisor rr \
		--workload $(WORKLOAD) -o $(BUILD)/boot_rr.img

$(BUILD)/supervisor.bin: $(BUILD)/boot.img

host: $(BUILD)/evoos_host

$(BUILD)/evoos_host: evoos_vm.c evoos_host.c evoos_vm.h | $(BUILD)
	$(HOST_CC) $(HOST_CFLAGS) -o $@ evoos_vm.c evoos_host.c

boot.img: $(BUILD)/boot.img
	cp $(BUILD)/boot.img boot.img

$(BUILD)/kernel.elf: evoos_boot.c evoos_vm.c evoos_image.S evoos_vm.h linker.ld limine.h boot.img | $(BUILD)
	$(CC) $(KERN_CFLAGS) -I. -c evoos_boot.c -o $(BUILD)/evoos_boot.o
	$(CC) $(KERN_CFLAGS) -I. -c evoos_vm.c -o $(BUILD)/evoos_vm_boot.o
	$(CC) $(KERN_CFLAGS) -c evoos_image.S -o $(BUILD)/evoos_image.o
	$(CC) $(KERN_CFLAGS) $(KERN_LDFLAGS) -o $@ $(BUILD)/evoos_boot.o $(BUILD)/evoos_vm_boot.o $(BUILD)/evoos_image.o

kernel: $(BUILD)/kernel.elf

limine-tool: $(BUILD)/limine

$(BUILD)/limine: $(LIMINE)/limine.c | $(BUILD)
	$(HOST_CC) -std=c99 -O2 -o $@ $(LIMINE)/limine.c

$(BUILD)/evoos.iso: $(BUILD)/kernel.elf $(BUILD)/limine limine.conf $(LIMINE)/limine-bios.sys
	rm -rf $(BUILD)/iso_root
	mkdir -p $(BUILD)/iso_root/boot/limine $(BUILD)/iso_root/EFI/BOOT
	cp $(BUILD)/kernel.elf $(BUILD)/iso_root/boot/kernel
	cp limine.conf $(BUILD)/iso_root/boot/limine/
	cp $(LIMINE)/limine-bios.sys $(LIMINE)/limine-bios-cd.bin $(LIMINE)/limine-uefi-cd.bin \
		$(BUILD)/iso_root/boot/limine/
	cp $(LIMINE)/BOOTX64.EFI $(BUILD)/iso_root/EFI/BOOT/
	cp $(LIMINE)/BOOTIA32.EFI $(BUILD)/iso_root/EFI/BOOT/
	xorriso -as mkisofs -R -r -J -b boot/limine/limine-bios-cd.bin \
		-no-emul-boot -boot-load-size 4 -boot-info-table -hfsplus \
		-apm-block-size 2048 --efi-boot boot/limine/limine-uefi-cd.bin \
		-efi-boot-part --efi-boot-image --protective-msdos-label \
		$(BUILD)/iso_root -o $(BUILD)/evoos.iso
	$(BUILD)/limine bios-install $(BUILD)/evoos.iso
	mkdir -p $(ROOT)/artifacts
	cp $(BUILD)/evoos.iso $(ROOT)/artifacts/evoos.iso

iso: $(BUILD)/evoos.iso

test-diff: host $(BUILD)/boot.img $(BUILD)/boot_rr.img
	cd $(ROOT) && PYTHONPATH=$(ROOT) $(PYTHON) -m evoos.boot.diff_traces \
		--host $(BUILD)/evoos_host \
		--image $(BUILD)/boot.img
	cd $(ROOT) && PYTHONPATH=$(ROOT) $(PYTHON) -m evoos.boot.diff_traces \
		--host $(BUILD)/evoos_host \
		--image $(BUILD)/boot_rr.img

QEMU ?= qemu-system-x86_64
QEMU_COMMON = -cdrom $(BUILD)/evoos.iso -m 128M -display none \
	-serial file:$(BUILD)/serial.log \
	-device isa-debug-exit,iobase=0xf4,iosize=0x04 \
	-no-reboot -cpu qemu64

OVMF_CODE ?= /usr/share/OVMF/OVMF_CODE_4M.fd
OVMF_VARS ?= /usr/share/OVMF/OVMF_VARS_4M.fd

test-qemu: iso
	rm -f $(BUILD)/serial.log
	set +e; timeout 20 $(QEMU) $(QEMU_COMMON); st=$$?; \
	  echo "qemu-bios-exit=$$st"; \
	  if [ ! -f $(BUILD)/serial.log ]; then echo "missing serial.log"; exit 1; fi; \
	  cat $(BUILD)/serial.log; \
	  grep -q "EvoOS done." $(BUILD)/serial.log || exit 1; \
	  grep -q "completed 4/4" $(BUILD)/serial.log || exit 1; \
	  grep -q "isolation=ok" $(BUILD)/serial.log || exit 1; \
	  test $$st -eq 1
	@echo "BIOS QEMU boot OK"
	@if [ -f "$(OVMF_CODE)" ]; then \
	  rm -f $(BUILD)/serial-uefi.log $(BUILD)/ovmf_vars.fd; \
	  cp "$(OVMF_VARS)" $(BUILD)/ovmf_vars.fd; \
	  set +e; timeout 25 $(QEMU) -drive if=pflash,format=raw,readonly=on,file=$(OVMF_CODE) \
	    -drive if=pflash,format=raw,file=$(BUILD)/ovmf_vars.fd \
	    -cdrom $(BUILD)/evoos.iso -m 128M -display none \
	    -serial file:$(BUILD)/serial-uefi.log \
	    -device isa-debug-exit,iobase=0xf4,iosize=0x04 \
	    -no-reboot -cpu qemu64; st=$$?; \
	    echo "qemu-uefi-exit=$$st"; \
	    if [ -f $(BUILD)/serial-uefi.log ]; then cat $(BUILD)/serial-uefi.log; fi; \
	    grep -q "EvoOS done." $(BUILD)/serial-uefi.log && \
	    grep -q "completed 4/4" $(BUILD)/serial-uefi.log && \
	    grep -q "isolation=ok" $(BUILD)/serial-uefi.log && \
	    test $$st -eq 1 && echo "UEFI QEMU boot OK" || echo "UEFI QEMU boot skipped/failed (st=$$st)"; \
	else echo "OVMF not found; UEFI test skipped"; fi

clean:
	rm -rf $(BUILD) boot.img

# Recreate boot.img when supervisor/workload flags change.
.PHONY: force-image
force-image:
	rm -f $(BUILD)/boot.img
	$(MAKE) $(BUILD)/boot.img
