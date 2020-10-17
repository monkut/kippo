# kippo README

*kippo* is intended to be a light-weight project tracker.

## Installation

1. Install python 3.5.X

2. clone project from github
    https://github.com/monkut/kippo.git

3. Create the virtualenv and install the requirements:

    > Note:
    > This will use the 'pipenv' created and added in the virtual environment

    ```
    $ pipenv install
    ```

## Local Development

Prerequisites:

- [docker](https://store.docker.com/search?type=edition&offering=community)
- [pgcli](https://www.pgcli.com/) (for local db creation)
- [python 3.6](https://www.python.org/downloads/release/python-365/)
- [pipenv](https://docs.pipenv.org/)

1. Install development requirements:

    ```
    pipenv install --dev

    # enter environment
    pipenv shell
    ```
    
2. Setup `pre-commit` hooks (_black_, _isort_):

    ```bash
    # assumes pre-commit is installed on system via: `pip install pre-commit`
    pre-commit install
    ```
    
    
3. Prepare the local settings:

    > The settings directory contains the `base.py` file, this file is intended to be imported by 
    > the appropriate settings file (local.py, production.py, etc)

    ```
    # kippo/settings/local.py:
    from .base import *  # noqa: F401
    
    STATIC_URL = '/static/'
    
    DEBUG = True
    
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': 'kippo',
            'HOST': '127.0.0.1',
            'PORT': 5432,
            'USER': 'postgres',
            'PASSWORD': 'mysecretpassword',
        }
    }    
    ```   

4. Set `local.py` as the settings file to use:

    ```
    export DJANGO_SETTINGS_MODULE=kippo.settings.local
    ```

5. Setup database:

    ```
    # From the repository root run the following
    docker run --name postgres -e POSTGRES_PASSWORD=mysecretpassword -e POSTGRES_USER=postgres -p 5432:5432 -d postgres
    
    # create the database in the container
    pgcli -h localhost -p 5432 -U postgres -W
    
    # Create the database (make sure it matches the name defined in your kippo.settings.local configuration)
    > CREATE DATABASE kippo;
    > \q
    
    # Make migrations and migrate (create tables in the database)
    cd kippo
    python manage.py makemigrations
    python manage.py migrate
    
    # Load initial fixtures
    python manage.py loaddata default_columnset
    python manage.py loaddata default_labelset
    
    # Create management users
    python manage.py loaddata required_bot_users
    
    # load country holidays
    python manage.py loaddata jp_vn_publicholidays.json
    ```

## Optional Features

### ProjectId Mapping file output

Optionally, the environment variable, `PROJECTID_MAPPING_JSON_S3URI` may be defined to periodically write the *Active* 
ProjectIds to Project names in the following json format:

```json
{
    "last_updated": "2020-10-01T01:10:00+9:00",
    "{KippoProject.id (uuid)}":  "{KippoProject.name}"
}
```

> NOTE: appropriate permissions need to be applied to the related kippo execution role

To enable this feature the envar must be defined and related Cloudwatch event set to fire the following handler periodically (daily expected):

`projects.handlers.functions.handle_write_projectid_mapping_event` 