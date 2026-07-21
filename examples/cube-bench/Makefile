# Makefile for cube-bench

BINARY  := bin/cube-bench
GOFLAGS ?=

<<<<<<< HEAD
.PHONY: build clean fmt help
=======
.PHONY: build clean help
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)

## build: compile the benchmark binary (default)
build: $(BINARY)

$(BINARY):
	@mkdir -p bin
	go build $(GOFLAGS) -o $(BINARY) .
	@echo "Built: $(BINARY)"

<<<<<<< HEAD
## fmt: format Go sources
fmt:
	go fmt ./...

=======
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
## clean: remove compiled binary
clean:
	rm -rf bin

## help: list available targets
help:
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/^## /  /'

.DEFAULT_GOAL := build
