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

Prequisites

- [docker](https://store.docker.com/search?type=edition&offering=community)
- [pgcli](https://www.pgcli.com/) (for local db creation)
- python 3.6
- [pipenv](https://docs.pipenv.org/)

1. Install development requirements:

    ```
    pipenv install --dev

    # enter environment
    pipenv shell
    ```

2. Setup database:

    ```
    # From the repository root run the following
    docker run --name kippo-postgres -e POSTGRES_PASSWORD=mysecretpassword -e POSTGRES_USER=postgres -p 5432:5432 -d postgres
    
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
    python manage.py loaddata initial_data
    ```
