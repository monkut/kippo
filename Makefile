check:
	flake8 --max-line-length 155 --max-complexity 22 --ignore F403,F405 --exclude */migrations/* kippo/

pylint:
	pylint --rcfile .pylintrc kippo/

typecheck:
	mypy  kippo/ --disallow-untyped-defs --silent-imports

test:
	cd kippo && python manage.py test && cd ..

coverage:
	cd kippo && coverage run --source='.' manage.py test && cd ..

loadinitial:
	cd kippo && python manage.py loaddata default_columnset default_labelset required_bot_users && cd ..

pullrequestcheck: check coverage
