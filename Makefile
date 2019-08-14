check:
	flake8 --max-line-length 165 --max-complexity 20 --ignore F403,F405,E252,W606 --exclude */migrations/*,*/tests.py,*/tests/* kippo/

pylint:
	pylint --rcfile .pylintrc kippo/

typecheck:
	mypy  kippo/ --disallow-untyped-defs --silent-imports

test:
	cd kippo && pipenv run python manage.py test && cd ..

coverage:
	cd kippo && pipenv run coverage run --source='.' manage.py test && cd ..

loadinitial:
	cd kippo && python manage.py loaddata default_columnset default_labelset required_bot_users && cd ..

pullrequestcheck: check coverage
