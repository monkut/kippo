def get_all_subcommands() -> tuple:
    """Import and list all subcommands."""
    from accounts.slackcommand.subcommands.clockin import ClockInSubCommand
    from accounts.slackcommand.subcommands.clockout import ClockOutSubCommand
    from accounts.slackcommand.subcommands.setholiday import SetHolidaySubCommand
    from accounts.slackcommand.subcommands.listusers import ListUsersSubCommand
    from accounts.slackcommand.subcommands.breakstart import BreakStartSubCommand
    from accounts.slackcommand.subcommands.breakend import BreakEndSubCommand
    from accounts.slackcommand.subcommands.attendancecancel import AttendanceCancelSubCommand
    from projects.slackcommand.subcommands.projectstatus import ProjectStatusSubCommand
    from projects.slackcommand.subcommands.listprojectstatus import ListProjectStatusSubCommand
    from projects.slackcommand.subcommands.projecteffort import ProjectEffortSubCommand
    from projects.slackcommand.subcommands.listprojecteffort import ListProjectEffortSubCommand

    from .subcommands.listcommands import ListCommandsSubCommand

    return (
        # Add new commands here!
        ClockInSubCommand,
        ClockOutSubCommand,
        SetHolidaySubCommand,
        BreakStartSubCommand,
        BreakEndSubCommand,
        AttendanceCancelSubCommand,
        ListUsersSubCommand,
        ProjectStatusSubCommand,
        ListProjectStatusSubCommand,
        ListCommandsSubCommand,
        ProjectEffortSubCommand,
        ListProjectEffortSubCommand,
    )
