from commons.definitions import StringEnumWithChoices


class AttendanceRecordCategory(StringEnumWithChoices):
    START = "start"
    BREAK_START = "break_start"
    BREAK_END = "break_end"
    END = "end"
