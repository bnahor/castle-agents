.PHONY: install check lint types imports test sim sim-reply sim-multi chat

install:
	uv sync

check: lint types imports test

lint:
	uv run ruff format --check .
	uv run ruff check .

types:
	uv run pyright

imports:        ## layering, adapter independence, private internals
	uv run lint-imports

test:
	uv run pytest -q

sim:            ## serial vs parallel on the busy scenario (mock models, offline)
	uv run castle-agents sim busy

sim-reply:      ## same, posting answers as replies instead of in strict order
	uv run castle-agents sim busy --ordering reply

sim-multi:      ## three chats at once
	uv run castle-agents sim multi --quiet

chat:           ## interactive; add --provider claude once keys are in .env
	uv run castle-agents chat
