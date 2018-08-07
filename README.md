# kippo README

*kippo* is intended to be a light-weight project tracker with a focus on understanding the state of deliverying a project.


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

- docker
- python 3.6 with pipenv
- pgcli (install via pip install pgcli)

1. Install development requirements:

    ```
    pipenv install --dev

    # enter environment
    pipenv shell
    ```

2. Start local database:
    ```
    # start container
    docker run --name kippo-postgis -p 5432:5432 -e POSTGRES_PASSWORD=mysecretpassword -d mdillon/postgis
    
    # connect to container and create database 
    pgcli -h localhost -p 5432 -U postgres -W
    
    # WHERE 
    # > Password used is the password defined on `docker run`
 
    # From the pgcli prompt Create the database
    CREATE DATABASE kippo_local_development;
    
    # Connect to the database and add the postgis extension
    \c kippo_local_development
    CREATE EXTENSION postgis;
    
    # Quit 
    \q 
    ```

3. Add tables to database and load initial data
    ```
    # from the repository root/kippo directory 
    python manage.py migrate
    ... (Tables will be created)
    
    python manage.py loaddata initial_data
    ```
    
    
Your ready to develop!
    