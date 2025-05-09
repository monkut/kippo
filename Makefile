## Run tests (without coverage)
test:
	cd kippo && uv run python manage.py test --debug-mode

## Define checks to run on PR
check:
	uv run ruff check

loadinitial:
	cd kippo && python manage.py loaddata default_columnset default_labelset required_bot_users && cd ..

pullrequestcheck: check

