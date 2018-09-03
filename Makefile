check:
	flake8 --max-line-length 140 --max-complexity 18 --exclude */migrations/* kippo/

pylint:
	pylint --rcfile .pylintrc kippo/

typecheck:
	mypy  kippo/ --disallow-untyped-defs --silent-imports

test:
	cd kippo && python manage.py test && cd ..

coverage:
	cd kippo && coverage run --source='.' manage.py test && cd ..

loadinitial:
	cd kippo && python manage.py loaddata initial_data

pullrequestcheck: check coverage
