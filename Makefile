.PHONY: setup seed run day traps evals reflect web help

help:
	@echo "Targets:"
	@echo "  make setup   - uv sync + remind to copy .env"
	@echo "  make seed    - (re)create app.db with accounts, billing history, ledger"
	@echo "  make run     - one traced agent turn (MESSAGE='...')"
	@echo "  make day     - run the 6 clean scripted scenarios (generates traces)"
	@echo "  make traps   - run the 2 trap scenarios (generates failures)"
	@echo "  make evals   - LLM-as-a-Judge evals over recent traces -> Phoenix + ledger"
	@echo "  make reflect - reflection agent: query evals via Phoenix MCP, file proposals"
	@echo "  make web     - launch the dashboard at http://localhost:8080"

setup:
	uv sync
	@test -f .env || echo "Tip: copy .env.example to .env and add keys."

seed:
	uv run python -m earned_autonomy.seed.data

run:
	uv run python run.py "$(if $(MESSAGE),$(MESSAGE),I was double-charged this month — please refund my last payment. My email is dana@brightloop.io)"

day:
	uv run python -m earned_autonomy.seed.scenarios clean

traps:
	uv run python -m earned_autonomy.seed.scenarios traps

evals:
	uv run python -m earned_autonomy.evals.runner

reflect:
	uv run python -m earned_autonomy.reflection.run

web:
	uv run uvicorn earned_autonomy.web.main:app --host 0.0.0.0 --port 8080
