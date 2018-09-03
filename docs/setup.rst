
.. _initial-setup::

===============================================
Initial Setup
===============================================

This describes the steps needed for initial setup of a project.


Basic Setup
=============================


1. Create an organization :guilabel:`Projects -> KippoProject`

    - Add Organization Name (For display only)
    - Add Github orginization name (Name used in github)
    - `Add personal access token <https://help.github.com/articles/creating-a-personal-access-token-for-the-command-line/>`_
    - Add valid email domains (All sub-domains used for user emails for validation)

Load Existing Github Projects
===============================

Existing github organizational projects can be loaded into the system with the following command::

    # Locally (for testing)
    python manage.py collect_github_organizational_projects --github-organization-name ${GITHUB_ORGANIZATION_NAME}

    # Deployed with zappa
    zappa manage prod "collect_github_organization_projects --github-organization-name ${GITHUB_ORGANIZATION_NAME}"


Register Users
==============================

The kippo system requires that Task assignees are registered as KippoUser's.
This is done in order to allow mapping of PersonalHoliday objects to a specific assignee for more accurate project schedule calculation.


Collect Project Updates
==============================

Once projects with github project links are created, kippo can collect the project data with the following command.
When this command is run related Repository and task information is collected and registered in kippo.

.. note::

    Only *1* task update can be created per day, if already created, it the task status will *NOT* be updated.

Manually update information from registered github organiational projects::

    # Locally (for testing)
    python manage.py update_github_project_tasks --github-organization-name ${GITHUB_ORGANIZATION_NAME}

    # Deployed with zappa
    zappa manage prod "update_github_project_tasks --github-organization-name ${GITHUB_ORGANIZATION_NAME}"