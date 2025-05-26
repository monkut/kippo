def get_all_subcommands() -> tuple:
    """
    Import and list all subcommands.

    NOTE: using @lazy decorator to avoid circular import issues.
    """
    from accounts.slackcommand.subcommands.clockin import ClockInSubCommand
    from accounts.slackcommand.subcommands.clockout import ClockOutSubCommand
    from accounts.slackcommand.subcommands.setholiday import SetHolidaySubCommand
    from accounts.slackcommand.subcommands.listusers import ListUsersSubCommand
    from projects.slackcommand.subcommands.projectstatus import ProjectStatusSubCommand
    from projects.slackcommand.subcommands.listprojectstatus import ListProjectStatusSubCommand

    from .subcommands.listcommands import ListCommandsSubCommand

    return (
        # Add new commands here!
        ClockInSubCommand,
        ClockOutSubCommand,
        SetHolidaySubCommand,
        ListUsersSubCommand,
        ProjectStatusSubCommand,
        ListProjectStatusSubCommand,
        ListCommandsSubCommand,
    )
