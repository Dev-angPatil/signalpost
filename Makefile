.PHONY: test cache cache-nav cache-pack

test:
	SERPER_API_KEY=test-placeholder SIGNALPOST_AI=0 SIGNALPOST_WIKIDATA=0 SIGNALPOST_CACHE=0 \
	HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 ALL_PROXY=http://127.0.0.1:9 \
	NO_PROXY= http_proxy=http://127.0.0.1:9 https_proxy=http://127.0.0.1:9 all_proxy=http://127.0.0.1:9 no_proxy= \
	PYTHONPATH=src:. uv run --offline --no-env-file --locked python -m unittest discover -s tests -p "test*.py"

# Declared caches (see docs/plan-declared-cache-and-jobs.md). Resumable; work files in data/cache-work/.
cache: cache-nav cache-pack

cache-nav:
	uv run --locked --no-env-file python scripts/cache/fetch_brreg_bulk.py
	uv run --locked --no-env-file python scripts/cache/build_nav_cache.py all

cache-pack:
	uv run --locked --no-env-file python scripts/cache/pack_cache.py
