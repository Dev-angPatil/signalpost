.PHONY: test

test:
	SERPER_API_KEY=test-placeholder \
	HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 ALL_PROXY=http://127.0.0.1:9 \
	NO_PROXY= http_proxy=http://127.0.0.1:9 https_proxy=http://127.0.0.1:9 all_proxy=http://127.0.0.1:9 no_proxy= \
	PYTHONPATH=src:. uv run --offline --no-env-file --locked python -m unittest discover -s tests -p "test*.py"
