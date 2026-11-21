FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc make xorriso qemu-system-x86 ovmf python3 python3-pip python3-venv \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY . /src
RUN python3 -m pip install --break-system-packages -e ".[dev]"

# `make iso` produces build/evoos.iso and artifacts/evoos.iso
# `make test-qemu` boots it headless and checks the serial log.
CMD ["make", "iso"]
