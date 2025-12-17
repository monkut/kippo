from commons.definitions import StringEnumWithChoices


class AssumptionCategories(StringEnumWithChoices):
    ASSUMPTION = "assumption"
    CONSTRAINT = "constraint"

    @classmethod
    def choices(cls) -> tuple[tuple[str, str], ...]:
        items = (
            (cls.ASSUMPTION.value, "前提条件"),
            (cls.CONSTRAINT.value, "制約事項"),
        )
        return items
